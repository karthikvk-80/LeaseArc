from __future__ import annotations

import re
from typing import Any

from .schemas import AttributeSpec

SILENT_VALUES = {
    "lease is silent",
    "lease is silent.",
    "not found",
    "n/a",
    "none",
}


def normalize_and_validate(payload: dict[str, Any], attribute: AttributeSpec) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    payload.setdefault("attribute_key", attribute.attribute_key)
    payload.setdefault("display_name", attribute.display_name)
    payload.setdefault("category", attribute.category)
    payload.setdefault("enumerated_values", [])

    is_enum = bool(payload.get("is_enumerate", False))
    payload["is_enumerate"] = is_enum
    if is_enum:
        payload["single_value"] = None
        values = payload.get("enumerated_values")
        if not isinstance(values, list):
            values = []
            payload["enumerated_values"] = values
            errors.append("enumerated_values was not a list")
        payload["enumerate_count"] = len(values)
        for idx, item in enumerate(values):
            _normalize_value(item, attribute, errors, index=idx)
            item["index"] = idx
            item.setdefault("label", f"{attribute.display_name} {idx + 1}")
    else:
        payload["enumerate_count"] = 0
        payload["enumerated_values"] = []
        if not isinstance(payload.get("single_value"), dict):
            payload["single_value"] = _empty_single(attribute)
            errors.append("single_value missing or invalid")
        _normalize_value(payload["single_value"], attribute, errors, index=None)
    return payload, errors


def assess_quality(
    payload: dict[str, Any],
    attribute: AttributeSpec,
    retrieved: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    values = (
        payload.get("enumerated_values", [])
        if payload.get("is_enumerate")
        else [payload.get("single_value") or {}]
    )
    context_pages = {
        (
            str(item.get("block", {}).get("file_name") or ""),
            _as_page_number(item.get("block", {}).get("page_number")),
        ): str(item.get("block", {}).get("text") or "")
        for item in retrieved
    }

    for index, value in enumerate(values):
        fields = value.get("fields") if isinstance(value.get("fields"), dict) else {}
        is_empty = _fields_are_empty(fields)
        value_label = f"item {index + 1}" if payload.get("is_enumerate") else "single value"

        if is_empty:
            original_score = int(value.get("confidence_score") or 0)
            if original_score > 25:
                value["confidence_score"] = 25
                value["confidence_level"] = "low"
                warnings.append(
                    _warning(
                        "null_high_confidence",
                        f"{value_label} was null or silent with confidence {original_score}; confidence was reduced to 25.",
                    )
                )
            warnings.append(
                _warning(
                    "profiler_present_but_not_found",
                    f"Profiler supplied pages for {attribute.display_name}, but {value_label} returned null or Lease is silent.",
                )
            )
            continue

        source = value.get("source") if isinstance(value.get("source"), dict) else {}
        document_id = str(source.get("document_id") or "")
        file_name = str(source.get("file_name") or "")
        document_type = str(source.get("document_type") or "")
        page_number = _as_page_number(source.get("page_number"))
        source_clause = str(source.get("source_clause") or "").strip()

        if not document_id or not file_name or not document_type or page_number is None or not source_clause:
            warnings.append(
                _warning(
                    "missing_source",
                    f"{value_label} is non-null but does not include complete document, file, page, and source-clause metadata.",
                )
            )
            continue

        page_text = context_pages.get((file_name, page_number))
        if page_text is None:
            warnings.append(
                _warning(
                    "source_outside_context",
                    f"{value_label} cites {file_name} page {page_number}, which was not in the profiler context.",
                )
            )
            continue

        if _normalize_evidence(source_clause) not in _normalize_evidence(page_text):
            warnings.append(
                _warning(
                    "source_quote_not_verbatim",
                    f"{value_label} source clause was not found as one contiguous passage on the cited page.",
                )
            )
        if len(source_clause) > 500:
            warnings.append(
                _warning(
                    "source_quote_too_long",
                    f"{value_label} source clause is {len(source_clause)} characters; use a shorter supporting passage.",
                )
            )

        if attribute.display_name == "Total building area":
            _check_area_basis(fields, source_clause, warnings)

    profiler_count = _profiler_enumerate_count(retrieved)
    if payload.get("is_enumerate"):
        extracted_count = len(values)
    else:
        single_fields = values[0].get("fields", {}) if values else {}
        extracted_count = 0 if _fields_are_empty(single_fields) else 1
    if profiler_count is not None and profiler_count != extracted_count:
        warnings.append(
            _warning(
                "profiler_cardinality_mismatch",
                f"Profiler signaled {profiler_count} item(s), while extraction returned {extracted_count}. Review roles and evidence; the profiler count is advisory.",
            )
        )

    payload["quality_warnings"] = warnings
    return warnings


def _normalize_value(value: dict[str, Any], attribute: AttributeSpec, errors: list[str], index: int | None) -> None:
    fields = value.get("fields")
    if not isinstance(fields, dict):
        fields = {}
        value["fields"] = fields
        errors.append(f"fields missing for {attribute.attribute_key}:{index}")
    for field in attribute.expected_fields:
        fields.setdefault(field, None)
    score = value.get("confidence_score", 0)
    try:
        score = max(0, min(100, int(round(float(score)))))
    except Exception:
        score = 0
    value["confidence_score"] = score
    value["confidence_level"] = value.get("confidence_level") or (
        "high" if score >= 80 else "medium" if score >= 50 else "low"
    )
    confidence_reason = value.get("confidence_reason")
    value["confidence_reason"] = (
        confidence_reason.strip()
        if isinstance(confidence_reason, str) and confidence_reason.strip()
        else None
    )
    source = value.get("source")
    if not isinstance(source, dict):
        source = {}
        value["source"] = source
    for key in ("document_id", "file_name", "document_type", "page_number", "source_clause"):
        source.setdefault(key, None)
    if not isinstance(value.get("trace"), list):
        value["trace"] = []


def _fields_are_empty(fields: dict[str, Any]) -> bool:
    meaningful = [value for value in fields.values() if value not in (None, "")]
    if not meaningful:
        return True
    return all(str(value).strip().lower() in SILENT_VALUES for value in meaningful)


def _as_page_number(value: Any) -> int | None:
    try:
        page_number = int(value)
    except (TypeError, ValueError):
        return None
    return page_number if page_number > 0 else None


def _normalize_evidence(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _profiler_enumerate_count(retrieved: list[dict[str, Any]]) -> int | None:
    counts: list[int] = []
    for item in retrieved:
        signals = item.get("block", {}).get("profiler_signals")
        if not isinstance(signals, dict):
            continue
        for key, value in signals.items():
            normalized_key = str(key).lower()
            if "count" not in normalized_key and "enumerate" not in normalized_key:
                continue
            try:
                count = int(value)
            except (TypeError, ValueError):
                continue
            if count >= 0:
                counts.append(count)
    return max(counts) if counts else None


def _check_area_basis(
    fields: dict[str, Any],
    source_clause: str,
    warnings: list[dict[str, Any]],
) -> None:
    source = source_clause.lower()
    result = " ".join(str(value) for value in fields.values() if value not in (None, "")).lower()
    area_bases = {
        "built-up": ("built-up", "built up", "builtup"),
        "usable": ("usable", "useable"),
        "leasable": ("leasable",),
        "rentable": ("rentable",),
        "carpet": ("carpet",),
    }
    for label, terms in area_bases.items():
        if any(term in source for term in terms) and not any(term in result for term in terms):
            warnings.append(
                _warning(
                    "area_basis_missing",
                    f"Total building area evidence describes {label} area, but the extracted value does not preserve that basis.",
                )
            )
            return


def _warning(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "severity": "warning", "message": message}


def _empty_single(attribute: AttributeSpec) -> dict[str, Any]:
    return {
        "fields": {field: None for field in attribute.expected_fields},
        "confidence_score": 0,
        "confidence_level": "low",
        "confidence_reason": None,
        "source": {
            "document_id": None,
            "file_name": None,
            "document_type": None,
            "page_number": None,
            "source_clause": None,
        },
        "trace": [],
    }

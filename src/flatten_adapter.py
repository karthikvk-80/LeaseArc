from __future__ import annotations

import re
import uuid
from typing import Any


PARTY_OUTPUT_FIELDS = {"Value", "Role"}


def slugify(text: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower())).strip("_")


def schema_display_name(text: str) -> str:
    for prefix in ("Lease_Abstraction.", "Clauses."):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def flatten_attribute(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("is_enumerate"):
        return _flatten_enumerated(payload)
    return [_flatten_single(payload)]


def _flatten_single(payload: dict[str, Any]) -> dict[str, Any]:
    value_obj = payload["single_value"]
    fields = value_obj.get("fields", {})
    extracted_value = fields.get("Value")
    if extracted_value is None and fields:
        extracted_value = "; ".join(f"{k}: {v}" for k, v in fields.items() if v not in (None, ""))
    return _base_row(
        payload=payload,
        value_obj=value_obj,
        attribute_key=payload["attribute_key"],
        attribute_name=payload["display_name"],
        schema_path=f"Lease_catalyst.Lease_Abstraction.{schema_display_name(payload['display_name'])}",
        group=None,
        slot_index=None,
        sub_field=None,
        extracted_value=extracted_value,
    )


def _flatten_enumerated(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    allowed_fields = (
        PARTY_OUTPUT_FIELDS
        if payload.get("attribute_key") in {"landlord_name", "tenant_name"}
        else None
    )
    for item in payload.get("enumerated_values", []):
        idx = int(item.get("index", len(rows)))
        fields = item.get("fields", {})
        for field, value in fields.items():
            if allowed_fields is not None and field not in allowed_fields:
                continue
            rows.append(_base_row(
                payload=payload,
                value_obj=item,
                attribute_key=f"{payload['attribute_key']}_{idx}_{slugify(field)}",
                attribute_name=f"{payload['display_name']} {idx + 1} - {field}",
                schema_path=f"Lease_catalyst.Lease_Abstraction.{schema_display_name(payload['display_name'])}.{idx}.{field}",
                group=payload["display_name"],
                slot_index=idx,
                sub_field=field,
                extracted_value=value,
            ))
    return rows


def _base_row(
    *,
    payload: dict[str, Any],
    value_obj: dict[str, Any],
    attribute_key: str,
    attribute_name: str,
    schema_path: str,
    group: str | None,
    slot_index: int | None,
    sub_field: str | None,
    extracted_value: Any,
) -> dict[str, Any]:
    source = value_obj.get("source", {})
    return {
        "attribute_id": str(uuid.uuid4()),
        "attribute_key": attribute_key,
        "attribute_name": attribute_name,
        "display_name": attribute_name,
        "schema_path": schema_path,
        "category": payload.get("category"),
        "group": group,
        "slot_index": slot_index,
        "sub_field": sub_field,
        "data_type": "text",
        "extracted_value": None if extracted_value in ("", "Not found", "N/A") else extracted_value,
        "user_edited_value": None,
        "confidence_score": value_obj.get("confidence_score", 0),
        "confidence_level": value_obj.get("confidence_level", "low"),
        "confidence_reason": value_obj.get("confidence_reason"),
        "is_verified": False,
        "verified_by": None,
        "verified_at": None,
        "source_document_id": source.get("document_id"),
        "source_file_name": source.get("file_name"),
        "source_document_type": source.get("document_type"),
        "page_number": source.get("page_number"),
        "source_clause": source.get("source_clause"),
        "translation": None,
        "bbox": None,
        "bbox_rects": None,
        "is_enumerate": bool(payload.get("is_enumerate")),
        "enumerate_count": payload.get("enumerate_count", 0),
        "trace": value_obj.get("trace", []),
    }

from __future__ import annotations

import re
import uuid
from typing import Any


def slugify(text: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower())).strip("_")


def flatten_attribute(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("is_enumerate"):
        return _flatten_enumerated(payload)
    flattened = _flatten_single(payload)
    return flattened if isinstance(flattened, list) else [flattened]


def _flatten_single(payload: dict[str, Any]) -> dict[str, Any] | list[dict[str, Any]]:
    value_obj = payload["single_value"]
    fields = value_obj.get("fields", {})
    if set(fields) != {"Value"}:
        return _flatten_structured_single(payload, value_obj, fields)
    extracted_value = fields.get("Value")
    if extracted_value is None and fields:
        extracted_value = "; ".join(f"{k}: {v}" for k, v in fields.items() if v not in (None, ""))
    return _base_row(
        payload=payload,
        value_obj=value_obj,
        attribute_key=payload["attribute_key"],
        attribute_name=payload["display_name"],
        group=None,
        slot_index=None,
        sub_field=None,
        extracted_value=extracted_value,
    )


def _flatten_structured_single(
    payload: dict[str, Any],
    value_obj: dict[str, Any],
    fields: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        _base_row(
            payload=payload,
            value_obj=value_obj,
            attribute_key=_field_attribute_key(payload["attribute_key"], field),
            attribute_name=field,
            group=payload["display_name"],
            slot_index=0,
            sub_field=field,
            extracted_value=value,
        )
        for field, value in fields.items()
    ]


def _flatten_enumerated(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in payload.get("enumerated_values", []):
        idx = int(item.get("index", len(rows)))
        fields = item.get("fields", {})
        for field, value in fields.items():
            rows.append(_base_row(
                payload=payload,
                value_obj=item,
                attribute_key=(
                    _field_attribute_key(payload["attribute_key"], field)
                    if len(payload.get("enumerated_values", [])) == 1
                    else f"{payload['attribute_key']}_{idx}_{slugify(field)}"
                ),
                attribute_name=(
                    field
                    if len(payload.get("enumerated_values", [])) == 1
                    else f"{payload['display_name']} {idx + 1} - {field}"
                ),
                group=payload["display_name"],
                slot_index=idx,
                sub_field=field,
                extracted_value=value,
            ))
    return rows


def _field_attribute_key(parent_key: str, field: str) -> str:
    field_key = slugify(field)
    if field_key == "value":
        return parent_key
    aliases = {
        ("area", "unit_suite_number"): "unit_suite_number",
        ("area", "type"): "lease_type",
        ("area", "gross_area"): "gross_area",
        ("area", "gross_area_uom"): "gross_area_uom",
        ("area", "net_area"): "net_area",
        ("area", "net_area_uom"): "net_area_uom",
        ("area", "floor_no"): "floor_no",
        ("expenses", "monthly_amount"): "base_rent_monthly",
        ("expenses", "annual_amount"): "base_rent_annual",
        ("expenses", "monthly_amount_per_sf"): "monthly_amount_per_sf",
        ("expenses", "annual_amount_per_sf"): "annual_amount_per_sf",
        ("expenses", "currency"): "currency",
        ("expenses", "payment_frequency"): "payment_frequency",
        ("security_deposit", "security_deposit_amount"): "security_deposit",
    }
    return aliases.get((parent_key, field_key), f"{parent_key}_{field_key}")


def _base_row(
    *,
    payload: dict[str, Any],
    value_obj: dict[str, Any],
    attribute_key: str,
    attribute_name: str,
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
        "category": payload.get("category"),
        "group": group,
        "slot_index": slot_index,
        "sub_field": sub_field,
        "data_type": _data_type(attribute_key, attribute_name, extracted_value),
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
        "translation": source.get("translation") or value_obj.get("translation"),
        "bbox": source.get("bbox") or value_obj.get("bbox"),
        "bbox_rects": source.get("bbox_rects") or value_obj.get("bbox_rects"),
        "is_enumerate": bool(payload.get("is_enumerate")),
        "enumerate_count": payload.get("enumerate_count", 0),
        "trace": value_obj.get("trace", []),
    }


def _data_type(attribute_key: str, attribute_name: str, value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "number"
    name = f"{attribute_key} {attribute_name}".lower()
    if re.search(r"\b(date|commencement|expiration|expiry|deadline)\b", name):
        return "date"
    if re.search(r"\b(percent|percentage|rate|pct|increase amount)\b", name):
        return "percentage"
    if re.search(r"\b(amount|area|count|number|rent|deposit|per sf)\b", name):
        return "number"
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return "boolean"
    return "text"

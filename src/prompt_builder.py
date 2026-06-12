from __future__ import annotations

import json
from typing import Any

from .schemas import AttributeSpec


def format_context(retrieved: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in retrieved:
        b = item["block"]
        profiler_lines: list[str] = []
        if b.get("profiler_attribute_name"):
            profiler_lines.append(f"profiler_attribute={b.get('profiler_attribute_name')}")
        if b.get("profiler_definition"):
            profiler_lines.append(f"profiler_definition={b.get('profiler_definition')}")
        if b.get("profiler_might_be_present_under"):
            profiler_lines.append(f"profiler_sections={b.get('profiler_might_be_present_under')}")
        if b.get("profiler_signals"):
            profiler_lines.append(f"profiler_signals={b.get('profiler_signals')}")
        profiler_context = "\n".join(profiler_lines)
        parts.append(
            "["
            f"doc_id={b.get('document_id')} | "
            f"file={b.get('file_name')} | "
            f"type={b.get('document_type')} | "
            f"order={b.get('document_order')} | "
            f"date={b.get('document_date')} | "
            f"page={b.get('page_number')} | "
            f"chunk={b.get('chunk_id')}"
            "]\n"
            f"{profiler_context}\n"
            f"{b.get('text', '').strip()}"
        )
    return "\n\n---\n\n".join(parts)


def format_guidance(attribute: AttributeSpec) -> str:
    guidance = attribute.guidance or {}
    lines: list[str] = []
    label_map = {
        "hint": "Definition",
        "look_in": "Look in",
        "format": "Expected format",
        "example": "Example",
        "exclude": "Exclude",
        "value_style": "Value style",
    }
    for key, label in label_map.items():
        value = guidance.get(key)
        if value:
            lines.append(f"{label}: {value}")
    field_guidance = guidance.get("field_guidance") or []
    if field_guidance:
        lines.append("Field guidance:")
        lines.extend(f"- {item}" for item in field_guidance)
    special_rules = guidance.get("special_rules") or []
    if special_rules:
        lines.append("Special rules:")
        lines.extend(f"- {item}" for item in special_rules)
    generated_prefixes = (
        "Definition:",
        "Look in:",
        "Expected format:",
        "Example:",
        "Exclude:",
        "Value style:",
        "Field guidance:",
        "Special rule:",
    )
    additional_hints = [hint for hint in attribute.hints if not hint.startswith(generated_prefixes)]
    if additional_hints:
        lines.append("Additional hints:")
        lines.extend(f"- {hint}" for hint in additional_hints)
    return "\n".join(lines) if lines else "None"


def build_universal_prompt(attribute: AttributeSpec, retrieved: list[dict[str, Any]]) -> str:
    expected_fields = "\n".join(f"- {field}" for field in attribute.expected_fields)
    guidance = format_guidance(attribute)
    context = format_context(retrieved)
    schema = {
        "attribute_key": attribute.attribute_key,
        "display_name": attribute.display_name,
        "category": attribute.category,
        "is_enumerate": "boolean",
        "enumerate_count": "integer; 0 when is_enumerate=false",
        "single_value": {
            "fields": {"<expected field>": "string|null"},
            "confidence_score": "0-100",
            "confidence_level": "high|medium|low",
            "confidence_reason": "string|null",
            "source": {
                "document_id": "string|null",
                "file_name": "string|null",
                "document_type": "string|null",
                "page_number": "integer|null",
                "source_clause": "string|null",
                "translation": "English translation of source_clause when needed, else null",
                "bbox": "object|array|null",
                "bbox_rects": "array|null"
            },
            "trace": [
                {
                    "document_id": "string|null",
                    "file_name": "string|null",
                    "document_type": "string|null",
                    "page_number": "integer|null",
                    "effect": "current|added|superseded|replaced|removed|confirmed_unchanged|background|rejected|not_found",
                    "value_summary": "string",
                    "source_clause": "string|null",
                    "reason": "string"
                }
            ],
        },
        "enumerated_values": [
            {
                "index": "integer starting at 0",
                "label": "string",
                "fields": {"<expected field>": "string|null"},
                "confidence_score": "0-100",
                "confidence_level": "high|medium|low",
                "source": "same shape as single_value.source",
                "trace": "same shape as single_value.trace",
            }
        ],
    }
    return f"""
You are extracting one lease abstraction attribute from a lease package.

Documents are ordered oldest to newest by the `order` metadata.
Later amendments, renewals, assignments, side letters, or surrender letters may add, remove,
replace, confirm, or modify earlier values. Use only the provided context.

ATTRIBUTE
- key: {attribute.attribute_key}
- display_name: {attribute.display_name}
- category: {attribute.category}
- profiler_lookup_names: {attribute.profiler_lookup_names()}
- repeatable_hint: {attribute.repeatable_hint}

EXPECTED FIELDS
{expected_fields}

ATTRIBUTE GUIDANCE
{guidance}

TASK
Determine the final current abstraction result for this attribute.
Use the profiler metadata only as page-location guidance. Extract actual values only from the
provided page text.

CARDINALITY RULE
- If there is exactly one final current value/item, return is_enumerate=false and fill single_value.
- If there are multiple final current values/items, return is_enumerate=true, set enumerate_count,
  and fill enumerated_values.
- If repeatable_hint is true, actively look for multiple rows/items, but still return a single value
  if the text supports only one current item.
- If repeatable_hint is false, default to a single value unless the text clearly supports multiple
  simultaneous current values.
- Always put extracted data inside `fields`.
- For simple attributes, use fields.Value.
- For structured attributes, use the EXPECTED FIELDS exactly.
- Do not collapse multiple current values into one string.
- Historical or superseded values belong in trace, not enumerated_values.
- If no current value is found, return is_enumerate=false with all field values null and a not_found trace.

SOURCE RULE
Every single_value or enumerated item must include document_id, file_name, document_type,
page_number, and source_clause. The source_clause must be a short verbatim supporting quote from
one contiguous passage in the provided context. Do not join separate passages or rewrite the quote.
Include confidence_reason. When source_clause is not English, include an English translation.

TRACE RULE
Trace should explain what each relevant document contributed. Use these effects:
current, added, superseded, replaced, removed, confirmed_unchanged, background, rejected, not_found.

CONTEXT
{context}

Return JSON only. Match this schema shape:
{json.dumps(schema, indent=2)}
""".strip()

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


INDIVIDUAL_PROFILER_COLLECTION = "lease_ind_profiler"
COLLATED_PROFILER_COLLECTION = "lease_collated_profiler"
LEASE_RESULT_COLLECTION = "lease_result"

_NOT_FOUND_VALUES = {"", "lease is silent", "lease is silent.", "not found", "n/a", "none", "null"}


def new_lease_id() -> str:
    return str(uuid.uuid4())


def configured_lease_id() -> str | None:
    value = os.getenv("LEASE_ID")
    return value.strip() if value and value.strip() else None


def persist_individual_profiler(lease_id: str, document: dict[str, Any]) -> None:
    file_id = str(document.get("file_id") or document.get("artifact_id") or uuid.uuid4())
    payload = {"leaseId": lease_id, "fileId": file_id, **document}
    _replace_one(
        INDIVIDUAL_PROFILER_COLLECTION,
        {"leaseId": lease_id, "fileId": file_id},
        payload,
    )


def persist_collated_profiler(lease_id: str, profiler_output: dict[str, Any]) -> None:
    payload = {"leaseId": lease_id, **profiler_output}
    _replace_one(COLLATED_PROFILER_COLLECTION, {"leaseId": lease_id}, payload)


def persist_lease_result(result: dict[str, Any]) -> None:
    _replace_one(LEASE_RESULT_COLLECTION, {"leaseId": result["leaseId"]}, result)


def persist_pipeline_artifacts(
    *,
    lease_id: str,
    profiler_documents: list[dict[str, Any]],
    collated_profiler: dict[str, Any],
    final_abstraction: dict[str, Any],
) -> dict[str, Any]:
    for document in profiler_documents:
        persist_individual_profiler(lease_id, document)
    persist_collated_profiler(lease_id, collated_profiler)
    result = build_lease_result(
        final_abstraction,
        lease_id=lease_id,
        profiler_output=collated_profiler,
    )
    persist_lease_result(result)
    return result


def build_lease_result(
    final_abstraction: dict[str, Any],
    *,
    lease_id: str,
    profiler_output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profiler_output = profiler_output or {}
    attributes = _result_attributes(
        final_abstraction.get("attributes_universal", []),
        lease_id=lease_id,
    )
    extracted = [attribute for attribute in attributes if _has_value(attribute["extracted_value"])]
    confidence_scores = [
        int(attribute.get("confidence_score") or 0)
        for attribute in extracted
    ]
    documents = final_abstraction.get("documents", [])
    return {
        "leaseId": lease_id,
        "fileName": _primary_file_name(documents, profiler_output),
        "pageCount": sum(int(document.get("page_count") or 0) for document in documents),
        "extractedAt": _extracted_at(final_abstraction),
        "overallConfidence": (
            int(round(sum(confidence_scores) / len(confidence_scores)))
            if confidence_scores
            else 0
        ),
        "totalAttributes": len(attributes),
        "extractedCount": len(extracted),
        "lowConfidenceCount": sum(
            1 for attribute in attributes if attribute.get("confidence_level") == "low"
        ),
        "notFoundCount": len(attributes) - len(extracted),
        "hasDocument": False,
        "pdfType": _pdf_type(profiler_output),
        "attributes": attributes,
    }


def _result_attributes(
    universal_attributes: Any,
    *,
    lease_id: str,
) -> list[dict[str, Any]]:
    if not isinstance(universal_attributes, list):
        return []
    results: list[dict[str, Any]] = []
    for payload in universal_attributes:
        if not isinstance(payload, dict):
            continue
        values = (
            payload.get("enumerated_values", [])
            if payload.get("is_enumerate")
            else [payload.get("single_value") or {}]
        )
        for item_index, value_obj in enumerate(values):
            if not isinstance(value_obj, dict):
                continue
            fields = value_obj.get("fields")
            fields = fields if isinstance(fields, dict) else {}
            extracted_value = _extracted_value(fields)
            source = value_obj.get("source")
            source = source if isinstance(source, dict) else {}
            attribute_key = str(payload.get("attribute_key") or "")
            display_name = str(payload.get("display_name") or attribute_key)
            results.append(
                {
                    "attribute_id": str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"{lease_id}:{attribute_key}:{item_index}",
                        )
                    ),
                    "lease_id": lease_id,
                    "category": payload.get("category"),
                    "attribute_key": attribute_key,
                    "attribute_name": display_name,
                    "schema_path": _schema_path(display_name, item_index if payload.get("is_enumerate") else None),
                    "data_type": _data_type(attribute_key, display_name, extracted_value),
                    "extracted_value": extracted_value,
                    "user_edited_value": None,
                    "confidence_score": int(value_obj.get("confidence_score") or 0),
                    "confidence_level": value_obj.get("confidence_level") or "low",
                    "confidence_reason": value_obj.get("confidence_reason"),
                    "is_key_field": False,
                    "page_number": source.get("page_number"),
                    "source_clause": source.get("source_clause"),
                    "source_type": source.get("document_type"),
                    "source_file_name": source.get("file_name"),
                    "s3_file_path": None,
                    "translation": None,
                    "bbox": None,
                    "bbox_rects": None,
                    "is_verified": False,
                    "verified_by": None,
                    "verified_at": None,
                }
            )
    return results


def _extracted_value(fields: dict[str, Any]) -> Any:
    if set(fields) == {"Value"}:
        return _normalize_value(fields.get("Value"))
    return {
        key: _normalize_value(value)
        for key, value in fields.items()
    }


def _schema_display_name(text: str) -> str:
    for prefix in ("Lease_Abstraction.", "Clauses."):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def _schema_path(display_name: str, item_index: int | None) -> str:
    path = f"Lease_catalyst.Lease_Abstraction.{_schema_display_name(display_name)}"
    if item_index is not None:
        path = f"{path}.{item_index}"
    return path


def _normalize_value(value: Any) -> Any:
    if isinstance(value, str) and value.strip().lower() in _NOT_FOUND_VALUES:
        return None
    return value


def _has_value(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_has_value(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_value(item) for item in value)
    if value is None:
        return False
    return not (isinstance(value, str) and value.strip().lower() in _NOT_FOUND_VALUES)


def _data_type(attribute_key: str, display_name: str, value: Any) -> str:
    if isinstance(value, dict):
        return "object"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    name = f"{attribute_key} {display_name}".lower()
    if re.search(r"\b(date|commencement|expiration|expiry|deadline)\b", name):
        return "date"
    if re.search(r"\b(percent|percentage|rate|pct)\b", name):
        return "percentage"
    if re.search(r"\b(count|number|amount|area|rent|deposit|duration|day|month|year)\b", name):
        return "number"
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return "boolean"
    return "text"


def _primary_file_name(documents: Any, profiler_output: dict[str, Any]) -> str | None:
    relationships = profiler_output.get("document_relationships")
    if isinstance(relationships, dict) and relationships.get("base_document"):
        return str(relationships["base_document"])
    if isinstance(documents, list) and documents:
        base = next(
            (
                document
                for document in documents
                if str(document.get("document_type") or "").lower() in {"base", "document"}
            ),
            documents[0],
        )
        return base.get("file_name")
    return None


def _extracted_at(final_abstraction: dict[str, Any]) -> str:
    raw = final_abstraction.get("processed_at")
    if raw:
        return str(raw).replace("+00:00", "")
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _pdf_type(profiler_output: dict[str, Any]) -> str:
    formats = {
        str(document.get("data_format") or "").lower()
        for document in profiler_output.get("documents", [])
        if isinstance(document, dict)
    }
    if any("image" in value for value in formats):
        return "scanned"
    if formats:
        return "typed"
    return "typed"


def _replace_one(collection_name: str, query: dict[str, Any], payload: dict[str, Any]) -> None:
    try:
        from pymongo import MongoClient
    except ImportError as exc:
        raise RuntimeError(
            "pymongo is required for MongoDB persistence. Install project requirements."
        ) from exc

    client = MongoClient(_mongodb_dsn(), serverSelectionTimeoutMS=10_000)
    try:
        client[_mongodb_db()][collection_name].replace_one(query, payload, upsert=True)
    finally:
        client.close()


def _mongodb_dsn() -> str:
    config = _config_json()
    credentials = config.get("credentials", {}) if isinstance(config.get("credentials"), dict) else {}
    return str(
        os.getenv("MONGODB_DSN")
        or os.getenv("MONGO_URI")
        or credentials.get("MONGODB_DSN")
        or credentials.get("mongodb_dsn")
        or config.get("MONGODB_DSN")
        or config.get("mongodb_dsn")
        or "mongodb://localhost:27017"
    )


def _mongodb_db() -> str:
    config = _config_json()
    credentials = config.get("credentials", {}) if isinstance(config.get("credentials"), dict) else {}
    return str(
        os.getenv("MONGODB_DB")
        or os.getenv("MONGO_DB")
        or credentials.get("MONGODB_DB")
        or credentials.get("mongodb_db")
        or config.get("MONGODB_DB")
        or config.get("mongodb_db")
        or "lease"
    )


def _config_json() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "config.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}

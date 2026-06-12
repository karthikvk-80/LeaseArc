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
LEASE_MARKDOWN_COLLECTION = "lease_markdown"

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


def persist_markdown(
    *,
    lease_id: str,
    file_id: str,
    file_name: str,
    source_s3_path: str | None,
    markdown_text: str,
    page_count: int,
    pdf_type: str,
) -> None:
    payload = {
        "leaseId": lease_id,
        "fileId": file_id,
        "fileName": file_name,
        "sourceS3Path": source_s3_path,
        "markdownText": markdown_text,
        "pageCount": page_count,
        "pdfType": pdf_type,
        "createdAt": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }
    _replace_one(
        LEASE_MARKDOWN_COLLECTION,
        {"leaseId": lease_id, "fileId": file_id},
        payload,
    )


def load_markdown_documents(lease_id: str) -> list[dict[str, Any]]:
    documents = _find_many(
        LEASE_MARKDOWN_COLLECTION,
        {"leaseId": lease_id},
    )
    return sorted(
        documents,
        key=lambda document: (
            str(document.get("fileName") or "").lower(),
            str(document.get("fileId") or ""),
        ),
    )


def dump_lease_result_json(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def dump_final_result_files(output_dir: Path, result: dict[str, Any]) -> None:
    for file_name in (
        "lease_result.json",
        "final_abstraction_format.json",
        "expected_final_output.json",
    ):
        dump_lease_result_json(output_dir / file_name, result)


def dump_and_persist_lease_result(output_dir: Path, result: dict[str, Any]) -> None:
    dump_final_result_files(output_dir, result)
    persist_lease_result(result)


def persist_pipeline_artifacts(
    *,
    lease_id: str,
    profiler_documents: list[dict[str, Any]],
    collated_profiler: dict[str, Any],
    final_abstraction: dict[str, Any],
    result_path: Path,
) -> dict[str, Any]:
    result = build_lease_result(
        final_abstraction,
        lease_id=lease_id,
        profiler_output=collated_profiler,
    )
    dump_final_result_files(result_path.parent, result)
    for document in profiler_documents:
        persist_individual_profiler(lease_id, document)
    persist_collated_profiler(lease_id, collated_profiler)
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
        final_abstraction.get("attributes_flattened", []),
        lease_id=lease_id,
        documents=final_abstraction.get("documents", []),
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
        "hasDocument": bool(documents),
        "pdfType": _pdf_type(profiler_output),
        "attributes": attributes,
    }


def _result_attributes(
    flattened_attributes: Any,
    *,
    lease_id: str,
    documents: Any,
) -> list[dict[str, Any]]:
    if not isinstance(flattened_attributes, list):
        return []
    document_by_id = {
        str(document.get("document_id")): document
        for document in documents
        if isinstance(document, dict) and document.get("document_id")
    }
    document_by_name = {
        str(document.get("file_name")): document
        for document in documents
        if isinstance(document, dict) and document.get("file_name")
    }
    results: list[dict[str, Any]] = []
    for index, row in enumerate(flattened_attributes):
        if not isinstance(row, dict):
            continue
        source_filename = row.get("source_file_name")
        source_document = (
            document_by_id.get(str(row.get("source_document_id")))
            or document_by_name.get(str(source_filename))
            or {}
        )
        attribute_key = str(row.get("attribute_key") or "")
        attribute_name = str(
            row.get("display_name")
            or row.get("attribute_name")
            or attribute_key
        )
        extracted_value = _normalize_value(row.get("extracted_value"))
        has_value = _has_value(extracted_value)
        confidence_score = int(row.get("confidence_score") or 0) if has_value else 0
        confidence_level = (row.get("confidence_level") or "low") if has_value else "low"
        results.append(
            {
                "attribute_id": str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"{lease_id}:{attribute_key}:{index}",
                    )
                ),
                "lease_id": lease_id,
                "category": _result_category(
                    row.get("category"),
                    attribute_key,
                    attribute_name,
                ),
                "attribute_key": attribute_key,
                "attribute_name": attribute_name,
                "data_type": row.get("data_type")
                or _data_type(attribute_key, attribute_name, extracted_value),
                "extracted_value": extracted_value,
                "user_edited_value": None,
                "confidence_score": confidence_score,
                "confidence_level": confidence_level,
                "confidence_reason": row.get("confidence_reason") if has_value else None,
                "is_verified": False,
                "verified_by": None,
                "verified_at": None,
                "page_number": row.get("page_number"),
                "source_clause": row.get("source_clause"),
                "translation": row.get("translation"),
                "bbox": row.get("bbox"),
                "bbox_rects": row.get("bbox_rects"),
                "is_key_field": bool(row.get("is_key_field", False)),
                "source_filename": source_filename,
                "source_type": (
                    row.get("source_document_type")
                    or source_document.get("document_type")
                ),
                "source_s3_path": source_document.get("source_s3_path"),
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


def _result_category(
    category: Any,
    attribute_key: str,
    attribute_name: str,
) -> str | None:
    raw_category = str(category or "")
    if raw_category in {"Property", "Parties", "Dates", "Core Lease Terms"}:
        return "Core Lease Terms"
    if raw_category == "Financial Obligations":
        return raw_category
    if raw_category == "Options":
        return "Critical Dates"
    if raw_category != "Clauses":
        return raw_category or None
    name = f"{attribute_key} {attribute_name}".lower()
    cam_terms = (
        "operating_expense",
        "re_taxes",
        "property_insurance",
        "utilities",
        "parking",
        "signage",
        "repair",
        "maintenance",
        "hvac",
    )
    if any(term in name for term in cam_terms):
        return "CAM and Operating Expenses"
    return "Restrictive Clauses"


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


def _find_many(collection_name: str, query: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        from pymongo import MongoClient
    except ImportError as exc:
        raise RuntimeError(
            "pymongo is required for MongoDB persistence. Install project requirements."
        ) from exc

    client = MongoClient(_mongodb_dsn(), serverSelectionTimeoutMS=10_000)
    try:
        return list(client[_mongodb_db()][collection_name].find(query, {"_id": 0}))
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

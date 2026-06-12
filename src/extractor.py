from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import EXTRACTOR_MAX_WORKERS

from .attributes import ATTRIBUTE_SPECS, CONTACT_IDENTIFICATION_FIELDS
from .excel_exporter import export_final_abstraction_excel
from .flatten_adapter import flatten_attribute
from .llm_client import build_llm_client
from .mongo_persistence import build_lease_result, persist_lease_result
from .page_splitter import markdown_pages
from .prompt_builder import build_universal_prompt
from .schemas import DocumentMeta
from .validator import assess_quality, normalize_and_validate
from profiler import run_package as run_profiler_package

LOGGER = logging.getLogger(__name__)
_LLM_THREAD_LOCAL = threading.local()

PHASE_1_ATTRIBUTE_KEYS = {
    "property_name",
    "street",
    "street_no",
    "city",
    "state_province",
    "country",
    "building_type",
    "total_building_area",
    "uom",
    "landlord_name",
    "tenant_name",
    "effective_date",
    "execution_date",
    "original_commencement_date",
    "rent_commencement_date",
    "current_commencement_date",
    "current_expiration_date",
    "original_expiration_date",
    "possession_date",
    "delivery_date",
    "term_duration",
    "lease_status",
    "area",
    "security_deposit",
    "options",
}

_ANCHOR_EMPTY_VALUES = {"", "lease is silent", "lease is silent.", "not found", "n/a", "none", "null"}
_CONTACT_SOURCE_KEYS = {"landlord_name", "tenant_name"}
_PARTY_OUTPUT_FIELDS = {"Value", "Role"}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def retrieve_profiler_context(
    *,
    attribute,
    profiler_payload: dict[str, Any],
    documents: list[DocumentMeta],
    pages_by_document_id: dict[str, dict[int, str]],
) -> list[dict[str, Any]]:
    docs_by_name = {doc.file_name: doc for doc in documents}
    retrieved: list[dict[str, Any]] = []
    seen_pages: set[tuple[str, int, str]] = set()

    for profiler_doc in profiler_payload.get("documents", []):
        source = profiler_doc.get("source", {}) if isinstance(profiler_doc.get("source"), dict) else {}
        file_name = str(source.get("source_name") or "").strip()
        document = docs_by_name.get(file_name)
        if document is None:
            continue
        attribute_info = profiler_doc.get("attribute_info", {})
        if not isinstance(attribute_info, dict):
            continue

        matched_name = None
        info = None
        for name in attribute.profiler_lookup_names():
            candidate = attribute_info.get(name)
            if isinstance(candidate, dict):
                matched_name = name
                info = candidate
                break
        if not matched_name or not isinstance(info, dict):
            continue
        if not bool(info.get("is_attribute_present_file")):
            continue

        pages = pages_by_document_id.get(document.document_id, {})
        for page_number in _page_numbers(info.get("page_number_range")):
            page_text = pages.get(page_number)
            if not page_text:
                continue
            seen_key = (document.document_id, page_number, matched_name)
            if seen_key in seen_pages:
                continue
            seen_pages.add(seen_key)
            retrieved.append(
                {
                    "block": {
                        "chunk_id": f"profiler:{document.document_id}:{matched_name}:p{page_number}",
                        "document_id": document.document_id,
                        "file_name": document.file_name,
                        "document_type": document.document_type,
                        "document_order": document.document_order,
                        "document_date": document.document_date,
                        "page_number": page_number,
                        "chunk_index_on_page": 0,
                        "chunk_type": "profiler_page",
                        "text": page_text,
                        "context": "",
                        "profiler_attribute_name": matched_name,
                        "profiler_definition": info.get("definition"),
                        "profiler_might_be_present_under": info.get("might_be_present_under"),
                        "profiler_signals": _profiler_signals(info),
                    }
                }
            )

    return sorted(
        retrieved,
        key=lambda item: (
            int(item["block"].get("document_order", 0)),
            int(item["block"].get("page_number", 0)),
            str(item["block"].get("profiler_attribute_name") or ""),
        ),
    )


def retrieval_debug_payload(retrieved: list[dict[str, Any]]) -> list[dict[str, Any]]:
    debug: list[dict[str, Any]] = []
    for item in retrieved:
        block = dict(item.get("block", {}))
        block["text_preview"] = str(block.pop("text", ""))[:500]
        debug.append(block)
    return debug


def _has_anchor_value(fields: dict[str, Any]) -> bool:
    return any(str(value).strip().lower() not in _ANCHOR_EMPTY_VALUES for value in fields.values())


def _compact_anchor_fields(fields: dict[str, Any]) -> str:
    populated = [
        f"{key}: {value}"
        for key, value in fields.items()
        if str(value).strip().lower() not in _ANCHOR_EMPTY_VALUES
    ]
    return "; ".join(populated)


def _is_empty_value(value: Any) -> bool:
    return value is None or str(value).strip().lower() in _ANCHOR_EMPTY_VALUES


def _contact_type_for_payload(payload: dict[str, Any], fields: dict[str, Any]) -> str | None:
    explicit = fields.get("Contact Type")
    if not _is_empty_value(explicit):
        return str(explicit).strip()
    attribute_key = str(payload.get("attribute_key") or "")
    if attribute_key == "landlord_name":
        return "Landlord"
    if attribute_key == "tenant_name":
        return "Tenant"
    return None


def _contact_name(fields: dict[str, Any]) -> Any:
    name = fields.get("Name")
    if not _is_empty_value(name):
        return name
    return fields.get("Value")


def _contact_fields_from_party(payload: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    contact_fields = {field: fields.get(field) for field in CONTACT_IDENTIFICATION_FIELDS}
    contact_fields["Contact Type"] = _contact_type_for_payload(payload, fields)
    contact_fields["Name"] = _contact_name(fields)
    return contact_fields


def _contact_has_value(fields: dict[str, Any]) -> bool:
    return any(not _is_empty_value(value) for value in fields.values())


def build_contact_identification_payload(attributes_universal: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for payload in attributes_universal:
        if str(payload.get("attribute_key") or "") not in _CONTACT_SOURCE_KEYS:
            continue
        values = (
            payload.get("enumerated_values", [])
            if payload.get("is_enumerate")
            else [payload.get("single_value") or {}]
        )
        for value_obj in values:
            if not isinstance(value_obj, dict):
                continue
            fields = value_obj.get("fields")
            if not isinstance(fields, dict):
                continue
            contact_fields = _contact_fields_from_party(payload, fields)
            if not _contact_has_value(contact_fields):
                continue
            row_index = len(rows)
            source = value_obj.get("source") if isinstance(value_obj.get("source"), dict) else {}
            trace = value_obj.get("trace") if isinstance(value_obj.get("trace"), list) else []
            contact_type = contact_fields.get("Contact Type")
            name = contact_fields.get("Name")
            label_parts = [str(part).strip() for part in (contact_type, name) if not _is_empty_value(part)]
            rows.append(
                {
                    "index": row_index,
                    "label": " - ".join(label_parts) if label_parts else f"Contact Identification {row_index + 1}",
                    "fields": contact_fields,
                    "confidence_score": int(value_obj.get("confidence_score") or 0),
                    "confidence_level": value_obj.get("confidence_level") or "low",
                    "confidence_reason": value_obj.get("confidence_reason"),
                    "source": source,
                    "trace": [
                        *trace,
                        {
                            "document_id": source.get("document_id"),
                            "file_name": source.get("file_name"),
                            "document_type": source.get("document_type"),
                            "page_number": source.get("page_number"),
                            "effect": "confirmed_unchanged",
                            "value_summary": "Contact Identification row derived from extracted party/contact fields.",
                            "source_clause": source.get("source_clause"),
                            "reason": "Contact Identification is synthesized from Landlord Name and Tenant Name extraction rows to match the target schema without an additional LLM call.",
                        },
                    ],
                }
            )

    return {
        "attribute_key": "contact_identification",
        "display_name": "Contact Identification",
        "category": "Parties",
        "is_enumerate": bool(rows),
        "enumerate_count": len(rows) if rows else 0,
        "single_value": None if rows else {
            "fields": {field: None for field in CONTACT_IDENTIFICATION_FIELDS},
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
        },
        "enumerated_values": rows,
        "derived_from": ["landlord_name", "tenant_name"],
    }


def strip_party_contact_fields(attributes_universal: list[dict[str, Any]]) -> None:
    for payload in attributes_universal:
        if str(payload.get("attribute_key") or "") not in _CONTACT_SOURCE_KEYS:
            continue
        values = (
            payload.get("enumerated_values", [])
            if payload.get("is_enumerate")
            else [payload.get("single_value") or {}]
        )
        for value_obj in values:
            if not isinstance(value_obj, dict):
                continue
            fields = value_obj.get("fields")
            if not isinstance(fields, dict):
                continue
            value_obj["fields"] = {
                field: fields.get(field)
                for field in ("Value", "Role")
            }


def _build_phase1_anchor_facts(results: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for index in sorted(results):
        result = results[index]
        spec = result["spec"]
        if spec.attribute_key not in PHASE_1_ATTRIBUTE_KEYS:
            continue
        payload = result["payload"]
        values = payload.get("enumerated_values", []) if payload.get("is_enumerate") else [payload.get("single_value")]
        item_summaries: list[dict[str, Any]] = []
        for item in values:
            if not isinstance(item, dict):
                continue
            fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
            if not _has_anchor_value(fields):
                continue
            source = item.get("source") if isinstance(item.get("source"), dict) else {}
            item_summaries.append(
                {
                    "label": item.get("label"),
                    "fields": {key: value for key, value in fields.items() if str(value).strip().lower() not in _ANCHOR_EMPTY_VALUES},
                    "confidence_score": item.get("confidence_score"),
                    "confidence_level": item.get("confidence_level"),
                    "source_document_id": source.get("document_id"),
                    "source_file_name": source.get("file_name"),
                    "page_number": source.get("page_number"),
                }
            )
        if item_summaries:
            facts.append(
                {
                    "attribute_key": spec.attribute_key,
                    "display_name": spec.display_name,
                    "is_enumerate": bool(payload.get("is_enumerate")),
                    "items": item_summaries,
                }
            )
    return facts


def _format_phase1_anchor_context(anchor_facts: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for fact in anchor_facts:
        items = fact.get("items", [])
        if not isinstance(items, list) or not items:
            continue
        max_items = 5
        rendered_items: list[str] = []
        for item in items[:max_items]:
            fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
            text = _compact_anchor_fields(fields)
            if not text:
                continue
            page = item.get("page_number")
            confidence = item.get("confidence_level") or item.get("confidence_score")
            suffix_parts = []
            if confidence:
                suffix_parts.append(str(confidence))
            if page:
                suffix_parts.append(f"p{page}")
            suffix = f" ({', '.join(suffix_parts)})" if suffix_parts else ""
            rendered_items.append(f"{text}{suffix}")
        if len(items) > max_items:
            rendered_items.append(f"... {len(items) - max_items} more item(s)")
        if rendered_items:
            lines.append(f"- {fact.get('display_name')}: " + " | ".join(rendered_items))
    return "\n".join(lines)


def _page_numbers(raw_pages: Any) -> list[int]:
    if isinstance(raw_pages, int):
        return [raw_pages]
    if not isinstance(raw_pages, list):
        return []
    pages: list[int] = []
    for raw_page in raw_pages:
        try:
            page = int(raw_page)
        except Exception:
            continue
        if page > 0 and page not in pages:
            pages.append(page)
    return pages


def _profiler_signals(info: dict[str, Any]) -> dict[str, Any]:
    ignored = {"definition", "is_attribute_present_file", "page_number_range", "might_be_present_under"}
    return {key: value for key, value in info.items() if key not in ignored}


def _thread_llm_client(*, dry_run: bool):
    client = getattr(_LLM_THREAD_LOCAL, "client", None)
    if client is None:
        client = build_llm_client(dry_run=dry_run)
        _LLM_THREAD_LOCAL.client = client
    return client


def _extract_attribute_task(
    *,
    index: int,
    spec,
    retrieved: list[dict[str, Any]],
    prompt: str,
    intermediate_dir: Path,
    dry_run: bool,
) -> dict[str, Any]:
    llm = _thread_llm_client(dry_run=dry_run)
    try:
        LOGGER.info(
            "LLM extraction call started [%s/%s]: %s",
            index,
            len(ATTRIBUTE_SPECS),
            spec.display_name,
        )
        raw_payload = llm.complete_json(prompt)
        raw_path = intermediate_dir / "raw_llm" / f"{spec.attribute_key}.json"
        write_json(raw_path, raw_payload)
        LOGGER.info(
            "Raw LLM output saved [%s/%s]: %s",
            index,
            len(ATTRIBUTE_SPECS),
            raw_path,
        )
        payload, validation_errors = normalize_and_validate(raw_payload, spec)
    except Exception:
        failed_response = getattr(llm, "last_response_text", None)
        if failed_response:
            failed_dir = intermediate_dir / "raw_llm_failed"
            failed_dir.mkdir(exist_ok=True)
            failed_path = failed_dir / f"{spec.attribute_key}.txt"
            failed_path.write_text(str(failed_response), encoding="utf-8")
            LOGGER.error("Unparsed LLM response saved: %s", failed_path)
        LOGGER.exception("Extraction failed for attribute %s", spec.attribute_key)
        raise

    if validation_errors:
        message = f"Validation failed for attribute {spec.attribute_key}: {validation_errors}"
        LOGGER.error(message)
        raise ValueError(message)

    warnings = assess_quality(payload, spec, retrieved)
    flattened = flatten_attribute(payload)
    LOGGER.info(
        "Extraction completed [%s/%s]: %s -> %s flattened row(s)",
        index,
        len(ATTRIBUTE_SPECS),
        spec.display_name,
        len(flattened),
    )
    return {
        "index": index,
        "spec": spec,
        "payload": payload,
        "flattened": flattened,
        "warnings": warnings,
    }


def _run_parallel_extraction(
    extraction_tasks: list[dict[str, Any]],
    *,
    max_workers: int,
) -> dict[int, dict[str, Any]]:
    if not extraction_tasks:
        return {}
    worker_count = max(1, min(max_workers, len(extraction_tasks)))
    LOGGER.info(
        "Starting parallel LLM extraction: tasks=%s, max_workers=%s",
        len(extraction_tasks),
        worker_count,
    )
    completed_results: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="lease-extractor",
    ) as executor:
        future_to_task = {
            executor.submit(_extract_attribute_task, **task): task
            for task in extraction_tasks
        }
        try:
            for future in as_completed(future_to_task):
                result = future.result()
                completed_results[result["index"]] = result
        except Exception:
            for pending in future_to_task:
                pending.cancel()
            raise
    return completed_results


def run_abstraction(
    *,
    input_path: Path,
    output_dir: Path,
    dry_run: bool = False,
    lease_id: str | None = None,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    process_started_at = time.perf_counter()
    intermediate_dir = output_dir / "intermediate"
    markdown_dir = output_dir / "markdown"
    intermediate_dir.mkdir(parents=True, exist_ok=True)

    LOGGER.info("Profiler package run started from extractor: %s", input_path)
    profiler_result = run_profiler_package(
        input_path,
        markdown_output_dir=markdown_dir,
        lease_id=lease_id,
    )
    lease_id = str(profiler_result["lease_id"])
    profiler_seconds = time.perf_counter() - process_started_at
    extraction_started_at = time.perf_counter()
    profiler_output_path = Path(str(profiler_result["metadata_path"]))
    profiler_payload = json.loads(profiler_output_path.read_text(encoding="utf-8"))
    LOGGER.info(
        "Merged profiler result loaded from %s: %s documents, active extraction slice=%s attributes",
        profiler_output_path,
        len(profiler_payload.get("documents", [])),
        len(ATTRIBUTE_SPECS),
    )

    parsed_documents: list[DocumentMeta] = []
    pages_by_document_id: dict[str, dict[int, str]] = {}

    for index, parsed in enumerate(profiler_result.get("parsed_documents", []), start=1):
        doc = DocumentMeta(
            document_id=f"doc_{index:03d}",
            file_name=str(parsed["file_name"]),
            file_path=str(parsed["file_path"]),
            document_type=str(parsed["document_type"]),
            document_order=int(parsed["document_order"]),
            document_date=str(parsed.get("document_date") or "unknown"),
            markdown_path=str(parsed["markdown_path"]),
        )
        markdown = str(parsed.get("markdown_text") or "")
        if not markdown:
            markdown = Path(doc.markdown_path).read_text(encoding="utf-8")
        pages = markdown_pages(markdown)
        doc.page_count = len(pages)
        LOGGER.info(
            "Markdown page split [%s/%s]: %s pages for %s",
            index,
            len(profiler_result.get("parsed_documents", [])),
            len(pages),
            doc.file_name,
        )
        pages_by_document_id[doc.document_id] = {
            int(page["page_number"]): str(page.get("text") or "")
            for page in pages
            if page.get("page_number") is not None
        }
        parsed_documents.append(doc)

    write_json(intermediate_dir / "01_documents.json", [asdict(d) for d in parsed_documents])
    LOGGER.info("Parsed document manifest written: %s", intermediate_dir / "01_documents.json")
    write_json(intermediate_dir / "02_documents_parsed.json", [asdict(d) for d in parsed_documents])
    write_json(
        intermediate_dir / "03_pages.json",
        {
            doc_id: [{"page_number": page_no, "text_preview": text[:500]} for page_no, text in pages.items()]
            for doc_id, pages in pages_by_document_id.items()
        },
    )
    LOGGER.info("Parsed document metadata written: %s", intermediate_dir / "02_documents_parsed.json")
    LOGGER.info("Page preview debug written: %s", intermediate_dir / "03_pages.json")

    attributes_universal: list[dict[str, Any]] = []
    attributes_flattened: list[dict[str, Any]] = []
    retrieval_debug: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []
    quality_warnings: list[dict[str, Any]] = []
    prompt_dir = intermediate_dir / "prompts"
    prompt_dir.mkdir(exist_ok=True)

    def build_extraction_tasks(
        *,
        specs: list[tuple[int, Any]],
        phase: str,
        anchor_context: str | None = None,
    ) -> list[dict[str, Any]]:
        extraction_tasks: list[dict[str, Any]] = []
        for index, spec in specs:
            LOGGER.info(
                "Extraction task prep [%s/%s] %s: %s",
                index,
                len(ATTRIBUTE_SPECS),
                phase,
                spec.display_name,
            )
            retrieved = retrieve_profiler_context(
                attribute=spec,
                profiler_payload=profiler_payload,
                documents=parsed_documents,
                pages_by_document_id=pages_by_document_id,
            )
            retrieval_debug[spec.attribute_key] = retrieval_debug_payload(retrieved)
            if not retrieved:
                LOGGER.warning(
                    "Extraction skipped [%s/%s] %s: %s has no true profiler pages; lookup names=%s",
                    index,
                    len(ATTRIBUTE_SPECS),
                    phase,
                    spec.display_name,
                    spec.profiler_lookup_names(),
                )
                errors.append(
                    {
                        "attribute_key": spec.attribute_key,
                        "display_name": spec.display_name,
                        "phase": phase,
                        "message": "No true profiler pages found; skipped extraction.",
                        "profiler_lookup_names": spec.profiler_lookup_names(),
                    }
                )
                continue
            context_pages = [
                f"{item['block'].get('file_name')}#p{item['block'].get('page_number')}"
                for item in retrieved
            ]
            LOGGER.info(
                "Profiler context ready [%s/%s] %s: %s uses %s page blocks",
                index,
                len(ATTRIBUTE_SPECS),
                phase,
                spec.display_name,
                len(retrieved),
            )
            LOGGER.debug("Profiler context pages for %s: %s", spec.display_name, context_pages)
            prompt = build_universal_prompt(spec, retrieved, anchor_context=anchor_context)
            prompt_path = prompt_dir / f"{spec.attribute_key}.txt"
            prompt_path.write_text(prompt, encoding="utf-8")
            LOGGER.info("Prompt saved [%s/%s] %s: %s", index, len(ATTRIBUTE_SPECS), phase, prompt_path)
            extraction_tasks.append(
                {
                    "index": index,
                    "spec": spec,
                    "retrieved": retrieved,
                    "prompt": prompt,
                    "intermediate_dir": intermediate_dir,
                    "dry_run": dry_run,
                }
            )
        return extraction_tasks

    indexed_specs = list(enumerate(ATTRIBUTE_SPECS, start=1))
    phase1_specs = [(index, spec) for index, spec in indexed_specs if spec.attribute_key in PHASE_1_ATTRIBUTE_KEYS]
    phase2_specs = [(index, spec) for index, spec in indexed_specs if spec.attribute_key not in PHASE_1_ATTRIBUTE_KEYS]

    LOGGER.info(
        "Two-phase extraction plan: phase1_anchor=%s, phase2_guided=%s",
        len(phase1_specs),
        len(phase2_specs),
    )

    completed_results: dict[int, dict[str, Any]] = {}
    phase1_tasks = build_extraction_tasks(specs=phase1_specs, phase="phase_1_anchor")
    completed_results.update(
        _run_parallel_extraction(
            phase1_tasks,
            max_workers=EXTRACTOR_MAX_WORKERS,
        )
    )

    anchor_facts = _build_phase1_anchor_facts(completed_results)
    anchor_context = _format_phase1_anchor_context(anchor_facts)
    write_json(intermediate_dir / "08_phase1_anchor_facts.json", anchor_facts)
    (intermediate_dir / "08_phase1_anchor_context.txt").write_text(anchor_context, encoding="utf-8")
    LOGGER.info(
        "Phase 1 anchor context written: facts=%s, chars=%s",
        len(anchor_facts),
        len(anchor_context),
    )

    phase2_tasks = build_extraction_tasks(
        specs=phase2_specs,
        phase="phase_2_guided",
        anchor_context=anchor_context,
    )
    completed_results.update(
        _run_parallel_extraction(
            phase2_tasks,
            max_workers=EXTRACTOR_MAX_WORKERS,
        )
    )

    for index in sorted(completed_results):
        result = completed_results[index]
        spec = result["spec"]
        payload = result["payload"]
        for warning in result["warnings"]:
            quality_warnings.append(
                {
                    "attribute_key": spec.attribute_key,
                    "display_name": spec.display_name,
                    **warning,
                }
            )
            LOGGER.warning(
                "Extraction quality warning [%s/%s] %s: %s",
                index,
                len(ATTRIBUTE_SPECS),
                spec.display_name,
                warning["message"],
            )
        attributes_universal.append(payload)

    contact_identification_payload = build_contact_identification_payload(attributes_universal)
    attributes_universal.append(contact_identification_payload)
    strip_party_contact_fields(attributes_universal)
    attributes_flattened = [
        row
        for payload in attributes_universal
        for row in flatten_attribute(payload)
    ]

    write_json(intermediate_dir / "04_retrieval_debug.json", retrieval_debug)
    write_json(intermediate_dir / "05_attributes_universal.json", attributes_universal)
    write_json(intermediate_dir / "06_attributes_flattened.json", attributes_flattened)
    write_json(intermediate_dir / "07_quality_warnings.json", quality_warnings)
    LOGGER.info("Retrieval debug written: %s", intermediate_dir / "04_retrieval_debug.json")
    LOGGER.info("Universal attributes written: %s", intermediate_dir / "05_attributes_universal.json")
    LOGGER.info("Flattened attributes written: %s", intermediate_dir / "06_attributes_flattened.json")
    LOGGER.info("Quality warnings written: %s", intermediate_dir / "07_quality_warnings.json")

    final = {
        "run_id": started_at.strftime("%Y%m%d%H%M%S"),
        "input_path": str(input_path),
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "documents": [asdict(d) for d in parsed_documents],
        "index": {
            "retrieval_mode": "profiler_page_ranges",
            "extraction_mode": "two_phase_anchor_then_guided",
            "active_attribute_count": len(ATTRIBUTE_SPECS),
            "derived_attribute_count": 1,
            "phase_1_attribute_keys": sorted(PHASE_1_ATTRIBUTE_KEYS),
            "phase_1_anchor_facts_path": str(intermediate_dir / "08_phase1_anchor_facts.json"),
            "phase_1_anchor_context_path": str(intermediate_dir / "08_phase1_anchor_context.txt"),
            "profiler_output_path": str(profiler_result.get("metadata_path")),
        },
        "attributes_universal": attributes_universal,
        "attributes_flattened": attributes_flattened,
        "usage": {
            "llm_model": "gemini-3.1-pro",
            "llm_max_parallel_calls": EXTRACTOR_MAX_WORKERS,
            "llm_calls_planned": len(ATTRIBUTE_SPECS),
            "llm_calls_completed": len(ATTRIBUTE_SPECS),
            "cost_usd": None,
        },
        "timing": {
            "profiler_seconds": round(profiler_seconds, 3),
            "extraction_seconds": None,
            "total_seconds": None,
        },
        "errors": errors,
        "quality_warnings": quality_warnings,
    }
    final_json_path = output_dir / "final_abstraction.json"
    final_excel_path = output_dir / "final_abstraction_flattened.xlsx"
    final["index"]["flattened_excel_path"] = str(final_excel_path)
    write_json(final_json_path, final)
    export_final_abstraction_excel(final_json_path, final_excel_path)
    final["timing"]["extraction_seconds"] = round(
        time.perf_counter() - extraction_started_at,
        3,
    )
    final["timing"]["total_seconds"] = round(
        time.perf_counter() - process_started_at,
        3,
    )
    write_json(final_json_path, final)
    lease_result = build_lease_result(
        final,
        lease_id=lease_id,
        profiler_output=profiler_payload,
    )
    persist_lease_result(lease_result)
    write_json(output_dir / "lease_result.json", lease_result)
    LOGGER.info(
        "Final abstraction written: %s (documents=%s, attributes=%s, flattened_rows=%s, errors=%s, quality_warnings=%s)",
        final_json_path,
        len(parsed_documents),
        len(attributes_universal),
        len(attributes_flattened),
        len(errors),
        len(quality_warnings),
    )
    LOGGER.info("Flattened POC-style Excel written: %s", final_excel_path)
    return final

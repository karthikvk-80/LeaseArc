from pathlib import Path
from typing import Any
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
try:
    import boto3
except Exception:
    boto3 = None
from datetime import datetime
import hashlib
import json
import os
import requests
import re
import threading
import time
from config import REUSE_EXISTING_MARKDOWN
from src.mongo_persistence import (
    configured_lease_id,
    new_lease_id,
    persist_collated_profiler,
    persist_individual_profiler,
)
try:
    from pdfminer.high_level import extract_text
except Exception:
    extract_text = None
import os
try:
    from mistralai.client import Mistral
except Exception:
    try:
        from mistralai import Mistral
    except Exception:
        Mistral = None

url = "https://kkarthikeyanvk--indexing-index-pdf-endpoint.modal.run"
TABLE_SEPARATOR_PATTERN = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$")
PAGE_MARKER_PATTERN = re.compile(r"\[PAGE_(\d+)\]")
MARKDOWN_OUTPUT_DIR = Path(os.path.join(os.getcwd(), "Data", "pdf_files", "md"))
PROFILER_RESULTS_DIR = Path(os.path.join(os.getcwd(), "Data", "profiler_results"))
PIPELINE_VERSION = "v1.2.0"
MODEL_VERSION = "docling-0.3.1"
DEFAULT_UPLOAD_SOURCE = "batch"
MODEL_PRICING_USD_PER_MILLION = {
    "openai.gpt-oss-120b-1:0": {"input": 0.2704, "output": 1.0815},
    "openai.gpt-oss-120b": {"input": 0.2704, "output": 1.0815},
}
RUN_LLM_METRICS = {
    "calls": 0,
    "input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
    "bedrock_latency_ms": 0,
    "wall_time_ms": 0,
    "estimated_cost_usd": 0.0,
}
RUN_LLM_METRICS_LOCK = threading.Lock()

try:
    with open("config.json") as f:
        config_data = json.load(f)
except FileNotFoundError:
    config_data = {}


def build_manifest_from_profiler(profiler_payload: dict[str, Any]) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for index, doc in enumerate(profiler_payload.get("documents", [])):
        source = doc.get("source", {}) if isinstance(doc.get("source"), dict) else {}
        file_name = str(source.get("source_name") or "").strip()
        if not file_name:
            continue
        authoring = doc.get("authoring_info", {}) if isinstance(doc.get("authoring_info"), dict) else {}
        lease_document_info = (
            doc.get("lease_document_info", {})
            if isinstance(doc.get("lease_document_info"), dict)
            else {}
        )
        document_date = str(authoring.get("created_date") or authoring.get("registration_date") or "unknown")
        sub_category = str(
            doc.get("sub_content_category")
            or lease_document_info.get("sub_content_category")
            or authoring.get("sub_content_category")
            or ""
        )
        manifest.append(
            {
                "file_name": file_name,
                "document_type": _document_type_from_subcategory(sub_category),
                "document_order": index,
                "document_date": document_date,
            }
        )
    return manifest


def _document_type_from_subcategory(sub_category: str) -> str:
    lowered = sub_category.lower()
    if "base" in lowered or ("deed" in lowered and "amend" not in lowered):
        return "Base"
    if "renewal" in lowered:
        return "Renewal"
    if "surrender" in lowered:
        return "Surrender"
    if "amend" in lowered or "letter" in lowered or "term sheet" in lowered:
        return "Amendment"
    return "Document"


def _parameter_config_data() -> dict[str, Any]:
    config_path = Path(__file__).with_name("parameter_config.json")
    if not config_path.exists():
        return {}
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


PARAMETER_CONFIG = _parameter_config_data()
PROFILER_PARAMETER_CONFIG = (
    PARAMETER_CONFIG.get("profiler_model", {}) if isinstance(PARAMETER_CONFIG.get("profiler_model"), dict) else {}
)
IMAGE_PDF_MAX_PAGES = int(PROFILER_PARAMETER_CONFIG.get("image_pdf_max_pages", 15))
SECTION_SPAN_BUFFER_PAGES = int(PROFILER_PARAMETER_CONFIG.get("section_span_buffer_pages", 1))
ATTRIBUTE_INFO_RETRY_ATTEMPTS = int(PROFILER_PARAMETER_CONFIG.get("attribute_info_retry_attempts", 3))
ATTRIBUTE_INFO_BATCH_SIZE = int(PROFILER_PARAMETER_CONFIG.get("attribute_info_batch_size", 5))
ATTRIBUTE_INFO_MAX_WORKERS = int(PROFILER_PARAMETER_CONFIG.get("attribute_info_max_workers", 5))
REPEATABLE_ATTRIBUTE_COUNT_KEYS = {
    "Area": "area_count",
    "Expenses": "expenses_count",
    "Allowance": "allowance_count",
    "Security Deposit": "security_deposit_count",
    "Options": "options_count",
}

LEASE_ATTRIBUTE_LIST = [
    "Property name",
    "Street",
    "Street no",
    "Postal code",
    "City",
    "County",
    "State / province",
    "Country",
    "Building Type",
    "Total building area",
    "UOM",
    "Landlord Name",
    "Tenant Name",
    "Effective Date",
    "Execution Date",
    "Original Commencement Date",
    "Rent Commencement Date",
    "Current Commencement Date",
    "Current Expiration Date",
    "Original Expiration Date",
    "Possession Date",
    "Delivery Date",
    "Term Duration",
    "Lease Status",
    "Lease_Abstraction.Default",
    "Lease_Abstraction.Estoppel",
    "Lease_Abstraction.Business Hours",
    "Lease_Abstraction.Late Charges",
    "Lease_Abstraction.Repair and Maintenance",
    "Lease_Abstraction.Insurance Requirements",
    "Lease_Abstraction.Parking",
    "Lease_Abstraction.Signage",
    "Lease_Abstraction.Surrender",
    "Lease_Abstraction.Holdover",
    "Lease_Abstraction.Permitted Use",
    "Lease_Abstraction.Assignment/Sublet",
    "Lease_Abstraction.Alterations",
    "Lease_Abstraction.Operating Expenses",
    "Lease_Abstraction.RE Taxes",
    "Lease_Abstraction.Property Insurance",
    "Lease_Abstraction.Restricted Uses",
    "Lease_Abstraction.Prohibited Uses",
    "Lease_Abstraction.Exclusive Use",
    "Lease_Abstraction.Percentage Rent (Payment)",
    "Lease_Abstraction.Gross Sales (Reporting)",
    "Lease_Abstraction.Go dark",
    "Lease_Abstraction.Co-Tenancy",
    "Lease_Abstraction.Radius Restrictions",
    "Lease_Abstraction.Tenant Improvement Allowance",
    "Lease_Abstraction.Brokers",
    "Lease_Abstraction.Notices",
    "Lease_Abstraction.Base Rent Comments",
    "Lease_Abstraction.Utilities",
    "Area",
    "Expenses",
    "Allowance",
    "Security Deposit",
    "Options",
]


def _load_env_file(path: Path) -> None:
    if not path.exists() or not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(Path(__file__).with_name(".env"))

credentials = config_data.get("credentials", {})
defaults = config_data.get("defaults", {})


def _config_value(key: str, default: str | None = None) -> str | None:
    value = os.getenv(key) or credentials.get(key) or config_data.get(key) or default
    return str(value) if value else None


def _bedrock_model_id() -> str:
    model_id = (
        os.getenv("BEDROCK_MODEL_ID")
        or credentials.get("BEDROCK_MODEL_ID")
        or credentials.get("bedrock_model_id")
        or defaults.get("bedrock_model_id")
        or config_data.get("BEDROCK_MODEL_ID")
        or config_data.get("bedrock_model_id")
    )
    return str(model_id or "openai.gpt-oss-120b-1:0")


def _bedrock_region() -> str:
    region = (
        os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or credentials.get("AWS_REGION")
        or credentials.get("AWS_DEFAULT_REGION")
        or config_data.get("AWS_REGION")
        or config_data.get("AWS_DEFAULT_REGION")
    )
    return str(region or "us-east-1")


def _mongodb_dsn() -> str:
    return str(
        os.getenv("MONGODB_DSN")
        or os.getenv("MONGO_URI")
        or credentials.get("MONGODB_DSN")
        or credentials.get("mongodb_dsn")
        or config_data.get("MONGODB_DSN")
        or config_data.get("mongodb_dsn")
        or "mongodb://localhost:27017"
    )


def _mongodb_db() -> str:
    return str(
        os.getenv("MONGODB_DB")
        or os.getenv("MONGO_DB")
        or credentials.get("MONGODB_DB")
        or credentials.get("mongodb_db")
        or config_data.get("MONGODB_DB")
        or config_data.get("mongodb_db")
        or "lease"
    )


def _mistral_api_key() -> str | None:
    return (
        os.getenv("MISTRAL_API_KEY")
        or credentials.get("MISTRAL_API_KEY")
        or credentials.get("mistral_api_key")
        or config_data.get("MISTRAL_API_KEY")
        or config_data.get("mistral_api_key")
    )


for key, value in credentials.items():
    if value:
        os.environ.setdefault(key, str(value))

bedrock_model_id = _bedrock_model_id()
bedrock_region = _bedrock_region()
bedrock_client = (
    boto3.client(
        service_name="bedrock-runtime",
        region_name=bedrock_region,
    )
    if boto3 is not None
    else None
)


def reset_run_llm_metrics() -> None:
    RUN_LLM_METRICS["calls"] = 0
    RUN_LLM_METRICS["input_tokens"] = 0
    RUN_LLM_METRICS["output_tokens"] = 0
    RUN_LLM_METRICS["total_tokens"] = 0
    RUN_LLM_METRICS["bedrock_latency_ms"] = 0
    RUN_LLM_METRICS["wall_time_ms"] = 0
    RUN_LLM_METRICS["estimated_cost_usd"] = 0.0


def estimate_cost_usd(model_id: str, input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING_USD_PER_MILLION.get(model_id)
    if pricing is None:
        return 0.0
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return input_cost + output_cost


def complete_with_bedrock(prompt: str) -> str:
    if bedrock_client is None:
        raise RuntimeError("boto3 is required to run the legacy profiler LLM stage")
    request_started_at = time.perf_counter()
    resolved_model_id = bedrock_model_id
    response = bedrock_client.converse(
        modelId=resolved_model_id,
        messages=[
            {
                "role": "user",
                "content": [{"text": prompt}],
            }
        ],
    )
    elapsed_ms = int((time.perf_counter() - request_started_at) * 1000)
    usage = response.get("usage", {})
    metrics = response.get("metrics", {})
    input_tokens = int(usage.get("inputTokens", 0))
    output_tokens = int(usage.get("outputTokens", 0))
    total_tokens = int(usage.get("totalTokens", input_tokens + output_tokens))
    latency_ms = int(metrics.get("latencyMs", 0) or 0)
    estimated_cost_usd = estimate_cost_usd(resolved_model_id, input_tokens, output_tokens)

    with RUN_LLM_METRICS_LOCK:
        RUN_LLM_METRICS["calls"] += 1
        RUN_LLM_METRICS["input_tokens"] += input_tokens
        RUN_LLM_METRICS["output_tokens"] += output_tokens
        RUN_LLM_METRICS["total_tokens"] += total_tokens
        RUN_LLM_METRICS["bedrock_latency_ms"] += latency_ms
        RUN_LLM_METRICS["wall_time_ms"] += elapsed_ms
        RUN_LLM_METRICS["estimated_cost_usd"] += estimated_cost_usd

    return next(
        (
            item["text"]
            for item in response["output"]["message"]["content"]
            if "text" in item
        ),
        "",
    )

def markdown_conversion_model(file_path: str, file_id: str) -> str:

    with open(file_path, "rb") as pdf_file:
        pdf_bytes = pdf_file.read()

    filename = Path(file_path).name

    payload = {
        "pdf_file": {
            "content": base64.b64encode(pdf_bytes).decode("ascii"),
            "filename": filename,
        },
        "file_id": file_id,
        "dpi": 150,
        "do_ocr": True,
        "table_mode": "accurate",
    }

    response = requests.post(url, json=payload, allow_redirects=False)

    print("Initial Status:", response.status_code)

    if response.status_code == 303:
        redirect_url = response.headers.get("location")
        final_response = requests.get(redirect_url)
        markdown_text = final_response.text

    else:
        markdown_text = response.text
    return markdown_text


def replace_table_refs_with_content(ocr_json: dict[str, Any]) -> str:
    final_markdown = ""
    for page in ocr_json.get("pages", []):
        page_no = int(page.get("index", 0)) + 1
        markdown = page.get("markdown", "") or ""
        table_map = {
            table["id"]: table["content"]
            for table in page.get("tables", [])
            if table.get("id") and table.get("content")
        }
        for table_id, table_content in table_map.items():
            pattern = rf"\[{re.escape(table_id)}\]\({re.escape(table_id)}\)"
            markdown = re.sub(pattern, f"\n\n{table_content}\n\n", markdown)
        final_markdown += f"\n[PAGE_{page_no}]\n{markdown.strip()}\n"
    return final_markdown.strip()


def mistral_ocr_markdown(file_path: str) -> str:
    if Mistral is None:
        raise RuntimeError("Mistral OCR requested but the mistralai package is not installed.")
    api_key = _mistral_api_key()
    if not api_key:
        raise RuntimeError("Mistral OCR requested but MISTRAL_API_KEY is not configured.")

    with open(file_path, "rb") as pdf_file:
        pdf_base64 = base64.b64encode(pdf_file.read()).decode("utf-8")

    client = Mistral(api_key=api_key)
    response = client.ocr.process(
        model="mistral-ocr-latest",
        document={
            "type": "document_url",
            "document_url": f"data:application/pdf;base64,{pdf_base64}",
        },
        table_format="markdown",
        extract_header=True,
        extract_footer=True,
        confidence_scores_granularity="page",
        include_image_base64=False,
    )
    return replace_table_refs_with_content(response.model_dump())

def extract_json_from_response(response: str):
    try:
        start = response.find('{')
        end = response.rfind('}') + 1
        if start == -1 or end == 0 or end <= start:
            return None
        return json.loads(response[start:end])
    except json.JSONDecodeError as e:
        print(f"JSON decoding error: {e}")
        return None


def parse_markdown_response(response_text: str) -> dict[str, Any]:
    try:
        payload = json.loads(response_text)
    except Exception:
        return {"markdown_text": response_text}
    if isinstance(payload, dict) and payload.get("markdown_text"):
        return {"markdown_text": str(payload["markdown_text"])}
    return {"markdown_text": response_text}


def extract_pages_text_list(markdown_text: str) -> list[str]:
    page_splits = re.split(r"(\[PAGE_\d+\])", markdown_text)
    pages_text_list: list[str] = []

    current_marker: str | None = None
    for part in page_splits:
        if not part:
            continue
        if PAGE_MARKER_PATTERN.fullmatch(part):
            current_marker = part
            continue
        if current_marker is not None:
            pages_text_list.append(f"{current_marker}{part}")
            current_marker = None

    return pages_text_list


def is_markdown_table_block(lines: list[str]) -> bool:
    if len(lines) < 2:
        return False
    return "|" in lines[0] and any(TABLE_SEPARATOR_PATTERN.match(line) for line in lines[1:3])


def split_markdown_content(markdown_text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tables: list[dict[str, Any]] = []
    text_parts: list[dict[str, Any]] = []
    current_lines: list[str] = []
    recent_text_context: list[str] = []
    current_page_no = None

    def flush_block() -> None:
        nonlocal current_lines, recent_text_context, current_page_no
        block = "\n".join(current_lines).strip()
        current_lines = []
        if not block:
            return

        block_lines = block.splitlines()
        if is_markdown_table_block(block_lines):
            context = "\n".join(recent_text_context[-3:])
            tables.append({
                "text": block,
                "properties": context,
                "page_no": current_page_no
            })
            return

        recent_text_context.append(block)
        text_parts.append({
            "text": block,
            "properties": "",
            "page_no": current_page_no
        })



    for line in markdown_text.splitlines():
        stripped = line.strip()
        page_match = PAGE_MARKER_PATTERN.match(stripped)
        if page_match:
            current_page_no = int(page_match.group(1))
            continue

        if line.strip():
            current_lines.append(line)
            continue
        flush_block()

    flush_block()
    return tables, text_parts

def is_image_pdf(file_path, max_pages=IMAGE_PDF_MAX_PAGES):
    if extract_text is None:
        return "Image PDF"
    try:
        text = extract_text(file_path, maxpages=max_pages)
        text_length = len(text.strip())

        if text_length < 50:   # threshold instead of zero
            return "Image PDF"
        else:
            return "Text PDF"

    except Exception:
        return "Image PDF"


def detect_document_type(data_format: str, markdown_text: str) -> str:
    if data_format == "Image PDF":
        return "image"
    if "<!-- image -->" in markdown_text.lower():
        return "hybrid"
    return "text"


def compute_file_hash(file_path: str) -> str:
    hash_obj = hashlib.sha256()
    with open(file_path, "rb") as pdf_file:
        for chunk in iter(lambda: pdf_file.read(1024 * 1024), b""):
            hash_obj.update(chunk)
    return hash_obj.hexdigest()


def save_markdown_file(markdown_text: str, source_file_path: str, output_dir: str | Path | None = None) -> Path:
    source_path = Path(source_file_path)
    target_dir = Path(output_dir) if output_dir is not None else MARKDOWN_OUTPUT_DIR
    output_path = target_dir / f"{source_path.stem}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown_text, encoding="utf-8")
    return output_path


def get_mongo_collection(collection_name: str):
    try:
        from pymongo import MongoClient
    except ImportError as exc:
        raise RuntimeError("pymongo is required to persist profiler output to MongoDB.") from exc

    client = MongoClient(_mongodb_dsn())
    return client[_mongodb_db()][collection_name]


def upsert_mongo_document(collection_name: str, document_id: str, document: dict[str, Any]) -> None:
    collection = get_mongo_collection(collection_name)
    collection.replace_one({"_id": document_id}, document, upsert=True)


def build_markdown_context_document(
    file_id: str,
    source_file_path: str,
    markdown_text: str,
    raw_text: str,
    structured_data: dict[str, Any] | None,
    tables: list[dict[str, Any]],
    data_format: str,
    ocr_engine: str,
    processing_time_ms: int,
    status: str,
    error_message: str | None = None,
    error_stage: str | None = None,
) -> dict[str, Any]:
    source_path = Path(source_file_path)
    file_size = os.path.getsize(source_file_path)
    file_hash = compute_file_hash(source_file_path)
    detected_type = detect_document_type(data_format, markdown_text)
    page_count = len(extract_pages_text_list(markdown_text))
    language = None
    if structured_data:
        language = structured_data.get("authoring_info", {}).get("language") or structured_data.get("language")

    error_flag = error_message is not None
    is_ocr_used = data_format == "Image PDF" or detected_type == "hybrid"
    text_length = len(markdown_text)
    ocr_coverage_ratio = 1.0 if is_ocr_used else 0.0

    return {
        "_id": file_id,
        "file": {
            "filename": source_path.name,
            "filesize": file_size,
            "file_hash": file_hash,
            "upload_source": DEFAULT_UPLOAD_SOURCE,
            "storage_path": str(source_path.resolve()),
        },
        "processing": {
            "timestamp": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            "status": status,
            "processing_time_ms": processing_time_ms,
            "pipeline_version": PIPELINE_VERSION,
            "model_version": MODEL_VERSION,
            "ocr_engine": ocr_engine if is_ocr_used else "none",
            "is_ocr_used": is_ocr_used,
            "llm_calls": RUN_LLM_METRICS["calls"],
            "llm_input_tokens": RUN_LLM_METRICS["input_tokens"],
            "llm_output_tokens": RUN_LLM_METRICS["output_tokens"],
            "llm_total_tokens": RUN_LLM_METRICS["total_tokens"],
            "llm_bedrock_latency_ms": RUN_LLM_METRICS["bedrock_latency_ms"],
            "llm_wall_time_ms": RUN_LLM_METRICS["wall_time_ms"],
            "llm_estimated_cost_usd": round(RUN_LLM_METRICS["estimated_cost_usd"], 8),
        },
        "document_metadata": {
            "page_count": page_count,
            "detected_type": detected_type,
            "language": language,
            "has_tables": bool(tables),
        },
        "output": {
            "markdown_text": markdown_text,
            "raw_text": raw_text,
            "structured_data": structured_data or {},
            "tables": tables,
        },
        "quality": {
            "confidence_score": 0.87 if status == "success" else 0.0,
            "text_length": text_length,
            "ocr_coverage_ratio": ocr_coverage_ratio,
            "llm_estimated_cost_usd": round(RUN_LLM_METRICS["estimated_cost_usd"], 8),
        },
        "error": {
            "error_flag": error_flag,
            "error_message": error_message,
            "error_stage": error_stage,
        },
    }


def build_profiler_document(
    file_id: str,
    source_file_path: str,
    structured_data: dict[str, Any],
) -> dict[str, Any]:
    return {
        "_id": file_id,
        "file_id": file_id,
        "filename": Path(source_file_path).name,
        "created_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "profiler_json": structured_data,
    }



def is_likely_index_page(text):
    normalized = str(text or "").lower()

    keywords = ["contents", "index", "table of contents", "contents page"]
    has_keywords = any(k in normalized for k in keywords)

    # detect many numbers (page numbers pattern)
    numbers = re.findall(r"\b\d+\b", normalized)
    has_numbers_pattern = len(numbers) >= 5   # threshold (tunable)

    # avoid dense transactional tables that can resemble an index page
    has_dense_table_terms = any(
        k in normalized for k in ["revenue", "assets", "liabilities", "cash flow", "rent schedule", "base rent"]
    )

    # additional signal: dotted leaders (..... 23)
    has_dot_pattern = bool(re.search(r"\.{2,}\s*\d+", normalized))

    return (has_keywords or has_dot_pattern) and has_numbers_pattern and not has_dense_table_terms

def extract_pdf_info(text: str) -> dict[str, Any] | None:
    prompt = f"""
You are given the partial content of a lease-related PDF document. From the content, extract the following metadata fields and return them strictly in the JSON format shown.

Extract as accurately as possible. If a value is not explicitly stated, use null.

Return only the JSON:

{{
  "authoring_info": {{
    "language": "<language used in the document like en, fr, likewise>",
    "author": "<Name of the individual author or signing party, if mentioned, else null>",
    "organization": "<Company or institution associated with the document, else null>",
    "created_date": "<The document creation or signing date in YYYY-MM-DD format, if found, else null>",
    "document_version": "v1",
    "country": "<Country the lease or property relates to, else null>",
    "country_code": "<ISO 3166-1 alpha-2 country code for the country above, else null>"
  }},
  "lease_document_info": {{
    "content_category": "lease",
    "sub_content_category": "<One of: Lease base, Lease Amendment, Addendum, Renewal, LOI. Use the best match from the document content, else null>",
    "property_name": "<Property/building name if stated, else null>",
    "landlord_name": "<Landlord name if stated, else null>",
    "tenant_name": "<Tenant name if stated, else null>",
    "effective_date": "<Effective date in YYYY-MM-DD format if found, else null>",
    "commencement_date": "<Current or original commencement date in YYYY-MM-DD format if found, else null>",
    "expiration_date": "<Current or original expiration date in YYYY-MM-DD format if found, else null>",
    "lease_status": "<Lease status if stated, else null>"
  }}
}}

Here is the content:

{text[:5000]}
"""

    response_text = complete_with_bedrock(prompt)
    result = extract_json_from_response(response_text.strip())
    return result


def normalize_pdf_info_payload(pdf_info: dict[str, Any] | None) -> dict[str, Any]:
    payload = pdf_info if isinstance(pdf_info, dict) else {}
    authoring_info = payload.get("authoring_info")
    if not isinstance(authoring_info, dict):
        authoring_info = {}
        payload["authoring_info"] = authoring_info
    lease_document_info = payload.get("lease_document_info")
    if not isinstance(lease_document_info, dict):
        lease_document_info = {}
        payload["lease_document_info"] = lease_document_info

    for key in ("content_category", "sub_content_category"):
        lease_value = lease_document_info.get(key)
        authoring_value = authoring_info.pop(key, None)
        if lease_value in (None, "") and authoring_value not in (None, ""):
            lease_document_info[key] = authoring_value

    return payload


def _normalize_title_key(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def build_section_spans(
    section_titles_page_no: list[str],
    page_count: int,
    *,
    buffer_pages: int = SECTION_SPAN_BUFFER_PAGES,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for raw_item in section_titles_page_no:
        if not isinstance(raw_item, str) or " ||| page_no: " not in raw_item:
            continue
        title, page_no_text = raw_item.rsplit(" ||| page_no: ", 1)
        title = title.strip()
        try:
            start_page = int(page_no_text.strip())
        except Exception:
            continue
        if not title:
            continue
        entries.append({"title": title, "start_page": start_page})

    if not entries:
        return []

    spans: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        start_page = entry["start_page"]
        next_start_page = entries[index + 1]["start_page"] if index + 1 < len(entries) else page_count + 1
        end_page = min(max(start_page, next_start_page - 1), page_count)
        buffered_start = max(1, start_page - buffer_pages)
        buffered_end = min(page_count, end_page + buffer_pages)
        spans.append(
            {
                "title": entry["title"],
                "start_page": start_page,
                "end_page": end_page,
                "buffered_start_page": buffered_start,
                "buffered_end_page": buffered_end,
                "buffered_page_range": list(range(buffered_start, buffered_end + 1)),
            }
        )
    return spans


def format_section_spans_for_prompt(section_spans: list[dict[str, Any]]) -> str:
    if not section_spans:
        return "[]"
    return json.dumps(section_spans, ensure_ascii=False, indent=2)


def _titles_match(left: str, right: str) -> bool:
    a = _normalize_title_key(left)
    b = _normalize_title_key(right)
    return bool(a and b and (a == b or a in b or b in a))


def expand_attribute_page_ranges(
    attribute_payload: dict[str, Any],
    section_spans: list[dict[str, Any]],
    page_count: int,
) -> dict[str, Any]:
    attribute_info = attribute_payload.get("attribute_info")
    if not isinstance(attribute_info, dict):
        return attribute_payload

    for details in attribute_info.values():
        if not isinstance(details, dict):
            continue

        matched_titles = details.get("might_be_present_under")
        matched_pages: set[int] = set()
        if isinstance(matched_titles, list):
            for matched_title in matched_titles:
                for span in section_spans:
                    if _titles_match(matched_title, span.get("title", "")):
                        matched_pages.update(span.get("buffered_page_range", []))

        if matched_pages:
            details["page_number_range"] = sorted(page for page in matched_pages if 1 <= int(page) <= page_count)
            continue

        page_range = details.get("page_number_range")
        if isinstance(page_range, list) and page_range:
            valid_pages = sorted({int(page) for page in page_range if isinstance(page, int) or str(page).isdigit()})
            if valid_pages:
                details["page_number_range"] = list(range(max(1, valid_pages[0]), min(page_count, valid_pages[-1]) + 1))

    return attribute_payload


def normalize_attribute_info_payload(attribute_payload: dict[str, Any]) -> dict[str, Any]:
    attribute_info = attribute_payload.get("attribute_info")
    if not isinstance(attribute_info, dict):
        return attribute_payload

    for attribute_name, details in attribute_info.items():
        if not isinstance(details, dict):
            attribute_info[attribute_name] = {"is_attribute_present_file": False}
            details = attribute_info[attribute_name]

        present_flag = details.get("is_attribute_present_file", False)
        if isinstance(present_flag, str):
            present_flag = present_flag.strip().lower() in {"true", "yes", "1", "present"}
        else:
            present_flag = bool(present_flag)
        details["is_attribute_present_file"] = present_flag

        if "Landlord Name" in str(attribute_name):
            landlord_count = details.get("Landlord_count", 0 if not present_flag else None)
            if isinstance(landlord_count, str) and landlord_count.strip().isdigit():
                landlord_count = int(landlord_count.strip())
            elif not isinstance(landlord_count, int):
                landlord_count = 0 if not present_flag else landlord_count
            details["Landlord_count"] = landlord_count
            reason = details.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                if present_flag:
                    reason = "Landlord count inferred from the distinct landlord party names explicitly identified in the lease."
                else:
                    reason = "No landlord party name was explicitly identified in the lease."
            details["reason"] = reason

        count_key = REPEATABLE_ATTRIBUTE_COUNT_KEYS.get(str(attribute_name))
        if count_key:
            if not present_flag:
                details[count_key] = 0
                continue
            raw_count = details.get(count_key, 0 if not present_flag else None)
            if isinstance(raw_count, str):
                normalized = raw_count.strip()
                raw_count = int(normalized) if normalized.isdigit() else (0 if not present_flag else None)
            elif not isinstance(raw_count, int):
                raw_count = 0 if not present_flag else raw_count
            if raw_count is None:
                raw_count = 0 if not present_flag else 1
            details[count_key] = max(0, raw_count)

    return attribute_payload

def extract_structure_info_from_markdown(markdown_text, objects=None, attributes=None):
    # ---------------------------
    # PAGE SPLIT
    # ---------------------------
    page_splits = re.split(r"\[PAGE_(\d+)\]", markdown_text)
    
    pages = []
    for i in range(1, len(page_splits), 2):
        page_no = int(page_splits[i])
        content = page_splits[i + 1]
        pages.append({"page": page_no, "text": content})

    page_count = len(pages)

    # ---------------------------
    # INIT (same as your code)
    # ---------------------------
    section_titles = []
    section_titles_page_no = []
    section_titles_dict = {}
    tables = []
    images = []
    charts = []
    has_headers = False
    header_styles = set()
    footnote_count = 0
    text_block_count = 0
    has_figures = False
    has_images = False
    has_tables = False

    # ---------------------------
    # PROCESS EACH PAGE
    # ---------------------------
    for page in pages:
        text = page["text"]

        # ---------------------------
        # HEADINGS
        # ---------------------------
        headings = re.findall(r"^(#{1,6})\s+(.*)", text, re.MULTILINE)
        for hashes, title in headings:
            clean_title = title.strip()

            section_titles.append(clean_title)
            section_titles_dict[clean_title] = page["page"]
            section_titles_page_no.append(f"{clean_title} ||| page_no: {page['page']}")

            has_headers = True

            if clean_title.isupper():
                header_styles.add("uppercase")

            header_styles.add(f"H{len(hashes)}")

        # ---------------------------
        # TABLES (markdown tables)
        # ---------------------------
        table_blocks = re.findall(
            r"(\|.+?\|\n\|[-\s|]+\|\n(?:\|.*\|\n?)*)",
            text
        )

        if table_blocks:
            has_tables = True

        for tbl in table_blocks:
            rows = [r for r in tbl.split("\n") if r.strip().startswith("|")]

            table = {
                "summary": rows[0] if rows else "",
                "markdown": tbl,
                "page_number": page["page"],
                "row_count": len(rows),
                "column_count": max((len(r.split("|")) - 2 for r in rows), default=0),
                "has_merged_cells": False,
                "has_nested_tables": False,
                "isPerfectTable": True,
                "attribute_info": []
            }
            tables.append(table)

        # ---------------------------
        # IMAGES
        # ---------------------------
        page_images = re.findall(r"<!--\s*image\s*-->", text, re.IGNORECASE)
        if page_images:
            has_images = True
            for _ in page_images:
                images.append({
                    "page_number": page["page"],
                    "summary": text[:200]  # mimic your summary logic
                })

        # ---------------------------
        # FIGURES
        # ---------------------------
        if page_images:
            has_figures = True

        # ---------------------------
        # FOOTNOTES
        # ---------------------------
        footnotes = re.findall(r"\[\d+\]|\(\d+\)", text)
        footnote_count += len(footnotes)

        # ---------------------------
        # TEXT BLOCK COUNT
        # ---------------------------
        text_block_count += len([l for l in text.split("\n") if l.strip()])

    # ---------------------------
    # FINAL STRUCTURE (UNCHANGED)
    # ---------------------------
    structure_info = {
        "has_sections": len(section_titles) > 0,
        "section_count": len(section_titles),
        "section_titles": section_titles_dict,

        "has_tables": has_tables,
        "table_count": len(tables),
        "tables": tables,

        "has_headers": len(header_styles) > 0,
        "header_styles": list(header_styles),

        "has_footnotes": footnote_count > 0,
        "footnote_count": footnote_count,

        "has_images": has_images,
        "image_count": len(images),
        "image_summary": images,

        "has_figures": has_figures,

        "text_block_count": text_block_count,
        "average_paragraph_length": round(text_block_count / page_count, 1) if page_count > 0 else 0,

        "layout_type": "single-column",

        "page_dimensions": {
            "width": "8.27in",
            "height": "11.69in",
            "unit": "inches"
        },

        "reading_order": "top-to-bottom-left-to-right",

        "embedded_objects": {
            "charts": 0,
            "forms": 0,
            "form_fields": []
        },

        "document_hierarchy": {
            "levels": max([int(h[1]) for h in header_styles if h.startswith("H")], default=0),
            "uses_numbering": any(t.strip().startswith(tuple("1234567890")) for t in section_titles)
        }
    }

    return structure_info, section_titles_page_no, page_count


def save_failed_attribute_info_attempt(
    source_file_path: str,
    attempt: int,
    attributes: list,
    response_text: str,
    parsed_response: Any,
) -> Path:
    source_path = Path(source_file_path).resolve()
    output_path = source_path.with_name(f"{source_path.stem}_failed_attempt_{attempt}_attribute_info.json")
    payload = {
        "attempt": attempt,
        "attributes": attributes,
        "raw_response": response_text,
        "parsed_response": parsed_response,
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_path


def extract_attribute_info(attributes: list, section_titles: str, section_spans: str, page_count: int) -> tuple[dict[str, Any], str, Any]:
    prompt = f"""
You are given:
1. A list of lease attributes to analyze.
2. A list of section titles from a lease document.
3. Generic section spans derived from those section-title start pages.
4. Structural context from the lease document.

Your task is to return structured metadata for each attribute.

For each attribute, provide the following:
- definition: A one-sentence explanation of what the attribute means in a lease abstraction context.
- is_attribute_present_file: true if the attribute is explicitly present anywhere in the file, otherwise false.
- page_number_range: An approximate page range where this attribute might be located based on the section spans. Return all page numbers in the likely inclusive range, not only sparse title pages.
- might_be_present_under: A list of section titles (from the given list) that are likely to contain this attribute, using fuzzy matching and semantic understanding.

Additional rule for landlord attributes:
- For "Landlord Name", also return "Landlord_count" as the number of distinct landlords explicitly stated in the file. Return 0 if none are present.
- For "Landlord Name", also return "reason" as a short justification for why that landlord count was assigned.

Additional rules for repeatable attributes:
- For "Area", also return "area_count" as the number of distinct area rows/items explicitly supported by the file. Return 0 if none are present.
- For "Expenses", also return "expenses_count" as the number of distinct recurring rent/expense rows or concurrent charge streams explicitly supported by the file. Return 0 if none are present.
- For "Allowance", also return "allowance_count" as the number of distinct allowance or concession items explicitly supported by the file. Return 0 if none are present.
- For "Security Deposit", also return "security_deposit_count" as the number of distinct deposit/security instruments or tranches explicitly supported by the file. Return 0 if none are present.
- For "Options", also return "options_count" as the number of distinct option rights explicitly supported by the file. Return 0 if none are present.
- Count only items explicitly evidenced in the file. Do not infer hidden rows/items.

Use this rule generically for every attribute:
- a section title marks where content begins, not necessarily where it ends
- content can continue to later pages
- prefer the provided buffered section spans over single title pages
- if a section is likely, include the full inclusive pages from that buffered span

Return only JSON in this format:

{{
  "attribute_info": {{
    "Effective Date": {{
      "definition": "...",
      "is_attribute_present_file": true,
      "page_number_range": [4,6,11,23],
      "might_be_present_under": ["Basic Lease Information", "Term", "Premises"]
    }},
    "Landlord Name": {{
      "definition": "...",
      "is_attribute_present_file": true,
      "Landlord_count": 2,
      "reason": "Two distinct landlord entities are named in the parties section of the lease.",
      "page_number_range": [1,2,3],
      "might_be_present_under": ["Parties", "Basic Lease Information"]
    }},
    "Expenses": {{
      "definition": "...",
      "is_attribute_present_file": true,
      "expenses_count": 3,
      "page_number_range": [12,13,14],
      "might_be_present_under": ["Rent", "Operating Expenses", "Schedule of Rent"]
    }},
    ...
  }}
}}

Attributes to analyze:
{attributes}

Section Titles:
{section_titles}

Section Spans:
{section_spans}
    """

    response_text = complete_with_bedrock(prompt)
    response_preview = response_text.strip()
    parsed = extract_json_from_response(response_preview)
    if not isinstance(parsed, dict):
        print(f"extract_attribute_info returned non-dict response for batch {attributes}:")
        print(response_preview)
        return {}, response_preview, parsed
    expanded = expand_attribute_page_ranges(parsed, json.loads(section_spans), page_count)
    expanded = normalize_attribute_info_payload(expanded)
    attribute_info = expanded.get("attribute_info")
    if not isinstance(attribute_info, dict) or not attribute_info:
        print(f"extract_attribute_info returned empty attribute_info for batch {attributes}:")
        print(response_preview)
    return expanded, response_preview, parsed


def extract_attribute_info_with_retry(
    attributes: list,
    section_titles: str,
    section_spans: str,
    page_count: int,
    source_file_path: str,
    *,
    max_attempts: int = ATTRIBUTE_INFO_RETRY_ATTEMPTS,
) -> dict[str, Any]:
    for attempt in range(1, max_attempts + 1):
        result, response_text, parsed_response = extract_attribute_info(attributes, section_titles, section_spans, page_count)
        result = result or {}
        attribute_info = result.get("attribute_info")
        if isinstance(attribute_info, dict) and attribute_info:
            return result
        failed_path = save_failed_attribute_info_attempt(
            source_file_path,
            attempt,
            attributes,
            response_text,
            parsed_response,
        )
        print(
            f"attribute_info empty for batch {attributes} on attempt {attempt}/{max_attempts}"
        )
        print(f"saved failed attribute_info attempt to: {failed_path}")
    return {}

def build_structured_json(
    markdown_text: str,
    tables: list[dict[str, Any]],
    file_path: str,
    filename: str,
    attribute_list: list[str],
    data_format: str,
    ocr_engine: str,
) -> dict[str, Any]:
    batch_size = ATTRIBUTE_INFO_BATCH_SIZE
    max_workers = max(1, ATTRIBUTE_INFO_MAX_WORKERS)
    now = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    pages_text_list = extract_pages_text_list(markdown_text)
    author_info = normalize_pdf_info_payload(extract_pdf_info(markdown_text) or {})
    ext_str_info = time.time()
    structure_info, section_titles, page_count = extract_structure_info_from_markdown(markdown_text)
    section_spans = build_section_spans(section_titles, page_count)
    section_spans_prompt = format_section_spans_for_prompt(section_spans)
    print(f"Text extraction took {round(time.time() - ext_str_info, 2)} seconds")
    attribute_info: dict[str, Any] = {}
    attribute_batches = [
        attribute_list[i:i + batch_size]
        for i in range(0, len(attribute_list), batch_size)
    ]
    worker_count = min(len(attribute_batches), max_workers) if attribute_batches else 1
    if attribute_batches:
        print(
            f"Running {len(attribute_batches)} attribute batches in parallel "
            f"(batch_size={batch_size}, max_workers={worker_count})"
        )
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_batch = {
                executor.submit(
                    extract_attribute_info_with_retry,
                    attribute_batch,
                    section_titles,
                    section_spans_prompt,
                    page_count,
                    file_path,
                ): attribute_batch
                for attribute_batch in attribute_batches
            }
            for future in as_completed(future_to_batch):
                attribute_batch = future_to_batch[future]
                attribute_result = future.result() or {}
                attribute_info.update(attribute_result.get("attribute_info", {}))
                print(f"Completed attribute batch of {len(attribute_batch)} items")
 
    word_count = sum(len(page_text.split()) for page_text in pages_text_list)

    structured_json = {
        "artifact_id": f"artifact_{Path(filename).stem.lower()}",
        "artifact_type": "structured_data",
        "data_format": data_format,
        "language": author_info.get("authoring_info", {}).get("language"),
        "country": author_info.get("authoring_info", {}).get("country"),
        "content_category": author_info.get("lease_document_info", {}).get("content_category"),
        "sub_content_category": author_info.get("lease_document_info", {}).get("sub_content_category"),
        "source": {
            "origin": "uploaded_pdf",
            "source_name": filename,
            "source_url": None,
            "mime_type": "application/pdf",
            "extraction_tool": "LLM-assisted + OCR where needed",
            "ingestion_timestamp": now
        },
        "authoring_info": author_info.get("authoring_info", {}),
        "lease_document_info": author_info.get("lease_document_info", {}),
        "file_stats": {
            "file_size_kb": round((os.path.getsize(file_path) / 1024), 2),
            "page_count": page_count,
            "word_count": word_count,
            "table_count": len(tables),
            "language_detected": "English",
            "ocr_applied": ocr_engine != "none",
            "ocr_engine": ocr_engine,
        },
        "structure_info": structure_info,
        "attribute_info": attribute_info
    }
    print(f"Structured JSON built in {round(time.time() - ext_str_info, 2)} seconds")
    print(structured_json)
    return structured_json

def save_metadata_json(
    metadata: dict,
    source_file_path: str,
    output_dir: str | Path = PROFILER_RESULTS_DIR,
) -> Path:
    source_path = Path(source_file_path).resolve()
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    output_path = target_dir / f"{source_path.stem}_metadata.json"
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    return output_path

def build_run_metrics(processing_time_ms: int) -> dict[str, Any]:
    return {
        "processing_time_ms": processing_time_ms,
        "processing_time_seconds": round(processing_time_ms / 1000, 3),
        "llm_calls": RUN_LLM_METRICS["calls"],
        "llm_input_tokens": RUN_LLM_METRICS["input_tokens"],
        "llm_output_tokens": RUN_LLM_METRICS["output_tokens"],
        "llm_total_tokens": RUN_LLM_METRICS["total_tokens"],
        "llm_bedrock_latency_ms": RUN_LLM_METRICS["bedrock_latency_ms"],
        "llm_wall_time_ms": RUN_LLM_METRICS["wall_time_ms"],
        "llm_estimated_cost_usd": round(RUN_LLM_METRICS["estimated_cost_usd"], 8),
        "cost_currency": "USD",
    }


def resolve_input_pdf_path(input_path: str | None) -> str:
    default_dir = Path(os.getcwd()) / "Data" / "pdf_files"
    candidate = Path(input_path).expanduser() if input_path else default_dir

    if candidate.is_dir():
        pdf_files = sorted(path for path in candidate.iterdir() if path.is_file() and path.suffix.lower() == ".pdf")
        if not pdf_files:
            raise FileNotFoundError(f"No PDF files found in directory: {candidate}")
        return str(pdf_files[0].resolve())

    if not candidate.exists():
        raise FileNotFoundError(f"Input path does not exist: {candidate}")
    if not candidate.is_file():
        raise ValueError(f"Input path is not a file: {candidate}")
    if candidate.suffix.lower() != ".pdf":
        raise ValueError(f"Input file must be a PDF: {candidate}")
    return str(candidate.resolve())


def parse_document(
    file_path: str,
    file_id: str = "file_123",
    markdown_output_dir: str | Path | None = None,
    reuse_existing_markdown: bool = REUSE_EXISTING_MARKDOWN,
) -> dict[str, Any]:
    start_time = time.time()
    reset_run_llm_metrics()
    markdown_text = ""
    tables: list[dict[str, Any]] = []
    text_parts: list[dict[str, Any]] = []
    source_path = Path(file_path)
    output_dir = Path(markdown_output_dir) if markdown_output_dir is not None else MARKDOWN_OUTPUT_DIR
    markdown_output_path = output_dir / f"{source_path.stem}.md"

    if reuse_existing_markdown and markdown_output_path.is_file():
        print(f"Reusing existing markdown: {markdown_output_path}")
        markdown_text = markdown_output_path.read_text(encoding="utf-8")
        tables, text_parts = split_markdown_content(markdown_text)
        processing_time_ms = int((time.time() - start_time) * 1000)
        print(
            "Cached markdown ready: "
            f"{len(tables)} table(s), {len(text_parts)} text part(s)"
        )
        return {
            "markdown_path": str(markdown_output_path),
            "markdown_text": markdown_text,
            "profiler_run_metrics": build_run_metrics(processing_time_ms),
            "table_count": len(tables),
            "text_part_count": len(text_parts),
            "reused_markdown": True,
        }

    data_format = is_image_pdf(file_path)
    ocr_engine = "modal"
    current_stage = "markdown_conversion"
    print(f"Profiler started for file: {file_path}")
    print(f"Detected data format: {data_format}; parser/ocr engine: {ocr_engine}")
    print("Markdown parsing stage started.")

    try:
        print(f"Parsing started with Modal markdown parser: {file_path}")
        markdown_response_text = markdown_conversion_model(file_path, file_id)
        markdown_payload = parse_markdown_response(markdown_response_text)
        markdown_text = markdown_payload["markdown_text"]
        print(f"Parsing completed for file: {file_path}")

        current_stage = "markdown_file_write"
        markdown_output_path = save_markdown_file(markdown_text, file_path, markdown_output_dir)
        print(f"Markdown saved here: {markdown_output_path}")

        current_stage = "content_split"
        print("Splitting parsed markdown into tables and text parts.")
        tables, text_parts = split_markdown_content(markdown_text)
        print(f"{len(tables)} tables extracted.")
        print(f"{len(text_parts)} text parts extracted.")

        processing_time_ms = int((time.time() - start_time) * 1000)
        return {
            "markdown_path": str(markdown_output_path),
            "markdown_text": markdown_text,
            "profiler_run_metrics": build_run_metrics(processing_time_ms),
            "table_count": len(tables),
            "text_part_count": len(text_parts),
            "reused_markdown": False,
        }
    except Exception as exc:
        print(f"Profiler parsing failed at stage {current_stage}: {exc}")
        raise


def _relationship_type(document: dict[str, Any]) -> str:
    lease_info = document.get("lease_document_info", {})
    category = " ".join(
        str(value or "").lower()
        for value in (
            document.get("sub_content_category"),
            lease_info.get("sub_content_category") if isinstance(lease_info, dict) else None,
        )
    )
    if "amend" in category or "supplement" in category:
        return "amendment"
    if "addendum" in category:
        return "addendum"
    if "renew" in category:
        return "renewal"
    if "letter of intent" in category or re.search(r"\bloi\b", category):
        return "loi"
    if "lease base" in category or "base lease" in category:
        return "base_lease"
    return "other_lease_document"


def collated_profiler(
    documents: list[dict[str, Any]],
    output_path: str | Path,
) -> dict[str, Any]:
    if not documents:
        raise ValueError("At least one profiler document is required.")

    typed_documents = [(document, _relationship_type(document)) for document in documents]
    base_candidates = [
        document for document, document_type in typed_documents
        if document_type == "base_lease"
    ]
    if not base_candidates:
        base_candidates = [
            document for document, document_type in typed_documents
            if document_type == "other_lease_document"
        ]
    base_document = (
        base_candidates[0].get("source", {}).get("source_name")
        if base_candidates
        else None
    )
    related_documents = [
        {
            "document": document.get("source", {}).get("source_name"),
            "document_type": document_type,
            "relationship": (
                f"{document_type.replace('_', ' ').title()} related to "
                f"{base_document or 'the lease package'}."
            ),
        }
        for document, document_type in typed_documents
        if document.get("source", {}).get("source_name") != base_document
    ]
    profiler_output = {
        "profiler_run_id": f"profiler_lease_package_{datetime.utcnow().strftime('%Y%m%d')}",
        "artifact_type": "concatenated_profiler_result",
        "content_category": next(
            (document.get("content_category") for document in documents if document.get("content_category")),
            "lease",
        ),
        "country": next((document.get("country") for document in documents if document.get("country")), None),
        "country_code": next(
            (
                document.get("authoring_info", {}).get("country_code")
                for document in documents
                if isinstance(document.get("authoring_info"), dict)
                and document.get("authoring_info", {}).get("country_code")
            ),
            None,
        ),
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "profile_strategy": "One profiler object per lease PDF, combined into a single JSON object.",
        "input_files": {
            "lease_pdf_files_profiled": [
                document.get("source", {}).get("source_name")
                for document in documents
            ],
            "total_pdf_pages_profiled": sum(
                int(document.get("file_stats", {}).get("page_count") or 0)
                for document in documents
            ),
            "total_pdf_files_profiled": len(documents),
        },
        "document_relationships": {
            "base_document": base_document,
            "known_amendments_or_related_documents": related_documents,
        },
        "documents": documents,
    }
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(profiler_output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return profiler_output


def run_package(
    input_path: str | Path,
    markdown_output_dir: str | Path | None = None,
    lease_id: str | None = None,
) -> dict[str, Any]:
    start_time = time.time()
    lease_id = lease_id or configured_lease_id() or new_lease_id()
    input_root = Path(input_path)
    print(f"Profiler package run started: {input_root}")
    if input_root.is_file() and input_root.suffix.lower() == ".pdf":
        pdf_files = [input_root]
    elif input_root.is_dir():
        pdf_files = sorted(input_root.glob("*.pdf"), key=lambda path: path.name.lower())
    else:
        raise FileNotFoundError(f"Profiler input path not found: {input_root}")
    if not pdf_files:
        raise FileNotFoundError(f"No PDF files found for profiler parsing: {input_root}")

    if markdown_output_dir is not None:
        markdown_dir = Path(markdown_output_dir)
        markdown_dir.mkdir(parents=True, exist_ok=True)
        current_markdown_names = {f"{pdf_path.stem}.md" for pdf_path in pdf_files}
        for markdown_path in markdown_dir.glob("*.md"):
            if markdown_path.name not in current_markdown_names:
                markdown_path.unlink()
                print(f"Removed stale markdown output: {markdown_path}")

    print(f"Parsing stage started: {len(pdf_files)} PDF document(s)")
    print(f"Reuse existing markdown: {REUSE_EXISTING_MARKDOWN}")
    parsed_by_name: dict[str, dict[str, Any]] = {}
    for index, file_path in enumerate(pdf_files, start=1):
        print(f"Preparing document [{index}/{len(pdf_files)}]: {file_path.name}")
        parsed = parse_document(
            str(file_path),
            file_id=f"doc_{index:03d}",
            markdown_output_dir=markdown_output_dir,
            reuse_existing_markdown=REUSE_EXISTING_MARKDOWN,
        )
        parsed_by_name[file_path.name] = {
            "file_name": file_path.name,
            "file_path": str(file_path),
            "markdown_path": parsed["markdown_path"],
            "markdown_text": parsed["markdown_text"],
            "profiler_run_metrics": parsed["profiler_run_metrics"],
            "table_count": parsed["table_count"],
            "text_part_count": parsed["text_part_count"],
            "reused_markdown": parsed["reused_markdown"],
        }
    reused_count = sum(
        1 for parsed_document in parsed_by_name.values()
        if parsed_document["reused_markdown"]
    )
    print(
        "Parsing stage completed: "
        f"{len(parsed_by_name)} markdown file(s) ready; "
        f"reused={reused_count}; parsed={len(parsed_by_name) - reused_count}"
    )

    print("Profiling stage started: building one profiler object per PDF")
    profiler_results_dir = (
        Path(markdown_output_dir).parent / "profiler_results"
        if markdown_output_dir is not None
        else PROFILER_RESULTS_DIR
    )
    profiler_documents: list[dict[str, Any]] = []
    for index, file_path in enumerate(pdf_files, start=1):
        parsed = parsed_by_name[file_path.name]
        tables, _ = split_markdown_content(parsed["markdown_text"])
        profiler_document = build_structured_json(
            parsed["markdown_text"],
            tables,
            str(file_path),
            file_path.name,
            LEASE_ATTRIBUTE_LIST,
            is_image_pdf(str(file_path)),
            "modal",
        )
        profiler_document["file_id"] = f"file_{compute_file_hash(str(file_path))[:16]}"
        profiler_document["profiler_run_metrics"] = parsed["profiler_run_metrics"]
        metadata_path = save_metadata_json(
            profiler_document,
            str(file_path),
            output_dir=profiler_results_dir,
        )
        print(f"Profiler object completed [{index}/{len(pdf_files)}]: {metadata_path}")
        persist_individual_profiler(lease_id, profiler_document)
        profiler_documents.append(profiler_document)

    profiler_output_path = (
        Path(markdown_output_dir).parent / "merged_profiler_result.json"
        if markdown_output_dir is not None
        else PROFILER_RESULTS_DIR / "merged_profiler_result.json"
    )
    profiler_output = collated_profiler(profiler_documents, profiler_output_path)
    persist_collated_profiler(lease_id, profiler_output)
    document_manifest = build_manifest_from_profiler(profiler_output)

    parsed_names = set(parsed_by_name)
    profiler_names = {document["file_name"] for document in document_manifest}
    if parsed_names != profiler_names:
        raise ValueError(
            "Parsed PDF files do not match merged profiler documents. "
            f"Only parsed: {sorted(parsed_names - profiler_names)}; "
            f"only profiler: {sorted(profiler_names - parsed_names)}"
        )

    parsed_documents: list[dict[str, Any]] = []
    for document in document_manifest:
        parsed_documents.append({**document, **parsed_by_name[document["file_name"]]})

    print(f"Profiling stage completed: merged profiler output saved to {profiler_output_path}")

    elapsed_ms = int((time.time() - start_time) * 1000)
    print(
        "Merged profiler output ready: "
        f"{len(profiler_output.get('documents', []))} documents; "
        f"parsed_documents={len(parsed_documents)}; elapsed={elapsed_ms} ms"
    )
    return {
        "metadata_path": str(profiler_output_path),
        "lease_id": lease_id,
        "profiler_output": profiler_output,
        "document_manifest": document_manifest,
        "parsed_documents": parsed_documents,
        "profiler_run_metrics": {
            "processing_time_ms": elapsed_ms,
            "processing_time_seconds": round(elapsed_ms / 1000, 3),
        },
        "note": "PDFs were parsed, profiled, collated, and passed directly to extraction.",
    }


def main(
    file_path: str,
    file_id: str = "file_123",
    markdown_output_dir: str | Path | None = None,
) -> dict[str, Any]:
    package_result = run_package(file_path, markdown_output_dir=markdown_output_dir)
    parsed = package_result["parsed_documents"][0]
    return {**package_result, **parsed, "file_id": file_id}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run lease profiler on a PDF file.")
    parser.add_argument(
        "input_path",
        nargs="?",
        default=None,
        help="Path to a PDF file or a directory containing PDFs. Defaults to Data/pdf_files.",
    )
    parser.add_argument(
        "--file-id",
        dest="file_id",
        default="file_123",
        help="Identifier used for metadata and Mongo persistence.",
    )
    args = parser.parse_args()

    file_path = resolve_input_pdf_path(args.input_path)
    print(f"Using input PDF: {file_path}")
    main(file_path, file_id=args.file_id)

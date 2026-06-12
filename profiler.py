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
import tempfile
import threading
import time
from urllib.parse import unquote, urlparse
from src.attributes import REFERENCE_HINTS
from src.page_splitter import markdown_pages
from src.mongo_persistence import (
    configured_lease_id,
    new_lease_id,
    persist_collated_profiler,
    persist_individual_profiler,
    persist_markdown,
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
    config_data = json.loads(
        Path(__file__).with_name("config.json").read_text(encoding="utf-8")
    )
except (FileNotFoundError, json.JSONDecodeError):
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
                "file_id": str(doc.get("file_id") or ""),
                "source_s3_path": str(source.get("source_url") or ""),
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
ATTRIBUTE_INFO_RETRY_ATTEMPTS = int(PROFILER_PARAMETER_CONFIG.get("attribute_info_retry_attempts", 3))
ATTRIBUTE_INFO_MAX_WORKERS = int(PROFILER_PARAMETER_CONFIG.get("attribute_info_max_workers", 5))
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

LEASE_ATTRIBUTE_PREFIX = "Lease_catalyst.Lease_Abstraction."
ATTRIBUTE_CATEGORIES: dict[str, list[str]] = {
    "Core Lease Terms": [
        "Property name", "Street", "Street no", "Postal code", "City", "County",
        "State / province", "Country", "Building Type", "Total building area", "UOM",
    ],
    "Parties": [
        "Landlord Name", "Tenant Name",
        "Contact Identification.0.Contact Type", "Contact Identification.0.Name",
        "Contact Identification.0.Attention", "Contact Identification.0.Care of",
        "Contact Identification.0.DBA", "Contact Identification.0.Street no.",
        "Contact Identification.0.Street", "Contact Identification.0.Suite",
        "Contact Identification.0.P.O. Box", "Contact Identification.0.Zip code",
        "Contact Identification.0.City", "Contact Identification.0.State / Province",
        "Contact Identification.0.Country",
        "Contact Identification.0.Additional address details",
        "Contact Identification.0.Phone", "Contact Identification.0.Mobile",
        "Contact Identification.0.Fax", "Contact Identification.0.Email",
    ],
    "Critical Dates": [
        "Effective Date", "Execution Date", "Original Commencement Date",
        "Rent Commencement Date", "Current Commencement Date",
        "Current Expiration Date", "Original Expiration Date", "Possession Date",
        "Delivery Date", "Term Duration",
    ],
    "Restrictive Clauses": [
        "Lease Status", "Default", "Estoppel", "Business Hours", "Late Charges",
        "Repair and Maintenance", "Insurance Requirements", "Parking", "Signage",
        "Surrender", "Holdover", "Permitted Use", "Assignment/Sublet",
        "Alterations", "Operating Expenses", "RE Taxes", "Property Insurance",
        "Restricted Uses", "Prohibited Uses", "Exclusive Use",
        "Percentage Rent (Payment)", "Gross Sales (Reporting)", "Go dark",
        "Co-Tenancy", "Radius Restrictions", "Tenant Improvement Allowance",
        "Brokers", "Notices", "Base Rent Comments", "Utilities",
    ],
    "Expenses": [
        "Expenses", "Expenses.0.Rent Type", "Expenses.0.Start date",
        "Expenses.0.End date", "Expenses.0.Monthly Amount",
        "Expenses.0.Monthly Amount per SF", "Expenses.0.Annual Amount",
        "Expenses.0.Annual amount per SF", "Expenses.0.Currency",
        "Expenses.0.On Day", "Expenses.0.Payment Frequency",
    ],
    "Allowance": [
        "Allowance", "Allowance.0.Allowance Type", "Allowance.0.Allowance Amount",
        "Allowance.0.Payment Deadline", "Allowance.0.Allowance Comments",
    ],
    "Security Deposit": [
        "Security Deposit", "Security Deposit.0.Security Deposit Type",
        "Security Deposit.0.Security Deposit Amount",
        "Security Deposit.0.Security Deposit Currency",
        "Security Deposit.0.Payment Date", "Security Deposit.0.Return Due Date",
        "Security Deposit.0.Security Deposit Comments",
    ],
    "Options": [
        "Options", "Options.0.Option type", "Options.0.Option status",
        "Options.0.Option Effective Date", "Options.0.Option End Date",
        "Options.0.Option Earliest Notice",
        "Options.0.Option Latest Notice Deadline", "Options.0.Options Comments",
    ],
    "Area": [
        "Area", "Area.0.Unit/suite number", "Area.0.Type", "Area.0.Gross area",
        "Area.0.Gross Area UOM", "Area.0.Net area", "Area.0.Net Area UOM",
        "Area.0.Floor no.", "Area.0.Start date", "Area.0.End date",
        "Area.0.Duration",
    ],
}

ATTRIBUTE_DEFINITION_OVERRIDES = {
    "Contact Identification": "Contact details or an address block for a lease party.",
    "Expenses": "Rent, recurring charge, or expense schedule information.",
    "Allowance": "Allowance, contribution, concession, or reimbursement information.",
    "Security Deposit": "Security deposit, bank guarantee, letter of credit, or similar lease security.",
    "Options": "Renewal, extension, termination, expansion, contraction, purchase, or other lease option.",
    "Area": "Leased premises area, suite, floor, measurement, or applicable area period.",
}


def _full_attribute_path(relative_name: str) -> str:
    return f"{LEASE_ATTRIBUTE_PREFIX}{relative_name}"


def _attribute_output_name(relative_name: str, category: str) -> str:
    if category == "Restrictive Clauses" and relative_name != "Lease Status":
        return f"Lease_Abstraction.{relative_name}"
    return relative_name


def _attribute_leaf_name(relative_name: str) -> str:
    return re.sub(r"^.+?\.0\.", "", relative_name)


def _attribute_definition(relative_name: str) -> str:
    leaf_name = _attribute_leaf_name(relative_name)
    hints = REFERENCE_HINTS.get("by_suffix", {})
    guidance = hints.get(leaf_name, {}) if isinstance(hints, dict) else {}
    definition = guidance.get("hint") if isinstance(guidance, dict) else None
    if definition:
        return str(definition)
    parent = relative_name.split(".0.", 1)[0]
    parent_definition = ATTRIBUTE_DEFINITION_OVERRIDES.get(parent)
    if relative_name == parent and parent_definition:
        return parent_definition
    if parent_definition:
        return f"{leaf_name} associated with {parent.lower()} information."
    return f"Lease abstraction information for {leaf_name}."


ATTRIBUTE_CATALOG: list[dict[str, str]] = [
    {
        "category": category,
        "relative_name": relative_name,
        "full_path": _full_attribute_path(relative_name),
        "output_name": _attribute_output_name(relative_name, category),
        "definition": _attribute_definition(relative_name),
    }
    for category, relative_names in ATTRIBUTE_CATEGORIES.items()
    for relative_name in relative_names
]
ATTRIBUTE_BY_FULL_PATH = {item["full_path"]: item for item in ATTRIBUTE_CATALOG}


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


def _aws_client_kwargs() -> dict[str, str]:
    access_key = _config_value("AWS_ACCESS_KEY_ID")
    secret_key = _config_value("AWS_SECRET_ACCESS_KEY")
    session_token = _config_value("AWS_SESSION_TOKEN")
    if bool(access_key) != bool(secret_key):
        raise RuntimeError(
            "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must both be configured."
        )

    kwargs = {"region_name": _bedrock_region()}
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key
    if session_token:
        kwargs["aws_session_token"] = session_token
    return kwargs


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
        **_aws_client_kwargs(),
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


def complete_with_bedrock(
    prompt: str,
    *,
    output_json_schema: dict[str, Any] | None = None,
    output_schema_name: str = "structured_response",
    output_schema_description: str | None = None,
) -> str:
    if bedrock_client is None:
        raise RuntimeError("boto3 is required to run the legacy profiler LLM stage")
    request_started_at = time.perf_counter()
    resolved_model_id = bedrock_model_id
    request: dict[str, Any] = {
        "modelId": resolved_model_id,
        "messages": [
            {
                "role": "user",
                "content": [{"text": prompt}],
            }
        ],
    }
    if output_json_schema is not None:
        json_schema_definition = {
            "name": output_schema_name,
            "schema": json.dumps(
                output_json_schema,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }
        if output_schema_description:
            json_schema_definition["description"] = output_schema_description
        request["outputConfig"] = {
            "textFormat": {
                "type": "json_schema",
                "structure": {
                    "jsonSchema": json_schema_definition,
                },
            },
        }

    response = bedrock_client.converse(**request)
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

def markdown_conversion_model(
    file_path: str,
    file_id: str,
    *,
    filename: str | None = None,
) -> str:

    with open(file_path, "rb") as pdf_file:
        pdf_bytes = pdf_file.read()

    filename = filename or Path(file_path).name

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
    decoder = json.JSONDecoder()
    last_error: json.JSONDecodeError | None = None
    for match in re.finditer(r"\{", response):
        try:
            parsed, _ = decoder.raw_decode(response, match.start())
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(parsed, dict):
            return parsed
    if last_error is not None:
        print(f"JSON decoding error: {last_error}")
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


def normalize_s3_pdf_paths(input_paths: str | list[str] | tuple[str, ...]) -> list[str]:
    raw_paths = [input_paths] if isinstance(input_paths, str) else list(input_paths)
    paths: list[str] = []
    for raw_path in raw_paths:
        for value in str(raw_path).split(","):
            path = value.strip()
            if not path:
                continue
            parsed = urlparse(path)
            if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.strip("/"):
                raise ValueError(f"Invalid S3 PDF path: {path}")
            if path not in paths:
                paths.append(path)
    if not paths:
        raise ValueError("At least one S3 PDF path is required.")
    return paths


def s3_file_name(s3_path: str) -> str:
    return Path(unquote(urlparse(s3_path).path)).name


def download_s3_pdf(s3_path: str) -> Path:
    if boto3 is None:
        raise RuntimeError("boto3 is required to download PDFs from S3.")
    parsed = urlparse(s3_path)
    with tempfile.NamedTemporaryFile(
        prefix="lease_pdf_",
        suffix=".pdf",
        dir="/tmp",
        delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)
    try:
        boto3.client("s3", **_aws_client_kwargs()).download_file(
            parsed.netloc,
            unquote(parsed.path.lstrip("/")),
            str(temporary_path),
        )
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return temporary_path


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
    page_number: int,
    response_text: str,
    parsed_response: Any,
) -> Path:
    source_path = Path(source_file_path).resolve()
    output_path = source_path.with_name(
        f"{source_path.stem}_page_{page_number}_failed_attempt_{attempt}_attribute_info.json"
    )
    payload = {
        "attempt": attempt,
        "page_number": page_number,
        "raw_response": response_text,
        "parsed_response": parsed_response,
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_path


def _attribute_boolean_template() -> dict[str, dict[str, bool]]:
    return {
        category: {
            _full_attribute_path(relative_name): False
            for relative_name in relative_names
        }
        for category, relative_names in ATTRIBUTE_CATEGORIES.items()
    }


def _page_attribute_json_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "page_number": {"type": "integer"},
            "present_attributes": {
                "type": "array",
                "description": (
                    "Exact attribute paths whose information is present on the page. "
                    "Use an empty array when no listed attribute is present."
                ),
                "items": {
                    "type": "string",
                    "enum": [item["full_path"] for item in ATTRIBUTE_CATALOG],
                },
            },
        },
        "required": ["page_number", "present_attributes"],
        "additionalProperties": False,
    }


PAGE_ATTRIBUTE_JSON_SCHEMA = _page_attribute_json_schema()


def _normalize_page_classification(parsed: Any, page_number: int) -> dict[str, Any] | None:
    if not isinstance(parsed, dict):
        return None

    present_attributes = parsed.get("present_attributes")
    if isinstance(present_attributes, list):
        normalized = _attribute_boolean_template()
        for full_path in present_attributes:
            item = ATTRIBUTE_BY_FULL_PATH.get(str(full_path))
            if item is None:
                return None
            normalized[item["category"]][item["full_path"]] = True
        return {"page_number": page_number, **normalized}

    # Accept the previous full Boolean matrix while old responses are retried.
    raw_attributes = parsed.get("attributes", parsed)
    if not isinstance(raw_attributes, dict):
        return None

    normalized = _attribute_boolean_template()
    recognized = 0
    for category, expected_attributes in normalized.items():
        raw_category = raw_attributes.get(category)
        if not isinstance(raw_category, dict):
            continue
        for full_path in expected_attributes:
            if full_path not in raw_category:
                continue
            raw_value = raw_category[full_path]
            if isinstance(raw_value, str):
                value = raw_value.strip().lower() in {"true", "yes", "1", "present"}
            else:
                value = bool(raw_value)
            expected_attributes[full_path] = value
            recognized += 1
    if recognized != len(ATTRIBUTE_CATALOG):
        return None
    return {"page_number": page_number, **normalized}


def extract_attribute_info_for_page(
    page_number: int,
    page_text: str,
) -> tuple[dict[str, Any] | None, str, Any]:
    attribute_reference = [
        {
            "category": item["category"],
            "attribute_path": item["full_path"],
            "definition": item["definition"],
        }
        for item in ATTRIBUTE_CATALOG
    ]
    prompt = f"""
Classify the lease information present on PAGE {page_number} only.

Return an attribute path in present_attributes when this page contains any
information relevant to it. Relevant context includes a value, definition,
obligation, condition, exception, amendment, table row, address component, or
cross-reference with substantive attribute information.

Be recall-oriented: when information is related to an attribute, include its path.
Do not require the exact attribute label to appear. Do not infer content that is
not supported by this page. Set page_number to {page_number}. Return an empty
present_attributes array only when none of the listed attributes is relevant.
The response is constrained by the supplied JSON Schema.

ATTRIBUTE REFERENCE:
{json.dumps(attribute_reference, ensure_ascii=False, separators=(",", ":"))}

PAGE {page_number} CONTENT:
{page_text}
    """

    response_text = complete_with_bedrock(
        prompt,
        output_json_schema=PAGE_ATTRIBUTE_JSON_SCHEMA,
        output_schema_name="lease_page_attribute_classification",
        output_schema_description=(
            "Exact lease attribute paths present on one document page."
        ),
    )
    response_preview = response_text.strip()
    parsed = extract_json_from_response(response_preview)
    return _normalize_page_classification(parsed, page_number), response_preview, parsed


def extract_attribute_info_with_retry(
    page_number: int,
    page_text: str,
    source_file_path: str,
    *,
    max_attempts: int = ATTRIBUTE_INFO_RETRY_ATTEMPTS,
) -> dict[str, Any]:
    for attempt in range(1, max_attempts + 1):
        result, response_text, parsed_response = extract_attribute_info_for_page(
            page_number,
            page_text,
        )
        if result is not None:
            return result
        failed_path = save_failed_attribute_info_attempt(
            source_file_path,
            attempt,
            page_number,
            response_text,
            parsed_response,
        )
        print(
            f"attribute classification invalid for page {page_number} "
            f"on attempt {attempt}/{max_attempts}"
        )
        print(f"saved failed attribute_info attempt to: {failed_path}")
    raise RuntimeError(
        f"Bedrock did not return the complete attribute matrix for page {page_number} "
        f"after {max_attempts} attempts."
    )


def _page_headings(page_text: str) -> list[str]:
    return [
        title.strip()
        for _, title in re.findall(r"^(#{1,6})\s+(.+)$", page_text, re.MULTILINE)
        if title.strip()
    ]


def build_attribute_info_from_page_classifications(
    page_classifications: list[dict[str, Any]],
    pages: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    pages_by_number = {
        int(page["page_number"]): str(page.get("text") or "")
        for page in pages
    }
    present_pages: dict[str, set[int]] = {
        item["full_path"]: set() for item in ATTRIBUTE_CATALOG
    }
    for classification in page_classifications:
        page_number = int(classification["page_number"])
        categories = classification.get("attributes", classification)
        for category_values in categories.values():
            if not isinstance(category_values, dict):
                continue
            for full_path, is_present in category_values.items():
                if full_path in present_pages and bool(is_present):
                    present_pages[full_path].add(page_number)

    attribute_info: dict[str, Any] = {}
    attributes_present: list[str] = []
    for item in ATTRIBUTE_CATALOG:
        page_numbers = sorted(present_pages[item["full_path"]])
        headings: list[str] = []
        for page_number in page_numbers:
            for heading in _page_headings(pages_by_number.get(page_number, "")):
                if heading not in headings:
                    headings.append(heading)
        output_name = item["output_name"]
        details = {
            "definition": item["definition"],
            "category": item["category"],
            "attribute_path": item["full_path"],
            "is_attribute_present_file": bool(page_numbers),
            "page_number_range": page_numbers,
            "might_be_present_under": headings,
        }
        attribute_info[output_name] = details
        if page_numbers:
            attributes_present.append(output_name)

    return {
        "attributes_present": attributes_present,
        "total_attributes": len(ATTRIBUTE_CATALOG),
        "present_count": len(attributes_present),
        "absent_count": len(ATTRIBUTE_CATALOG) - len(attributes_present),
    }, normalize_attribute_info_payload({"attribute_info": attribute_info})["attribute_info"]

def build_structured_json(
    markdown_text: str,
    tables: list[dict[str, Any]],
    diagnostic_path: str,
    filename: str,
    attribute_list: list[str],
    data_format: str,
    ocr_engine: str,
    *,
    file_size_kb: float,
    source_s3_path: str | None,
) -> dict[str, Any]:
    max_workers = max(1, ATTRIBUTE_INFO_MAX_WORKERS)
    now = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    pages_text_list = extract_pages_text_list(markdown_text)
    page_entries = markdown_pages(markdown_text)
    author_info = normalize_pdf_info_payload(extract_pdf_info(markdown_text) or {})
    ext_str_info = time.time()
    structure_info, _, page_count = extract_structure_info_from_markdown(markdown_text)
    print(f"Text extraction took {round(time.time() - ext_str_info, 2)} seconds")
    page_classifications: list[dict[str, Any]] = []
    worker_count = min(len(page_entries), max_workers) if page_entries else 1
    if page_entries:
        print(
            f"Classifying {len(page_entries)} pages in parallel "
            f"(max_workers={worker_count})"
        )
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_page = {
                executor.submit(
                    extract_attribute_info_with_retry,
                    page["page_number"],
                    page["text"],
                    diagnostic_path,
                ): page["page_number"]
                for page in page_entries
            }
            for future in as_completed(future_to_page):
                page_number = future_to_page[future]
                page_classifications.append(future.result())
                print(f"Completed attribute classification for page {page_number}")
    page_classifications.sort(key=lambda item: int(item["page_number"]))
    attribute_info_summary, attribute_info = build_attribute_info_from_page_classifications(
        page_classifications,
        page_entries,
    )

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
            "origin": "s3" if source_s3_path else "local",
            "source_name": filename,
            "source_url": source_s3_path,
            "mime_type": "application/pdf",
            "extraction_tool": "LLM-assisted + OCR where needed",
            "ingestion_timestamp": now
        },
        "authoring_info": author_info.get("authoring_info", {}),
        "lease_document_info": author_info.get("lease_document_info", {}),
        "file_stats": {
            "file_size_kb": round(file_size_kb, 2),
            "page_count": page_count,
            "word_count": word_count,
            "table_count": len(tables),
            "language_detected": "English",
            "ocr_applied": ocr_engine != "none",
            "ocr_engine": ocr_engine,
        },
        "structure_info": structure_info,
        "attribute_info_summary": attribute_info_summary,
        "attribute_info": attribute_info,
        "attribute_page_classifications": page_classifications,
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
    source_filename: str | None = None,
) -> dict[str, Any]:
    start_time = time.time()
    reset_run_llm_metrics()
    markdown_text = ""
    tables: list[dict[str, Any]] = []
    text_parts: list[dict[str, Any]] = []
    data_format = is_image_pdf(file_path)
    ocr_engine = "modal"
    current_stage = "markdown_conversion"
    print(f"Profiler started for file: {file_path}")
    print(f"Detected data format: {data_format}; parser/ocr engine: {ocr_engine}")
    print("Markdown will be persisted to MongoDB after parsing.")

    try:
        print(f"Parsing started with Modal markdown parser: {file_path}")
        markdown_response_text = markdown_conversion_model(
            file_path,
            file_id,
            filename=source_filename,
        )
        markdown_payload = parse_markdown_response(markdown_response_text)
        markdown_text = markdown_payload["markdown_text"]
        print(f"Parsing completed for file: {file_path}")

        current_stage = "content_split"
        print("Splitting parsed markdown into tables and text parts.")
        tables, text_parts = split_markdown_content(markdown_text)
        print(f"{len(tables)} tables extracted.")
        print(f"{len(text_parts)} text parts extracted.")

        processing_time_ms = int((time.time() - start_time) * 1000)
        return {
            "markdown_text": markdown_text,
            "data_format": data_format,
            "profiler_run_metrics": build_run_metrics(processing_time_ms),
            "table_count": len(tables),
            "text_part_count": len(text_parts),
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
    input_path: str | Path | list[str | Path] | tuple[str | Path, ...],
    markdown_output_dir: str | Path | None = None,
    lease_id: str | None = None,
    source_s3_paths: dict[str, str] | None = None,
) -> dict[str, Any]:
    start_time = time.time()
    lease_id = lease_id or configured_lease_id() or new_lease_id()
    raw_paths = [input_path] if isinstance(input_path, (str, Path)) else list(input_path)
    pdf_paths = [Path(path).resolve() for path in raw_paths]
    if not pdf_paths:
        raise ValueError("At least one local PDF path is required.")
    for pdf_path in pdf_paths:
        if not pdf_path.is_file():
            raise FileNotFoundError(f"Local PDF not found: {pdf_path}")
        if pdf_path.suffix.lower() != ".pdf":
            raise ValueError(f"Local input must be a PDF: {pdf_path}")
    source_s3_paths = {
        str(Path(path).resolve()): value
        for path, value in (source_s3_paths or {}).items()
    }

    print(f"Profiler package run started: {len(pdf_paths)} local PDF file(s)")
    print(f"Parsing stage started: {len(pdf_paths)} PDF document(s)")
    parsed_by_name: dict[str, dict[str, Any]] = {}
    for index, local_path in enumerate(pdf_paths, start=1):
        source_s3_path = source_s3_paths.get(str(local_path))
        file_name = (
            s3_file_name(source_s3_path)
            if source_s3_path
            else local_path.name
        )
        if file_name in parsed_by_name:
            raise ValueError(f"Duplicate PDF filename in one lease package: {file_name}")
        print(f"Preparing document [{index}/{len(pdf_paths)}]: {local_path}")
        file_hash = compute_file_hash(str(local_path))
        file_id = f"file_{file_hash[:16]}"
        file_size_kb = local_path.stat().st_size / 1024
        parsed = parse_document(
            str(local_path),
            file_id=file_id,
            source_filename=file_name,
        )

        page_count = len(extract_pages_text_list(parsed["markdown_text"]))
        pdf_type = "scanned" if parsed["data_format"] == "Image PDF" else "typed"
        persist_markdown(
            lease_id=lease_id,
            file_id=file_id,
            file_name=file_name,
            source_s3_path=source_s3_path,
            markdown_text=parsed["markdown_text"],
            page_count=page_count,
            pdf_type=pdf_type,
        )
        parsed_by_name[file_name] = {
            "file_name": file_name,
            "file_id": file_id,
            "file_path": str(local_path),
            "source_s3_path": source_s3_path,
            "markdown_text": parsed["markdown_text"],
            "data_format": parsed["data_format"],
            "file_size_kb": file_size_kb,
            "profiler_run_metrics": parsed["profiler_run_metrics"],
            "table_count": parsed["table_count"],
            "text_part_count": parsed["text_part_count"],
        }
    print(
        "Parsing stage completed: "
        f"{len(parsed_by_name)} Markdown document(s) stored in lease_markdown"
    )

    print("Profiling stage started: building one profiler object per PDF")
    profiler_results_dir = (
        Path(markdown_output_dir).parent / "profiler_results"
        if markdown_output_dir is not None
        else PROFILER_RESULTS_DIR
    )
    profiler_results_dir.mkdir(parents=True, exist_ok=True)
    profiler_documents: list[dict[str, Any]] = []
    for index, local_path in enumerate(pdf_paths, start=1):
        source_s3_path = source_s3_paths.get(str(local_path))
        file_name = (
            s3_file_name(source_s3_path)
            if source_s3_path
            else local_path.name
        )
        parsed = parsed_by_name[file_name]
        tables, _ = split_markdown_content(parsed["markdown_text"])
        diagnostic_path = profiler_results_dir / file_name
        profiler_document = build_structured_json(
            parsed["markdown_text"],
            tables,
            str(diagnostic_path),
            file_name,
            LEASE_ATTRIBUTE_LIST,
            parsed["data_format"],
            "modal",
            file_size_kb=parsed["file_size_kb"],
            source_s3_path=source_s3_path,
        )
        profiler_document["file_id"] = parsed["file_id"]
        profiler_document["profiler_run_metrics"] = parsed["profiler_run_metrics"]
        metadata_path = save_metadata_json(
            profiler_document,
            str(diagnostic_path),
            output_dir=profiler_results_dir,
        )
        print(f"Profiler object completed [{index}/{len(pdf_paths)}]: {metadata_path}")
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
        parsed = parsed_by_name[document["file_name"]]
        parsed_documents.append(
            {
                **document,
                "file_id": parsed["file_id"],
                "file_path": parsed["file_path"],
                "source_s3_path": parsed["source_s3_path"],
                "profiler_run_metrics": parsed["profiler_run_metrics"],
                "table_count": parsed["table_count"],
                "text_part_count": parsed["text_part_count"],
            }
        )

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
    file_path: str | Path | list[str | Path],
    file_id: str = "file_123",
    markdown_output_dir: str | Path | None = None,
    lease_id: str | None = None,
    source_s3_paths: dict[str, str] | None = None,
) -> dict[str, Any]:
    package_result = run_package(
        file_path,
        markdown_output_dir=markdown_output_dir,
        lease_id=lease_id,
        source_s3_paths=source_s3_paths,
    )
    parsed = package_result["parsed_documents"][0]
    return {**package_result, **parsed, "file_id": file_id}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run lease profiler on local PDF files.")
    parser.add_argument(
        "pdf_paths",
        nargs="+",
        help="One or more local PDF paths.",
    )
    parser.add_argument(
        "--lease-id",
        default=None,
        help="Lease identifier shared by all MongoDB records.",
    )
    args = parser.parse_args()
    main(args.pdf_paths, lease_id=args.lease_id)

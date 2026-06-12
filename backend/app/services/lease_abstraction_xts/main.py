"""
Usage:
    python main_clean.py --lease <LEASE_ID>
    python main_clean.py --lease-dir input_pdfs/<LEASE_ID>
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any
import openpyxl
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from dotenv import load_dotenv
from google import genai
from google.genai import types
from openai import OpenAI
from pdf_document_loader import load_document_body, load_document_text

GEMINI_MODEL = ""
GEMINI_MODEL_FAST = "gemini-3.1-flash-lite"
GEMINI_MODEL_QUALITY = "gemini-3.5-flash"
OPENAI_MODEL_CONTACTS = "gpt-5-mini"
OPENAI_MODEL_EXPENSES = "gpt-5-mini"
USE_HYBRID_MODELS = True
INPUT_PRICE_PER_1M = 1.50
OUTPUT_PRICE_PER_1M = 9.0
INPUT_PRICE_PER_1M_LITE = 0.25
OUTPUT_PRICE_PER_1M_LITE = 1.50
INPUT_PRICE_PER_1M_GPT5_MINI = 0.25
OUTPUT_PRICE_PER_1M_GPT5_MINI = 2.00
INPUT_PRICE_PER_1M_OPENAI_MINI = 0.40
OUTPUT_PRICE_PER_1M_OPENAI_MINI = 1.60
DEFAULT_MAX_OUTPUT_TOKENS = 30000
EXPENSES_MAX_OUTPUT_TOKENS = 32768
CONTACTS_MAX_OUTPUT_TOKENS = 32768
CLAUSE_MAX_OUTPUT_TOKENS = 65536

ROOT = Path(__file__).resolve().parent
UNIQUE_ATTRS_PATH = ROOT / "config" / "attributes_unique.json"
REPEATABLE_SLOTS_PATH = ROOT / "config" / "attributes_repeatable_slots.json"
FIELD_HINTS_PATH = ROOT / "config" / "field_hints.json"
OUTPUT_DIR = ROOT / "output"
INPUT_PDFS_DIR = ROOT / "input_pdfs"
RUNS_LOG_PATH = ROOT / "runs_log.txt"

LEASE_DQC_ATTRS = ("Lease_DQC.document_type", "Lease_DQC.filename", "Lease_DQC.full_path_filename", "Lease_DQC.matched_keywords", "Lease_DQC.mode")
_REPEATABLE_SLOT_RE = re.compile(r"^(Lease_catalyst\.Lease_Abstraction\.(?:Area|Contact Identification|Options|Expenses|Security Deposit|Allowance))\.(\d+)\.(.+)$")
_DOC_DATE_RE = re.compile(r"dd_\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", re.IGNORECASE)
_MONTHS = {"jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12}

NOT_FOUND = "Not found"
LEASE_SILENT = "Lease is silent"
NA_VALUE = "N/A"
NUM_PASSES = 4

MAX_DOCUMENT_TEXT_CHARS = 1_500_000
OUTPUT_COLUMNS = ["Attribute_name", "Value", "Confidence Score", "Context", "Page Number"]

# ---------------------------------------------------------------------------
# System instructions
# ---------------------------------------------------------------------------

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "extractions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "attribute_name":   {"type": "string"},
                    "value":            {"type": "string"},
                    "confidence_score": {"type": "number"},
                    "context":          {"type": "string"},
                    "page_number":      {"type": "integer"},
                },
                "required": ["attribute_name", "value", "confidence_score", "context"],
            },
        }
    },
    "required": ["extractions"],
}

_SYSTEM_INTRO = (
    "You are an expert commercial lease / licence abstraction specialist with deep knowledge of "
    "real estate law across UAE, UK, US, Singapore, Hong Kong, Australia, and other jurisdictions."
)

_SYSTEM_BASE = f"""{_SYSTEM_INTRO}

STRICT EXTRACTION RULES:
1. Read the ENTIRE document: cover page, recitals, definitions, ALL clauses, ALL schedules, Exhibit A/B/C, annexures, rules and regulations, and signature blocks.
2. Return EXACTLY one JSON object per attribute listed — same attribute_name spelling, no skips.
3. Extract ONLY from the document. Do not invent or infer facts not present.

ANTI-PUNT RULE (CRITICAL):
4. NEVER return a reference as a value. These are INVALID values:
   - "Refer to Exhibit A", "See Schedule 1", "As per Clause 5", "As stated in agreement"
   - "To be confirmed", "As agreed", "Per attached", "As defined in"
   If you find a reference like this — GO TO that exhibit/schedule/clause and extract the ACTUAL value.
   If you cannot find the actual value after exhaustive search → use "{NOT_FOUND}".

EMPTY VALUE CONVENTIONS:
5. "{NA_VALUE}" — field is not applicable or not present for optional/contact fields.
6. "{LEASE_SILENT}" — ONLY for clause/legal topics the agreement genuinely does not address anywhere. Search ALL pages and annexures before using this.
7. "{NOT_FOUND}" — ONLY when a factual field (date, area, name, amount) cannot be found after exhaustive search. This should be rare.
8. Do NOT use "{LEASE_SILENT}" for contact address lines, zip codes, phone numbers, or numeric fields — use "{NA_VALUE}".

page_number (REQUIRED when value is found):
   - "1-based" means the first sheet of the PDF is page 1 (not page 0). Cover page may be page 1, but most fields are on later pages — use the page where THAT value or clause text actually appears.
   - Read the full PDF. Return the integer page where the supporting sentence/table row for THIS attribute is located.
   - WRONG: returning 1 for every field. WRONG: guessing. RIGHT: different page numbers for fields found on different pages.
   - In extracted-text mode, use the nearest "===== PAGE N =====" marker above the quote you placed in context.
   - Omit or null only for "{NOT_FOUND}" / "{NA_VALUE}" / "{LEASE_SILENT}".

confidence_score (BE STRICT — pipeline recomputes; do not inflate):
   - 85-100 ONLY when context contains clear verbatim or near-verbatim support for the value.
   - 65-84 when clearly stated but paraphrased in context.
   - 40-64 when indirect or partial support in context.
   - Below 40 when context does not support the value or you are uncertain. When in doubt, score LOWER.
   - 0 for "{NOT_FOUND}", "{NA_VALUE}", or "{LEASE_SILENT}".

Output valid JSON only. No markdown, no explanation."""

_PASS_SYSTEM_RULES: dict[str, str] = {
    "core_full": f"""
PASS FOCUS — core flat fields, dates, Options, Security Deposit, Allowance:
- Dates: US leases (US state/country) use MM/DD/YYYY. Other jurisdictions prefer dd/mm/yyyy. context MUST quote the exact sentence. Effective Date ≠ Execution Date when both exist. On renewals/amendments/addenda: Original Commencement/Expiration = prior term only; Current = this document's term. On assignment/addendum: Term Duration = N/A unless this document states a new term length (do not copy original 7-year recital).
- Security Deposit: search License Details, Exhibit A, deposit section for Type (Cash/LC) and Currency (USD, etc.).
- Areas/amounts: number only in numeric fields; unit in UOM field.
- Area Type = unit/premises category from the license details table (e.g. Commercial Bank) — NOT generic zone labels unless that exact phrase is the table Type.
- Property Postal code = postcode, ZIP, or P.O. Box number for the leased premises (digits/code only). Tenant-only P.O. Box belongs in Contact Identification, not property Postal code.
- Repeatable groups in this pass: one items[] entry per distinct row (.0, .1, .2+).
""".strip(),
    "repeatables_contacts_area": f"""
PASS FOCUS — Contact Identification and Area repeatables:
- CORE PARTIES ONLY: Landlord/Lessor, Tenant/Lessee, Payment Contact, Notice Copy for those parties. Same entity may appear multiple times with different Contact Type (e.g. Lessor, Notice Copy, Payment Contact) — duplicate address OK.
- Do NOT extract contractors, utilities, property managers, county attorneys, exhibit vendors, or brokers unless they are the named Landlord or Tenant notice party.
- ONE items[] object per contact = ALL fields for that party in ONE object (Street, City, Zip together). NEVER put Street, City, Zip in separate items.
- Slot order: .0 primary Landlord/Lessor → .1 primary Tenant/Lessee → .2+ additional Notice Copy or Payment Contact roles for those core parties only.
- Area: one item per distinct premises/suite row.
""".strip(),
    "repeatables_expenses": f"""
PASS FOCUS — Expenses repeatable group:
- One items[] entry per rent-schedule year/period with a stated dollar amount (including $0.00).
- Do NOT create expense items for narrative-only charges (utilities, personal property tax estimates) with no amount — those belong in clause summaries.
- Rent escalation: each lease year with ANNUAL/MONTHLY = separate item. CAM/insurance/tax only when amount is stated (0.00 is valid).
""".strip(),
    "clauses": f"""
PASS FOCUS — clause and legal summary fields:
- Provide a detailed factual abstraction (rights, obligations, notice periods, amounts, conditions, exceptions) — not a one-line summary.
- Only use "{LEASE_SILENT}" if the topic is truly absent from ALL pages and annexures.
- context = verbatim supporting quote from the clause.
""".strip(),
}

def _system_instruction_for_pass(pass_id: str) -> str:
    """Base rules on every call; pass-specific rules only where relevant."""
    extra = _PASS_SYSTEM_RULES.get(pass_id, "").strip()
    if not extra:
        return _SYSTEM_BASE
    return f"{_SYSTEM_BASE}\n\n{extra}"

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment & API keys
# ---------------------------------------------------------------------------

def load_env() -> None:
    load_dotenv(ROOT / ".env")

def get_api_key() -> str:
    load_env()
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise EnvironmentError("Set GEMINI_API_KEY in .env")
    return key

def get_openai_api_key() -> str:
    load_env()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise EnvironmentError("Set OPENAI_API_KEY in .env")
    return key

def _openai_client() -> OpenAI:
    return OpenAI(api_key=get_openai_api_key())

@dataclass
# ---------------------------------------------------------------------------
# Extraction plan & attribute config
# ---------------------------------------------------------------------------

class ExtractionPlan:
    label: str
    unique_names: list[str]
    repeatable_groups: dict[str, dict[str, Any]] = field(default_factory=dict)

def load_extraction_plan() -> ExtractionPlan:
    """Master plan: unique attrs + repeatable groups from config/."""
    return _load_master_plan()

def _load_repeatable_config() -> dict[str, dict[str, Any]]:
    if not REPEATABLE_SLOTS_PATH.is_file():
        raise FileNotFoundError(f"Missing {REPEATABLE_SLOTS_PATH}.")
    rep_data = json.loads(REPEATABLE_SLOTS_PATH.read_text(encoding="utf-8"))
    rep_groups: dict[str, Any] = rep_data.get("repeatable_groups") or {}
    if not isinstance(rep_groups, dict):
        raise ValueError("attributes_repeatable_slots.json repeatable_groups must be an object")
    return rep_groups

def _load_master_plan() -> ExtractionPlan:
    if not UNIQUE_ATTRS_PATH.is_file():
        raise FileNotFoundError(f"Missing {UNIQUE_ATTRS_PATH}.")
    unique_data = json.loads(UNIQUE_ATTRS_PATH.read_text(encoding="utf-8"))
    unique_names = unique_data.get("attributes") or []
    if not isinstance(unique_names, list) or not unique_names:
        raise ValueError("attributes_unique.json has no attributes[]")
    rep_groups = _load_repeatable_config()
    return ExtractionPlan(
        label="master (compact)",
        unique_names=[str(n).strip() for n in unique_names if str(n).strip()],
        repeatable_groups=rep_groups,
    )

def _suffix_from_slot_pattern(pat: str) -> str:
    if "{i}." in pat:
        return pat.split("{i}.", 1)[1]
    return pat.split(".")[-1]

def _canonical_slot_name(pat: str, slot: int) -> str:
    return pat.replace("{i}", str(slot))

def _group_cap(g: dict[str, Any]) -> int:
    indices = g.get("slot_indices") or []
    return len(indices) if indices else 0

def _slot_has_data(by_name: dict[str, dict], attrs_per_slot: list[str], slot: int) -> bool:
    for pat in attrs_per_slot:
        row = by_name.get(_canonical_slot_name(str(pat), slot))
        if row and not is_empty_value(row.get("value", "")):
            return True
    return False

def _rows_for_output_slots(
    plan: ExtractionPlan,
    by_name: dict[str, dict],
) -> list[dict]:
    rows: list[dict] = []
    for name in plan.unique_names:
        rows.append(
            by_name.get(name)
            or {
                "attribute_name": name,
                "value": NOT_FOUND,
                "confidence_score": 0.0,
                "context": "",
                "page_number": "",
            }
        )
    for group_name in sorted(plan.repeatable_groups.keys()):
        g = plan.repeatable_groups[group_name]
        attrs_per_slot = g.get("attributes_per_slot") or []
        cap = _group_cap(g)
        slots: set[int] = set()
        for slot in range(cap):
            if _slot_has_data(by_name, attrs_per_slot, slot):
                slots.add(slot)
        for slot in sorted(slots):
            for pat in attrs_per_slot:
                attr = _canonical_slot_name(str(pat), slot)
                row = by_name.get(attr)
                if row:
                    rows.append(row)
    return rows

def is_empty_value(value: str) -> bool:
    v = value.strip().lower()
    if not v:
        return True
    if v == NOT_FOUND.lower():
        return True
    if v in ("n/a", "na"):
        return True
    return v.startswith("lease is silent")

_field_hints_cache: dict | None = None

def _load_field_hints() -> dict:
    global _field_hints_cache
    if _field_hints_cache is None:
        if FIELD_HINTS_PATH.is_file():
            _field_hints_cache = json.loads(FIELD_HINTS_PATH.read_text(encoding="utf-8"))
        else:
            _field_hints_cache = {}
    return _field_hints_cache

def _field_suffix(attribute_name: str) -> str:
    base = attribute_name.split("\n")[0].strip()
    return base.split(".")[-1].strip()

def _hint_for_attribute(attribute_name: str) -> str:
    """Structured hint per attribute for extraction prompts."""
    hints     = _load_field_hints()
    short     = _field_suffix(attribute_name)
    by_suffix = hints.get("by_suffix") or {}

    parts: list[str] = [short]

    entry = by_suffix.get(short)
    clause_attr = (
        ".Clauses." in attribute_name
        or "Lease_Abstraction.Lease_Abstraction." in attribute_name
    )
    if entry:
        if isinstance(entry, dict):
            if entry.get("hint"):
                parts.append(f"What: {entry['hint']}")
            if entry.get("look_in"):
                parts.append(f"Look in: {entry['look_in']}")
            if entry.get("format"):
                if clause_attr:
                    clause_fmt = (hints.get("clause_value_format") or entry["format"]).strip()
                    parts.append(f"Format: {clause_fmt}")
                else:
                    parts.append(f"Format: {entry['format']}")
            if entry.get("example"):
                parts.append(f"Example: {entry['example']}")
            if entry.get("exclude"):
                parts.append(f"Exclude/Note: {entry['exclude']}")
        else:
            parts.append(str(entry))

    m = re.search(r"Contact Identification\.(\d+)\.", attribute_name)
    if m:
        contact_map = hints.get("contact_index") or {}
        idx_hint = contact_map.get(m.group(1), "")
        if idx_hint:
            parts.append(f"Party: {idx_hint}")

    if "Area." in attribute_name and short == "Type":
        area_note = (hints.get("area_group_note") or "").strip().replace("\n", " ")
        if area_note:
            parts.append(f"Area Type: {area_note}")

    return " | ".join(parts)

def _load_reference_punt_phrases() -> list[str]:
    hints = _load_field_hints()
    phrases = hints.get("reference_punt_phrases") or []
    if not phrases:
        phrases = ["refer to", "see exhibit", "see schedule", "as per exhibit", "as per schedule", "as per clause", "per clause", "as stated in", "as defined in", "as set out in", "as described in", "as specified in", "subject to clause", "in accordance with clause", "to be confirmed", "to be agreed", "tbd", "t.b.d", "per the agreement", "see above", "see below", "per attached", "per attachment"]
    return phrases

_PUNT_PHRASES: list[str] | None = None

def _is_reference_punt(value: str) -> bool:
    """Return True if the value is a reference to somewhere else rather than an actual value."""
    global _PUNT_PHRASES
    if _PUNT_PHRASES is None:
        _PUNT_PHRASES = _load_reference_punt_phrases()
    v = value.lower().strip()
    return any(phrase in v for phrase in _PUNT_PHRASES)

_DATE_MONTH_RE = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b",
    re.IGNORECASE,
)

def _looks_like_date_value(value: str) -> bool:
    """True when value plausibly contains a calendar date (post-extraction validation)."""
    v = value.strip()
    if not v:
        return False
    vl = v.lower()
    if re.search(r"\b(19|20)\d{2}\b", v):
        return True
    if _DATE_MONTH_RE.search(vl):
        return True
    if re.search(r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}", v):
        return True
    if re.search(r"\d{4}-\d{1,2}-\d{1,2}", v):
        return True
    if re.search(r"\d{1,2}(?:st|nd|rd|th)\s+(?:day\s+of\s+)?[A-Za-z]+", v, re.I):
        return True
    if re.search(r"\d{1,2}\s+[A-Za-z]{3,9},?\s+\d{2,4}", v):
        return True
    return False

def _validate_value(attribute_name: str, value: str) -> bool:
    """Return False if value fails validation (downgraded to Not found)."""
    if is_empty_value(value):
        return True

    if _is_reference_punt(value):
        log.debug("Validation FAIL (reference punt): %s = %r", attribute_name, value)
        return False

    short = _field_suffix(attribute_name).lower()

    NUMERIC_KEYWORDS = ("rent", "fee", "deposit", "area", "penalty", "charge", "amount", "price", "cost", "gross area", "net area", "monthly amount", "annual amount")
    if short.endswith("uom") or " uom" in short:
        return True

    if any(kw in short for kw in NUMERIC_KEYWORDS):
        if not re.search(r"\d", value):
            log.debug("Validation FAIL (no digits in numeric field): %s = %r", attribute_name, value)
            return False

    DATE_KEYWORDS = ["date", "expir", "commenc", "execution", "start date", "end date", "possession", "delivery"]
    if any(kw in short for kw in DATE_KEYWORDS):
        if not _looks_like_date_value(value):
            log.debug("Validation FAIL (no date pattern): %s = %r", attribute_name, value)
            return False

    DURATION_KEYWORDS = ["term duration", "duration"]
    if any(kw == short for kw in DURATION_KEYWORDS):
        has_number = bool(re.search(r"\d+|one|two|three|four|five|six|seven|eight|nine|ten", value.lower()))
        has_time_unit = bool(re.search(r"\b(year|month|week|day)s?\b", value.lower()))
        if not (has_number and has_time_unit):
            log.debug("Validation FAIL (no number+unit in duration): %s = %r", attribute_name, value)
            return False

    return True

CORE_REPEATABLE_GROUPS = ("Area", "Options", "Expenses", "Security Deposit", "Allowance")

def _suffixes_for_group(g: dict[str, Any]) -> list[str]:
    return [_suffix_from_slot_pattern(str(p)) for p in (g.get("attributes_per_slot") or [])]

_REPEATABLE_ITEM_META = (
    "    REQUIRED on every item object (even when some fields are N/A):\n"
    "    - context: verbatim lease quote (40+ characters) supporting this row's amounts/dates\n"
    "    - page_number: 1-based PDF page where that quote appears\n"
    "    - confidence_score: 0-100 per system rules\n"
)

def _build_group_item_schema(suffixes: list[str]) -> dict[str, Any]:
    props: dict[str, Any] = {s: {"type": "string"} for s in suffixes}
    props["confidence_score"] = {"type": "number"}
    props["context"] = {"type": "string"}
    props["page_number"] = {"type": "integer"}
    return {
        "type": "object",
        "properties": props,
        "required": ["context", "page_number", "confidence_score"],
    }

def _build_repeatable_groups_schema(groups: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Repeatable groups are optional schema keys; each may have items: []."""
    group_props: dict[str, Any] = {}
    for gname, gdef in groups.items():
        suffixes = _suffixes_for_group(gdef)
        group_props[gname] = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": _build_group_item_schema(suffixes),
                }
            },
        }
    return {"type": "object", "properties": group_props}

def _compact_core_schema(
    unique_names: list[str],
    repeatable_groups: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rep = {k: repeatable_groups[k] for k in CORE_REPEATABLE_GROUPS if k in repeatable_groups}
    props: dict[str, Any] = {
        "extractions": EXTRACTION_SCHEMA["properties"]["extractions"],
    }
    required = ["extractions"]
    if rep:
        props["repeatable_groups"] = _build_repeatable_groups_schema(rep)
        required.append("repeatable_groups")
    return {"type": "object", "properties": props, "required": required}

def _compact_contacts_schema(groups: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "repeatable_groups": _build_repeatable_groups_schema(groups),
        },
        "required": ["repeatable_groups"],
    }

_REPEATABLE_GROUP_HINT_KEYS: dict[str, str] = {"Area": "area_group_note", "Contact Identification": "contacts_group_note", "Options": "options_group_note", "Expenses": "expenses_group_note", "Security Deposit": "security_deposit_group_note", "Allowance": "allowance_group_note"}

def _format_group_note(note: str) -> str:
    if not note.strip():
        return ""
    return f"    {note.strip().replace(chr(10), chr(10) + '    ')}\n"

def _repeatable_instruction_extra(gname: str, hints: dict[str, Any]) -> str:
    """Universal .0/.1/.2+ rule plus group-specific note for every repeatable group."""
    parts: list[str] = []
    universal = (hints.get("repeatable_slots_note") or "").strip()
    if universal:
        parts.append(universal)
    specific_key = _REPEATABLE_GROUP_HINT_KEYS.get(gname)
    if specific_key:
        specific = (hints.get(specific_key) or "").strip()
        if specific:
            parts.append(specific)
    if not parts:
        return ""
    return _format_group_note("\n".join(parts))

def _repeatable_slots_prompt_block() -> str:
    hints = _load_field_hints()
    note = (hints.get("repeatable_slots_note") or "").strip()
    if not note:
        return ""
    return note + "\n\n"

def _build_compact_group_instructions(
    groups: dict[str, dict[str, Any]],
) -> str:
    hints = _load_field_hints()
    lines: list[str] = []
    for gname, gdef in groups.items():
        suffixes = _suffixes_for_group(gdef)
        cap = _group_cap(gdef)
        field_lines = "\n".join(f"    - {s}" for s in suffixes)
        extra = _repeatable_instruction_extra(gname, hints)
        lines.append(
            f"  {gname} (max {cap} items — map items[0]→{gname}.0, items[1]→{gname}.1, "
            f"items[2]→{gname}.2, …; include ALL rows/parties/charges found, not only .0/.1):\n"
            f"{field_lines}\n"
            f"{extra}"
            f"    Return under repeatable_groups.{gname}.items as an array of objects.\n"
            f"    Each object uses the field names above as keys, plus confidence_score, context, page_number.\n"
            f"{_REPEATABLE_ITEM_META}"
            f"    If none exist, return an empty items array []. Do NOT pad with empty objects."
        )
    return "\n".join(lines)

_DATE_FIELD_SUFFIXES = frozenset({
    "Effective Date",
    "Execution Date",
    "Original Commencement Date",
    "Rent Commencement Date",
    "Current Commencement Date",
    "Current Expiration Date",
    "Original Expiration Date",
    "Possession Date",
    "Delivery Date",
    "Start date",
    "End date",
})

def _date_fields_prompt_block(unique_names: list[str]) -> str:
    """Extra guidance when date flat fields are in a pass batch."""
    if not any(_field_suffix(n) in _DATE_FIELD_SUFFIXES for n in unique_names):
        return ""
    hints = _load_field_hints()
    pass_notes = hints.get("pass_notes") or {}
    parts: list[str] = []
    date_note = (hints.get("date_fields_note") or "").strip()
    renewal = (pass_notes.get("renewal_dates") or "").strip()
    if date_note:
        parts.append(date_note)
    if renewal:
        parts.append(renewal)
    if not parts:
        return ""
    return "\n".join(parts) + "\n\n"

def _clauses_prompt_block() -> str:
    hints = _load_field_hints()
    note = (hints.get("clauses_group_note") or "").strip()
    if not note:
        return ""
    return note + "\n\n"

def _property_address_prompt_block(unique_names: list[str]) -> str:
    """Extra Pass-1 emphasis when property address flat fields are in scope."""
    address_suffixes = frozenset({
        "Property name", "Street", "Street no", "Postal code", "City",
        "County", "State / province", "Country",
    })
    if not any(_field_suffix(n) in address_suffixes for n in unique_names):
        return ""
    hints = _load_field_hints()
    note = (hints.get("core_address_note") or "").strip()
    if not note:
        note = (
            "PROPERTY ADDRESS (this pass — flat fields only):\n"
            "- Postal code is REQUIRED when the document shows a postcode, ZIP, or P.O. Box for the "
            "licensed premises / mall / site. Return the code only (e.g. 60811), not the words "
            "'P.O. Box'.\n"
            "- Search cover page, recitals, premises description, and licensor address at the site.\n"
            "- Do not leave Postal code as N/A if a premises or licensor-at-site P.O. Box is visible."
        )
    return note + "\n\n"

def _build_compact_core_prompt(
    unique_names: list[str],
    repeatable_groups: dict[str, dict[str, Any]],
    label: str,
    *,
    anchor_block: str = "",
) -> str:
    rep = {k: repeatable_groups[k] for k in CORE_REPEATABLE_GROUPS if k in repeatable_groups}
    flat_lines = []
    for i, n in enumerate(unique_names, start=1):
        flat_lines.append(f"{i}. {n}\n   Guidance: {_hint_for_attribute(n)}")
    flat_block = "\n".join(flat_lines)
    address_block = _property_address_prompt_block(unique_names)
    date_block = _date_fields_prompt_block(unique_names)
    page_block = _page_number_prompt_block()
    if not rep:
        return (
            f"{label}\n{anchor_block}\n"
            f"{page_block}"
            f"{date_block}"
            f"{address_block}"
            f"Return JSON with extractions: exactly {len(unique_names)} flat attributes "
            f"(one object each). Focus on property, parties, and all date fields.\n\n"
            f"FLAT ATTRIBUTES:\n{flat_block}"
        )
    rep_block = _build_compact_group_instructions(rep)
    return (
        f"{label}\n{anchor_block}\n"
        f"{page_block}"
        f"{date_block}"
        f"{address_block}"
        f"Return JSON with:\n"
        f"1) extractions: exactly {len(unique_names)} flat attributes (one object each).\n"
        f"2) repeatable_groups: compact arrays — every distinct item in the lease (.0, .1, .2+).\n\n"
        f"{_repeatable_slots_prompt_block()}"
        f"REPEATABLE GROUPS:\n{rep_block}\n\n"
        f"FLAT ATTRIBUTES:\n{flat_block}"
    )

def _build_compact_repeatables_prompt(
    groups: dict[str, dict[str, Any]],
    label: str,
    *,
    anchor_block: str = "",
    focus_line: str = "",
) -> str:
    rep_block = _build_compact_group_instructions(groups)
    focus = (focus_line.strip() + "\n\n") if focus_line.strip() else ""
    return (
        f"{label}\n{anchor_block}\n"
        f"{_page_number_prompt_block()}"
        f"{focus}"
        f"Return JSON with repeatable_groups only.\n\n"
        f"{rep_block}"
    )

def _expense_anchor_date_lines(anchor_block: str) -> str:
    """Pull Current Commencement/Expiration from anchor block for expense alignment."""
    if not anchor_block:
        return ""
    lines: list[str] = []
    for suffix in ("Current Commencement Date", "Current Expiration Date", "Rent Commencement Date"):
        m = re.search(rf"^\s*{re.escape(suffix)}:\s*(.+)$", anchor_block, re.M)
        if m:
            val = m.group(1).strip()
            if val and val not in (NOT_FOUND, NA_VALUE):
                lines.append(f"  {suffix}: {val}")
    if not lines:
        return ""
    return (
        "\nANCHORED TERM DATES (from Pass 1 — Expenses.0 MUST align):\n"
        + "\n".join(lines)
        + "\n"
    )

def _build_compact_expenses_prompt(
    groups: dict[str, dict[str, Any]],
    label: str,
    *,
    anchor_block: str = "",
) -> str:
    hints = _load_field_hints()
    exp_note = (hints.get("expenses_group_note") or "").strip()
    anchor_dates = _expense_anchor_date_lines(anchor_block)
    focus = (
        "EXPENSES ONLY — this pass is only for repeatable_groups.Expenses:\n"
        + _repeatable_slots_prompt_block()
        + "- items[0] (Expenses.0) = CURRENT lease term / first lease year — NOT a prior or historical period.\n"
        + "- Expenses.0 Start date and End date MUST match Current Commencement Date and Current Expiration Date "
        "from Pass 1 when those are known (see CONFIRMED KEY FACTS / anchored dates below).\n"
        + "- One items[] entry per lease year or charge line ONLY when a dollar amount is stated (including $0.00).\n"
        + "- Do NOT create items for future lease years, options, or narrative charges without amounts.\n"
        + "- Recurring Additional Rent (CAM, taxes, insurance) = separate items only when an amount is stated.\n"
        + "- Rent Type must name the charge (Fixed Rent, Common Area Charges, Real Estate Taxes, etc.).\n"
        + "- context MUST quote the table row or clause sentence for that item.\n"
    )
    if anchor_dates:
        focus += anchor_dates
    if exp_note:
        focus += exp_note + "\n"
    return _build_compact_repeatables_prompt(
        groups, label, anchor_block=anchor_block, focus_line=focus,
    )

def _merge_compact_groups(
    by_name: dict[str, dict],
    groups: dict[str, dict[str, Any]],
    payload: dict[str, Any],
) -> None:
    rep = payload.get("repeatable_groups") or {}
    if not isinstance(rep, dict):
        return
    for gname, gdef in groups.items():
        block = rep.get(gname) or {}
        items = block.get("items") if isinstance(block, dict) else None
        if not isinstance(items, list):
            items = []
        cap = _group_cap(gdef)
        if len(items) > cap:
            log.warning("%s: model returned %s items; truncating to cap %s", gname, len(items), cap)
            items = items[:cap]
        attrs_per_slot = gdef.get("attributes_per_slot") or []
        suffixes = _suffixes_for_group(gdef)
        for slot_idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            meta_conf = float(item.get("confidence_score", 0) or 0)
            meta_ctx = str(item.get("context") or "")[:2000]
            meta_pg = _normalize_page_number(item.get("page_number"))
            for suffix, pat in zip(suffixes, attrs_per_slot):
                raw = item.get(suffix)
                val = NA_VALUE if raw is None or str(raw).strip() == "" else str(raw).strip()
                attr = _canonical_slot_name(str(pat), slot_idx)
                row = {
                    "attribute_name": attr,
                    "value": val,
                    "confidence_score": meta_conf,
                    "context": meta_ctx,
                    "page_number": meta_pg,
                }
                row = _normalize(row)
                by_name[attr] = row

def _unclosed_suffix(s: str) -> str | None:
    """Closing brackets needed to balance `s`, or None if `s` ends inside a string."""
    stack: list[str] = []
    in_str = False
    esc = False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch == "}":
            if stack and stack[-1] == "{":
                stack.pop()
        elif ch == "]":
            if stack and stack[-1] == "[":
                stack.pop()
    if in_str:
        return None
    return "".join("}" if c == "{" else "]" for c in reversed(stack))


def _loads_lenient(text: str) -> Any:
    """Parse JSON, salvaging complete entries when the model truncated its response.

    Gemini/OpenAI occasionally return JSON cut off mid-string (finish reason MAX_TOKENS or
    an intermittent malformed response). Rather than discarding the whole document, recover
    every complete top-level entry before the cut-off: trim back to the last full `}` and
    re-close the open brackets. Returns whatever parses; raises JSONDecodeError if nothing does.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    attempts = 0
    for end in range(len(text) - 1, 0, -1):
        if text[end] != "}":
            continue
        head = text[: end + 1]
        suffix = _unclosed_suffix(head)
        if suffix is None:
            continue
        try:
            return json.loads(head + suffix)
        except json.JSONDecodeError:
            attempts += 1
            if attempts > 100:
                break
            continue
    raise json.JSONDecodeError("unsalvageable truncated JSON", text, 0)


def _gemini_call_json(
    client: genai.Client,
    pdf_file: Any,
    prompt: str,
    schema: dict[str, Any],
    model: str,
    *,
    document_text: str | None = None,
    max_output_tokens: int = 20000,
    system_instruction: str | None = None,
    _retry: bool = True,
) -> tuple[dict[str, Any], dict[str, int]]:
    contents = [document_text, prompt] if document_text is not None else [pdf_file, prompt]
    resp = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction or _SYSTEM_BASE,
            response_mime_type="application/json",
            response_json_schema=schema,
            temperature=0.0,
            max_output_tokens=max_output_tokens,
        ),
    )
    usage = _usage_from_response(resp)
    text = (resp.text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text), usage
    except json.JSONDecodeError as e:
        # 1) Retry once with a larger output budget — truncation is often the model running
        #    out of tokens (thinking + output) before closing the JSON.
        if _retry:
            log.warning(
                "JSON parse failed (len=%s) — retrying [%s] with larger token budget", len(text), model,
            )
            return _gemini_call_json(
                client, pdf_file, prompt, schema, model,
                document_text=document_text,
                max_output_tokens=min(65536, int(max_output_tokens * 1.6)),
                system_instruction=system_instruction,
                _retry=False,
            )
        # 2) Still truncated — salvage the complete entries instead of failing the document.
        try:
            data = _loads_lenient(text)
            log.warning("JSON still truncated (len=%s) — salvaged partial result", len(text))
            return data, usage
        except json.JSONDecodeError:
            log.error("JSON parse failed (len=%s): %s", len(text), e)
            raise ValueError(f"Model returned truncated or invalid JSON ({e})") from e

def _split_into_passes_compact(
    plan: ExtractionPlan,
) -> list[tuple[str, str, dict[str, Any]]]:
    clause_names = [
        n for n in plan.unique_names
        if ".Clauses." in n or "Lease_Abstraction.Lease_Abstraction." in n
    ]
    clause_set = frozenset(clause_names)
    core_unique = [n for n in plan.unique_names if n not in clause_set]

    hints = _load_field_hints()
    pass_notes = hints.get("pass_notes") or {}
    titles = {
        "core_full": "Core fields, dates, Options, Security Deposit, and Allowance",
        "repeatables_contacts_area": "Contact Identification and Area",
        "repeatables_expenses": "Expenses (rent schedule and charges)",
        "clauses": "Clauses and legal terms",
    }
    buckets: list[tuple[str, str, dict[str, Any]]] = []
    all_rep = dict(plan.repeatable_groups)
    options_deposit_rep = {
        k: all_rep[k]
        for k in ("Security Deposit", "Allowance", "Options")
        if k in all_rep
    }
    contacts_area_rep = {
        k: all_rep[k]
        for k in ("Contact Identification", "Area")
        if k in all_rep
    }
    expenses_rep = {k: all_rep[k] for k in ("Expenses",) if k in all_rep}
    core_full_note = " ".join(
        p for p in (
            pass_notes.get("core", ""),
            pass_notes.get("renewal_dates", ""),
            pass_notes.get("options_deposit", ""),
        )
        if p
    ).strip()
    if core_unique or options_deposit_rep:
        buckets.append((
            "core_full",
            core_full_note or pass_notes.get("core_deposit_allowance", ""),
            {"unique": core_unique, "repeatable": options_deposit_rep},
        ))
    if contacts_area_rep:
        buckets.append((
            "repeatables_contacts_area",
            pass_notes.get("contacts_area", pass_notes.get("contacts", "")),
            {"repeatable": contacts_area_rep},
        ))
    if expenses_rep:
        buckets.append((
            "repeatables_expenses",
            pass_notes.get("expenses", pass_notes.get("expenses_options", "")),
            {"repeatable": expenses_rep},
        ))
    if clause_names:
        buckets.append((
            "clauses",
            pass_notes.get("clauses", ""),
            {"unique": clause_names, "repeatable": {}},
        ))
    n = len(buckets)
    return [
        (pid, f"Pass {i}/{n} — {titles.get(pid, pid)}. {note}".strip(), payload)
        for i, (pid, note, payload) in enumerate(buckets, start=1)
    ]

_ANCHOR_SUFFIXES = ("Landlord Name", "Tenant Name", "Property name", "City", "Country", "Original Commencement Date", "Original Expiration Date", "Current Commencement Date", "Current Expiration Date", "Rent Commencement Date", "Execution Date", "Term Duration", "Effective Date", "Unit/suite number")

def _build_anchor_block(by_name: dict[str, dict]) -> str:
    """Confirmed key facts injected into contacts/clauses pass prompts."""
    lines: list[str] = []
    for attr_name, row in by_name.items():
        val = row.get("value", "")
        if is_empty_value(val):
            continue
        suffix = _field_suffix(attr_name)
        if suffix in _ANCHOR_SUFFIXES:
            lines.append(f"  {suffix}: {val}")
    if not lines:
        return ""
    return "\nCONFIRMED KEY FACTS (already extracted — use these to anchor your search):\n" + "\n".join(lines) + "\n"

def _build_prompt(
    names: list[str],
    label: str,
    *,
    anchor_block: str = "",
) -> str:
    lines = []
    for i, n in enumerate(names, start=1):
        hint = _hint_for_attribute(n)
        lines.append(f"{i}. {n}\n   Guidance: {hint}")
    numbered = "\n".join(lines)

    address_block = _property_address_prompt_block(names)
    clause_block = _clauses_prompt_block()
    intro = (
        f"{label}\n"
        f"{anchor_block}\n"
        f"{clause_block}"
        f"{address_block}"
        f"You MUST return exactly {len(names)} extractions — one per attribute below.\n"
        f"Search the FULL document (all pages, all annexures, all schedules) before marking anything "
        f"{NOT_FOUND!r} or {LEASE_SILENT!r}.\n"
        f"NEVER return a reference like 'Refer to Exhibit A' as a value — find the actual value.\n"
    )
    intro += (
        "\npage_number: the PDF page (first page = 1) where THIS attribute's value appears. "
        "Use a different page per field when text is on different pages. "
        "Do NOT set 1 for every row. Omit if unknown.\n"
        "For clause attributes: value = detailed factual abstraction; context = verbatim lease quote.\n"
    )
    return intro + f"\nAttributes to extract:\n{numbered}"

_PAGE_MARKER_RE = re.compile(r"===== PAGE (\d+) =====", re.IGNORECASE)

def _page_number_prompt_block() -> str:
    return (
        "PAGE NUMBER: Return the actual PDF page (first page = 1) where each value appears. "
        "Most attributes are NOT on page 1 — scan schedules, clauses, and exhibits. "
        "Never default all rows to page 1. If using text with ===== PAGE N ===== markers, "
        "page_number must match the marker for the quote in context.\n\n"
    )

def _normalize_page_number(pg: Any) -> int | str:
    if pg in (None, "", 0, "0"):
        return ""
    try:
        n = int(pg)
        return n if n >= 1 else ""
    except (TypeError, ValueError):
        return ""

def _get_pdf_page_count(pdf_path: str) -> int:
    import fitz
    doc = fitz.open(pdf_path)
    n = len(doc)
    doc.close()
    return n

def _usage_from_response(resp: Any) -> dict[str, int]:
    u = getattr(resp, "usage_metadata", None)
    if not u:
        return {"input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "total_tokens": 0}
    inp = int(getattr(u, "prompt_token_count", 0) or 0)
    out = int(getattr(u, "candidates_token_count", 0) or 0)
    thinking = int(getattr(u, "thoughts_token_count", 0) or 0)
    total = int(getattr(u, "total_token_count", 0) or (inp + out + thinking))
    return {"input_tokens": inp, "output_tokens": out, "thinking_tokens": thinking, "total_tokens": total}

def _is_lite_model(model: str) -> bool:
    m = model.lower()
    return "lite" in m or "3.1-flash" in m

def _is_openai_model(model: str) -> bool:
    m = model.lower()
    return m.startswith("gpt-") or m.startswith("o1") or m.startswith("o3")

def _pricing_for_model(model: str) -> tuple[float, float]:
    m = model.lower()
    if _is_openai_model(model):
        if "gpt-5-mini" in m or "5-mini" in m:
            return INPUT_PRICE_PER_1M_GPT5_MINI, OUTPUT_PRICE_PER_1M_GPT5_MINI
        if "4.1-mini" in m or (m.startswith("gpt-4.1") and "mini" in m):
            return INPUT_PRICE_PER_1M_OPENAI_MINI, OUTPUT_PRICE_PER_1M_OPENAI_MINI
        return INPUT_PRICE_PER_1M_OPENAI_MINI, OUTPUT_PRICE_PER_1M_OPENAI_MINI
    if _is_lite_model(model):
        return INPUT_PRICE_PER_1M_LITE, OUTPUT_PRICE_PER_1M_LITE
    return INPUT_PRICE_PER_1M, OUTPUT_PRICE_PER_1M

def _estimate_cost_usd(
    input_tokens: int,
    output_tokens: int,
    thinking_tokens: int,
    *,
    model: str = "",
) -> float:
    inp_p, out_p = _pricing_for_model(model)
    billable_out = output_tokens + thinking_tokens
    return (input_tokens / 1_000_000 * inp_p) + (billable_out / 1_000_000 * out_p)

def _resolve_extraction_models() -> tuple[str | None, str, str, bool]:
    """
    Returns (single_override, fast_model, quality_model, hybrid_enabled).
    GEMINI_MODEL env or constant forces one model for every pass.
    """
    override = os.environ.get("GEMINI_MODEL", GEMINI_MODEL or "").strip() or None
    fast = os.environ.get("GEMINI_MODEL_FAST", GEMINI_MODEL_FAST).strip()
    quality = os.environ.get("GEMINI_MODEL_QUALITY", GEMINI_MODEL_QUALITY).strip()
    hybrid = USE_HYBRID_MODELS and not override
    return override, fast, quality, hybrid

def _openai_uses_completion_tokens_param(model: str) -> bool:
    m = model.lower()
    return "gpt-5" in m or m.startswith("o1") or m.startswith("o3")

def _openai_create_extra_kwargs(model: str, max_output_tokens: int) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if _openai_uses_completion_tokens_param(model):
        extra["max_completion_tokens"] = max_output_tokens
    else:
        extra["max_tokens"] = max_output_tokens
        extra["temperature"] = 0.0
    return extra

def _usage_from_openai(resp: Any) -> dict[str, int]:
    u = getattr(resp, "usage", None)
    if not u:
        return {"input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "total_tokens": 0}
    inp = int(getattr(u, "prompt_tokens", 0) or 0)
    out = int(getattr(u, "completion_tokens", 0) or 0)
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "thinking_tokens": 0,
        "total_tokens": inp + out,
    }

def _openai_call_json(
    client: OpenAI,
    prompt: str,
    schema: dict[str, Any],
    model: str,
    *,
    document_text: str,
    max_output_tokens: int = 20000,
    system_instruction: str | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    user_body = f"{document_text}\n\n{prompt}"
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_instruction or _SYSTEM_BASE},
            {"role": "user", "content": user_body},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "lease_extraction",
                "strict": False,
                "schema": schema,
            },
        },
        **_openai_create_extra_kwargs(model, max_output_tokens),
    )
    usage = _usage_from_openai(resp)
    text = (resp.choices[0].message.content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text), usage
    except json.JSONDecodeError as e:
        try:
            data = _loads_lenient(text)
            log.warning("OpenAI JSON truncated (len=%s) — salvaged partial result", len(text))
            return data, usage
        except json.JSONDecodeError:
            log.error("OpenAI JSON parse failed (len=%s): %s", len(text), e)
            raise ValueError(f"Model returned truncated or invalid JSON ({e})") from e

def _provider_for_pass(pass_id: str) -> str:
    if pass_id in ("repeatables_contacts_area", "repeatables_expenses"):
        return "openai"
    return "gemini"

def _model_for_pass(
    pass_id: str,
    *,
    override: str | None,
    fast: str,
    quality: str,
    hybrid: bool,
) -> tuple[str, str]:
    """Return (provider, model_slug) for a pipeline pass."""
    provider = _provider_for_pass(pass_id)
    if provider == "openai":
        if pass_id == "repeatables_expenses":
            model = os.environ.get("OPENAI_MODEL_EXPENSES", OPENAI_MODEL_EXPENSES).strip()
        else:
            model = os.environ.get("OPENAI_MODEL_CONTACTS", OPENAI_MODEL_CONTACTS).strip()
        return "openai", model or OPENAI_MODEL_CONTACTS
    if override:
        return "gemini", override
    if not hybrid:
        return "gemini", fast
    if pass_id == "core_full":
        return "gemini", quality
    return "gemini", fast

def _parse_extractions_json(text: str, *, label: str) -> list[dict]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        try:
            data = _loads_lenient(text)
            log.warning("%s: JSON truncated (len=%s) — salvaged partial result", label, len(text))
        except json.JSONDecodeError:
            log.error(
                "%s: invalid JSON (len=%s, error=%s). Tail: ...%s",
                label, len(text), e, text[-400:] if len(text) > 400 else text,
            )
            raise ValueError(
                f"{label}: model returned truncated or invalid JSON ({e}). "
                "Usually caused by too many long clause contexts in one response."
            ) from e
    items = data.get("extractions", [])
    if not isinstance(items, list):
        raise ValueError(f"{label}: invalid Gemini response — expected extractions list")
    return items

def _gemini_call(
    client: genai.Client,
    pdf_file: Any,
    names: list[str],
    label: str,
    model: str,
    *,
    anchor_block: str = "",
    document_text: str | None = None,
    max_output_tokens: int = 20000,
    system_instruction: str | None = None,
) -> tuple[list[dict], dict[str, int]]:
    prompt = _build_prompt(names, label, anchor_block=anchor_block)
    contents = [document_text, prompt] if document_text is not None else [pdf_file, prompt]
    resp = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction or _SYSTEM_BASE,
            response_mime_type="application/json",
            response_json_schema=EXTRACTION_SCHEMA,
            temperature=0.0,
            max_output_tokens=max_output_tokens,
        ),
    )
    usage = _usage_from_response(resp)
    text = (resp.text or "").strip()
    items = _parse_extractions_json(text, label=label)
    if len(items) < len(names):
        log.warning("Response has %s/%s extractions", len(items), len(names))
    return items, usage

def _normalize(item: dict) -> dict:
    name = str(item.get("attribute_name", "")).splitlines()[0].strip()
    val  = item.get("value")
    val  = NOT_FOUND if val is None or str(val).strip() == "" else str(val).strip()
    try:
        conf = float(item.get("confidence_score", 0))
    except (TypeError, ValueError):
        conf = 0.0
    ctx = str(item.get("context") or "")[:2000]
    pg_out = _normalize_page_number(item.get("page_number"))

    if not _validate_value(name, val):
        log.debug("Value failed validation → Not found: %s = %r", name, val)
        val  = NOT_FOUND
        conf = 0.0
        ctx  = ""
        pg_out = ""

    return {"attribute_name": name, "value": val, "confidence_score": conf,
            "context": ctx, "page_number": pg_out}

def _row_score(row: dict) -> float:
    """Prefer rows with real extracted values when merging passes (not LLM confidence)."""
    v = row.get("value", "").strip().lower()
    if not v or v == NOT_FOUND.lower():
        return 0.0
    if v.startswith(LEASE_SILENT.lower()):
        return 10.0
    return 100.0

def _merge_items(by_name: dict[str, dict], items: list[dict]) -> None:
    for item in items:
        row = _normalize(item)
        if not row["attribute_name"]:
            continue
        old = by_name.get(row["attribute_name"])
        if old is None or _row_score(row) > _row_score(old):
            by_name[row["attribute_name"]] = row


def _gapfill_unique_names(
    expected_names: list[str],
    by_name: dict[str, dict],
    *,
    label: str,
    provider: str,
    pass_model: str,
    pass_sys: str | None,
    client: genai.Client,
    pdf_file: Any,
    openai_client: OpenAI | None,
    document_text: str | None,
    anchor_block: str,
    max_rounds: int = 2,
) -> list[dict[str, int]]:
    """Re-request fixed (named) attributes that never came back — truncation gap-fill.

    When a pass truncates, salvage keeps the complete rows but the tail attributes are missing.
    Because the expected attribute names for a fixed pass (clauses / core terms) are known, we can
    re-ask the model for ONLY the missing ones — a small request that won't truncate — and merge
    them in. Repeatable groups are intentionally NOT gap-filled (their count is open-ended, so we
    can't tell "truncated away" from "doesn't exist"). Returns the usage of any extra calls made.
    """
    extra_usages: list[dict[str, int]] = []
    for round_no in range(1, max_rounds + 1):
        missing = [n for n in expected_names if n not in by_name]
        if not missing:
            break
        preview = ", ".join(missing[:8]) + ("…" if len(missing) > 8 else "")
        log.warning(
            "%s: gap-fill round %s — re-requesting %s missing attribute(s): %s",
            label, round_no, len(missing), preview,
        )
        gf_label = f"{label} (gap-fill {round_no})"
        try:
            if provider == "openai":
                if openai_client is None:
                    openai_client = _openai_client()
                gf_prompt = _build_prompt(missing, gf_label, anchor_block=anchor_block)
                data, usage = _openai_call_json(
                    openai_client, gf_prompt, EXTRACTION_SCHEMA, pass_model,
                    document_text=document_text,
                    max_output_tokens=CLAUSE_MAX_OUTPUT_TOKENS,
                    system_instruction=pass_sys,
                )
                items = data.get("extractions", []) if isinstance(data, dict) else []
            else:
                items, usage = _gemini_call(
                    client, pdf_file, missing, gf_label, pass_model,
                    anchor_block=anchor_block,
                    document_text=document_text,
                    max_output_tokens=CLAUSE_MAX_OUTPUT_TOKENS,
                    system_instruction=pass_sys,
                )
        except Exception as e:
            log.warning("%s: gap-fill round %s failed (%s) — keeping salvaged rows", label, round_no, e)
            break
        extra_usages.append({**usage, "model": pass_model, "provider": provider, "pass_id": gf_label})
        if not isinstance(items, list) or not items:
            log.warning("%s: gap-fill round %s returned nothing — stopping", label, round_no)
            break
        before = len(by_name)
        _merge_items(by_name, items)
        if len(by_name) == before:
            log.warning("%s: gap-fill round %s made no progress — stopping", label, round_no)
            break
    still_missing = [n for n in expected_names if n not in by_name]
    if still_missing:
        log.warning("%s: %s attribute(s) still missing after gap-fill", label, len(still_missing))
    return extra_usages

def _parse_dd_mm_yyyy(value: str) -> date | None:
    v = value.strip()
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", v)
    if not m:
        return None
    a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if a > 12:
        day, month = a, b
    else:
        month, day = a, b
    try:
        return date(y, month, day)
    except ValueError:
        return None

def _validate_date_consistency(rows: list[dict], *, document_text: str | None = None) -> None:
    """Log warnings for implausible dates — does not overwrite extracted values."""
    cur_comm = _find_row_by_suffix(rows, "Current Commencement Date")
    cur_exp = _find_row_by_suffix(rows, "Current Expiration Date")
    if cur_comm and cur_exp:
        comm_v = str(cur_comm.get("value", "")).strip()
        exp_v = str(cur_exp.get("value", "")).strip()
        d1, d2 = _parse_dd_mm_yyyy(comm_v), _parse_dd_mm_yyyy(exp_v)
        if d1 and d2 and d2 <= d1:
            log.warning(
                "Date check: Current Expiration (%s) is not after Current Commencement (%s)",
                exp_v, comm_v,
            )
    if _is_renewal_lease_document(rows, document_text):
        orig_comm = _find_row_by_suffix(rows, "Original Commencement Date")
        if orig_comm and cur_comm:
            o_v = str(orig_comm.get("value", "")).strip()
            c_v = str(cur_comm.get("value", "")).strip()
            if (
                o_v and c_v and not is_empty_value(o_v) and not is_empty_value(c_v)
                and o_v == c_v
            ):
                log.warning(
                    "Date check: on renewal/amendment, Original Commencement equals "
                    "Current Commencement (%s) — verify recitals",
                    c_v,
                )
    eff = _find_row_by_suffix(rows, "Effective Date")
    exe = _find_row_by_suffix(rows, "Execution Date")
    if eff and exe:
        e_v = str(eff.get("value", "")).strip()
        x_v = str(exe.get("value", "")).strip()
        if e_v and x_v and not is_empty_value(e_v) and not is_empty_value(x_v) and e_v == x_v:
            log.debug("Date note: Effective Date equals Execution Date (%s)", e_v)

_US_STATE_ABBREVS = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
    "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT",
    "VA", "WA", "WV", "WI", "WY", "DC",
})

def _lease_uses_us_dates(rows: list[dict], document_text: str | None = None) -> bool:
    country = _find_row_by_suffix(rows, "Country")
    if country:
        cv = (country.get("value") or "").strip().lower()
        if cv in ("united states", "united states of america", "usa", "us", "u.s.", "u.s.a."):
            return True
    state = _find_row_by_suffix(rows, "State / Province")
    if state:
        sv = (state.get("value") or "").strip()
        if sv.upper() in _US_STATE_ABBREVS or sv.lower() in (
            "florida", "california", "texas", "new york", "georgia", "illinois",
        ):
            return True
    if document_text and re.search(r"\b(?:United States|U\.S\.A?\.|State of Florida)\b", document_text[:20_000], re.I):
        return True
    fn = _lease_filename_from_rows(rows).lower()
    if re.search(r"\bUS\d|_FL\d|FL\d|ideal image of florida", fn):
        return True
    return False

def _normalize_date_value(value: str, *, us_dates: bool = False) -> str:
    v = value.strip()
    if not v or v.lower() in (NOT_FOUND.lower(), LEASE_SILENT.lower()):
        return v
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    }
    m_ord = re.match(
        r"^(\d{1,2})(?:st|nd|rd|th)?\s+(?:day\s+of\s+)?([A-Za-z]+),?\s+(\d{4})$",
        v.replace("  ", " "),
        re.I,
    )
    if m_ord:
        day = int(m_ord.group(1))
        mon = months.get(m_ord.group(2).lower().rstrip("."), 0)
        year = int(m_ord.group(3))
        if mon:
            if us_dates:
                return f"{mon:02d}/{day:02d}/{year}"
            return f"{day:02d}/{mon:02d}/{year}"
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", v)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a > 12:
            return f"{b:02d}/{a:02d}/{y}" if us_dates else f"{b:02d}/{a:02d}/{y}"
        if b > 12:
            return f"{a:02d}/{b:02d}/{y}"
        if us_dates:
            return f"{a:02d}/{b:02d}/{y}"
        return f"{a:02d}/{b:02d}/{y}"
    m2 = re.match(r"^(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})$", v.replace("  ", " "))
    if m2:
        day = int(m2.group(1))
        mon = months.get(m2.group(2).lower().rstrip("."), 0)
        year = int(m2.group(3))
        if mon:
            if us_dates:
                return f"{mon:02d}/{day:02d}/{year}"
            return f"{day:02d}/{mon:02d}/{year}"
    if re.match(r"^\d{4}-\d{1,2}-\d{1,2}$", v):
        y, mo, d = v.split("-")
        if us_dates:
            return f"{int(mo):02d}/{int(d):02d}/{y}"
        return f"{int(d):02d}/{int(mo):02d}/{y}"
    return v

def _cleanup_party_name(value: str) -> str:
    v = value.strip()
    if not v:
        return v
    v = re.sub(r"\bAI\s+Futtaim\b", "Al Futtaim", v, flags=re.IGNORECASE)
    v = re.sub(r"\s+", " ", v)
    return v

_AREA_UOM_IN_TEXT = re.compile(
    r"(sq\s*m(?:trs?|eters?)?|sq\.?\s*ft|square\s+met(?:er|re)s?|m²|m2|\bsm\b)",
    re.IGNORECASE,
)

def _find_row_by_suffix(rows: list[dict], suffix: str) -> dict | None:
    for row in rows:
        if _field_suffix(row["attribute_name"]) == suffix:
            return row
    return None

def _find_area0_row(rows: list[dict], suffix: str) -> dict | None:
    for row in rows:
        name = row["attribute_name"]
        if "Area.0." in name and _field_suffix(name) == suffix:
            return row
    return None

def _copy_value_if_empty(target: dict | None, source: dict | None, *, reason: str) -> None:
    if not target or not source:
        return
    if is_empty_value(target.get("value", "")) and not is_empty_value(source.get("value", "")):
        target["value"] = source["value"].strip()
        target["_conf_provenance"] = "backfill"
        log.info("Backfill %s ← %s (%s)", target["attribute_name"], source["attribute_name"], reason)

def _mark_post_fix(row: dict) -> None:
    row["_conf_provenance"] = "post_fix"

_RENEWAL_DOC_RE = re.compile(r"\b(renewal|amendment|extension|addendum)\b", re.I)

def _lease_filename_from_rows(rows: list[dict]) -> str:
    for row in rows:
        name = row.get("attribute_name", "")
        if "Lease_DQC" in name and name.endswith("filename"):
            return (row.get("value") or "").strip()
    return ""

def _is_renewal_lease_document(rows: list[dict], document_text: str | None = None) -> bool:
    """True when the source document is a renewal/amendment (not the original base lease)."""
    fn = _lease_filename_from_rows(rows).lower()
    if fn and _RENEWAL_DOC_RE.search(fn):
        return True
    if document_text and _RENEWAL_DOC_RE.search(document_text[:12_000]):
        return True
    return False

def _backfill_sparse_fields(rows: list[dict], *, document_text: str | None = None) -> None:

    cur_comm = _find_row_by_suffix(rows, "Current Commencement Date")
    cur_exp = _find_row_by_suffix(rows, "Current Expiration Date")
    rent_comm = _find_row_by_suffix(rows, "Rent Commencement Date")

    if not _is_renewal_lease_document(rows, document_text):
        _copy_value_if_empty(
            _find_row_by_suffix(rows, "Original Commencement Date"), cur_comm,
            reason="current term commencement",
        )
        _copy_value_if_empty(
            _find_row_by_suffix(rows, "Original Expiration Date"), cur_exp,
            reason="current term expiration",
        )
    else:
        log.info(
            "Renewal/amendment document: skipping Original date backfill from Current term"
        )
    _copy_value_if_empty(
        _find_row_by_suffix(rows, "Possession Date"),
        cur_comm or rent_comm,
        reason="commencement as possession",
    )

    postal = _find_row_by_suffix(rows, "Postal code")
    if postal and is_empty_value(postal.get("value", "")):
        for row in rows:
            if "Contact Identification.0." not in row["attribute_name"]:
                continue
            if _field_suffix(row["attribute_name"]) == "P.O. Box" and not is_empty_value(row.get("value", "")):
                postal["value"] = row["value"].strip()
                postal["_conf_provenance"] = "backfill"
                log.info("Backfill %s from licensor P.O. Box", postal["attribute_name"])
                break

    gross = _find_area0_row(rows, "Gross area")
    gross_uom = _find_area0_row(rows, "Gross Area UOM")
    net = _find_area0_row(rows, "Net area")
    net_uom = _find_area0_row(rows, "Net Area UOM")
    if net and not is_empty_value(net.get("value", "")):
        _copy_value_if_empty(net_uom, gross_uom, reason="gross UOM as net UOM when net area present")
    _copy_value_if_empty(_find_row_by_suffix(rows, "UOM"), gross_uom or net_uom, reason="area UOM")

    prop_name = _find_row_by_suffix(rows, "Property name")
    for row in rows:
        if "Contact Identification." not in row["attribute_name"]:
            continue
        if _field_suffix(row["attribute_name"]) != "DBA":
            continue
        if not is_empty_value(row.get("value", "")):
            continue
        slot = row["attribute_name"].split("Contact Identification.", 1)[-1].split(".", 1)[0]
        street = _find_row_by_suffix(
            [r for r in rows if f"Contact Identification.{slot}." in r["attribute_name"]],
            "Street",
        )
        if street and not is_empty_value(street.get("value", "")):
            row["value"] = street["value"].strip()
            row["_conf_provenance"] = "backfill"
            log.info("Backfill %s from contact street", row["attribute_name"])
        elif slot == "0" and prop_name and not is_empty_value(prop_name.get("value", "")):
            row["value"] = prop_name["value"].strip()
            row["_conf_provenance"] = "backfill"
            log.info("Backfill %s from property name", row["attribute_name"])
        elif slot == "1":
            tenant = _find_row_by_suffix(rows, "Tenant Name")
            if tenant and not is_empty_value(tenant.get("value", "")):
                row["value"] = tenant["value"].strip()
                row["_conf_provenance"] = "backfill"
                log.info("Backfill %s from tenant name", row["attribute_name"])

def _infer_area_uom(rows: list[dict]) -> str | None:
    """First non-empty Area.0 UOM value found anywhere in the abstraction."""
    for row in rows:
        if "Area.0" not in row["attribute_name"] or "UOM" not in row["attribute_name"]:
            continue
        val = row["value"].strip()
        if not is_empty_value(val):
            return val
    return None

def _uom_from_area_contexts(rows: list[dict]) -> str | None:
    """Unit mentioned in any Area.0 row context (often net/gross area extraction notes)."""
    for row in rows:
        if "Area.0" not in row["attribute_name"]:
            continue
        found = _uom_from_area_value(str(row.get("context") or ""))
        if found:
            return found
    return None

def _uom_from_area_value(value: str) -> str | None:
    """Unit embedded in an area string (e.g. '17.46 sq mtrs')."""
    m = _AREA_UOM_IN_TEXT.search(value)
    if m:
        return m.group(1).strip()
    return None

_AREA_TYPE_FROM_CONTEXT = re.compile(
    r"\b(Commercial\s+Bank|Commercial\s+Block)\b",
    re.IGNORECASE,
)
_GENERIC_AREA_TYPE = re.compile(
    r"^designated\s+(?:area|fsu|space)?$|^licensed\s+area$|^fsu$",
    re.IGNORECASE,
)

def _fix_area_type_rows(rows: list[dict]) -> None:
    """
    Correct Area.{i}.Type when the model used a generic zone label but the
    document context names Commercial Bank / Commercial Block (UAE mall licences).
    """
    blob_parts: list[str] = []
    for row in rows:
        if "Area.0." in row["attribute_name"]:
            blob_parts.append(str(row.get("value") or ""))
            blob_parts.append(str(row.get("context") or ""))
    blob = " ".join(blob_parts)
    match = _AREA_TYPE_FROM_CONTEXT.search(blob)
    if not match:
        return
    canonical = re.sub(r"\s+", " ", match.group(1)).strip()
    if canonical.lower() == "commercial block":
        canonical = "Commercial Block"
    elif canonical.lower() == "commercial bank":
        canonical = "Commercial Bank"

    for row in rows:
        name = row["attribute_name"]
        if "Area.0." not in name or _field_suffix(name) != "Type":
            continue
        val = (row.get("value") or "").strip()
        if not val or not _GENERIC_AREA_TYPE.match(val):
            continue
        row["value"] = canonical
        _mark_post_fix(row)
        log.info("Area fix: Type %r → %r (from document context)", val, canonical)

def _fix_area_uom_rows(rows: list[dict]) -> None:
    """
    When gross/net area has a value but UOM is empty, infer UOM from the document.
    Reusable across leases (any attribute schema with Area.0.* area + UOM fields).
    """
    gross = _find_row_by_suffix(rows, "Gross area")
    gross_uom = _find_row_by_suffix(rows, "Gross Area UOM")
    net = _find_row_by_suffix(rows, "Net area")
    net_uom = _find_row_by_suffix(rows, "Net Area UOM")

    inferred = _infer_area_uom(rows)

    doc_uom = _uom_from_area_contexts(rows)

    if gross and gross_uom and not is_empty_value(gross["value"]) and is_empty_value(
        gross_uom["value"]
    ):
        uom = (
            _uom_from_area_value(gross["value"])
            or _uom_from_area_value(str(gross.get("context") or ""))
            or doc_uom
            or inferred
        )
        if uom:
            gross_uom["value"] = uom
            _mark_post_fix(gross_uom)
            log.info("Area fix: Gross Area UOM set to %r", uom)

    if net and net_uom and not is_empty_value(net["value"]) and is_empty_value(net_uom["value"]):
        uom = (
            _uom_from_area_value(net["value"])
            or _uom_from_area_value(str(net.get("context") or ""))
            or (
                gross_uom["value"]
                if gross_uom and not is_empty_value(gross_uom.get("value", ""))
                else None
            )
            or doc_uom
            or inferred
        )
        if uom:
            net_uom["value"] = uom
            _mark_post_fix(net_uom)
            log.info("Area fix: Net Area UOM set to %r", uom)

_CONF_W_TOKEN = 0.35
_CONF_W_FORMAT = 0.25
_CONF_W_CONTEXT_LEN = 0.20
_CONF_W_LLM = 0.20

_CONF_STOPWORDS = frozenset({"that", "this", "with", "from", "shall", "will", "have", "been", "were", "their", "there", "which", "upon", "into", "also", "such", "only", "each", "any", "all", "for", "and", "the", "are", "not", "per", "may", "can"})

def _is_clause_attribute(attribute_name: str) -> bool:
    return (
        ".Clauses." in attribute_name
        or "Lease_Abstraction.Lease_Abstraction." in attribute_name
    )

def _confidence_skip_row(attribute_name: str, row: dict) -> bool:
    if attribute_name in LEASE_DQC_ATTRS:
        return True
    return row.get("_conf_provenance") in ("backfill", "post_fix")

def _normalize_llm_confidence(raw: Any) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if 0.0 <= v <= 1.0:
        v *= 100.0
    return max(0.0, min(100.0, v))

def _context_match_tokens(value: str, context: str) -> list[str]:
    tokens: list[str] = []
    for m in re.finditer(r"\d{3,}", value):
        tokens.append(m.group(0))
    for w in re.findall(r"[A-Za-z]{4,}", value):
        wl = w.lower()
        if wl not in _CONF_STOPWORDS:
            tokens.append(wl)
    return tokens

def _score_context_token_match(value: str, context: str) -> float:
    tokens = _context_match_tokens(value, context)
    if not tokens:
        return 60.0
    ctx = context.lower()
    hits = sum(1 for t in tokens if t in ctx)
    return round(100.0 * hits / len(tokens), 1)

def _score_format_fit(attribute_name: str, value: str) -> float:
    if is_empty_value(value):
        return 0.0
    short = _field_suffix(attribute_name).lower()
    v = value.strip()
    vl = v.lower()

    if _is_clause_attribute(attribute_name):
        wc = len(v.split())
        if wc >= 15:
            return 100.0
        if wc >= 8:
            return 80.0
        if wc >= 3:
            return 55.0
        return 25.0

    date_kws = ("date", "expir", "commenc", "execution", "possession", "delivery")
    if any(k in short for k in date_kws):
        if re.search(r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}", v):
            return 100.0
        if re.search(r"\b(19|20)\d{2}\b", v) and re.search(
            r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", vl
        ):
            return 85.0
        if re.search(r"\b(19|20)\d{2}\b", v):
            return 60.0
        return 20.0

    amount_kws = (
        "rent", "fee", "deposit", "amount", "penalty", "charge", "price", "cost",
        "monthly", "annual",
    )
    if any(k in short for k in amount_kws) and "uom" not in short:
        has_digit = bool(re.search(r"\d", v))
        has_ccy = bool(re.search(r"\b(aed|usd|eur|gbp|sgd|hkd|\$|€|£)\b", vl))
        if has_digit and has_ccy:
            return 100.0
        if has_digit:
            return 75.0
        return 0.0

    if "area" in short and "uom" not in short:
        return 100.0 if re.search(r"\d", v) else 20.0

    if short.endswith("uom") or " uom" in short:
        return 100.0 if v.strip() else 0.0

    if "duration" in short or short == "term duration":
        has_n = bool(re.search(r"\d+|one|two|three|four|five|six|seven|eight|nine|ten", vl))
        has_u = bool(re.search(r"\b(year|month|week|day)s?\b", vl))
        if has_n and has_u:
            return 100.0
        if has_n or has_u:
            return 60.0
        return 10.0

    if "name" in short:
        words = v.split()
        has_suffix = bool(re.search(r"\b(LLC|L\.L\.C|Ltd|Limited|PLC|Pte|Inc)\b", v, re.I))
        if len(words) >= 2 and has_suffix:
            return 100.0
        if len(words) >= 2:
            return 80.0
        return 45.0

    return 70.0

def _score_context_richness(attribute_name: str, context: str) -> float:
    n = len((context or "").strip())
    if n <= 0:
        return 0.0
    if _is_clause_attribute(attribute_name):
        if n >= 150:
            return 100.0
        if n >= 80:
            return 70.0
        if n >= 30:
            return 40.0
        return 20.0
    if n >= 50:
        return 100.0
    if n >= 30:
        return 70.0
    if n >= 10:
        return 40.0
    return 20.0

def finalize_confidence(rows: list[dict]) -> None:
    """Overwrite confidence_score from value, context, and normalized LLM input."""
    for row in rows:
        name = row["attribute_name"]
        if _confidence_skip_row(name, row):
            row["confidence_score"] = ""
            continue
        val = row.get("value", "")
        if is_empty_value(val):
            row["confidence_score"] = 0
            continue
        llm = _normalize_llm_confidence(row.get("confidence_score", 0))
        r1 = _score_context_token_match(val, str(row.get("context") or ""))
        r2 = _score_format_fit(name, val)
        r3 = _score_context_richness(name, str(row.get("context") or ""))
        blended = (
            _CONF_W_TOKEN * r1
            + _CONF_W_FORMAT * r2
            + _CONF_W_CONTEXT_LEN * r3
            + _CONF_W_LLM * llm
        )
        row["confidence_score"] = int(round(max(0.0, min(100.0, blended))))

def _excel_confidence_cell(row: dict) -> int | float | str:
    conf = row.get("confidence_score", "")
    if conf == "" or conf is None:
        return ""
    if isinstance(conf, (int, float)):
        return int(conf) if float(conf) == int(conf) else round(float(conf), 1)
    return conf

def _split_context_by_page_markers(context: str) -> dict[int, str]:
    """Sections of context text keyed by PDF page (from ===== PAGE N ===== markers)."""
    sections: dict[int, str] = {}
    if not context or "===== PAGE" not in context.upper():
        return sections
    parts = _PAGE_MARKER_RE.split(context)
    nums = [int(m.group(1)) for m in _PAGE_MARKER_RE.finditer(context)]
    for i, num in enumerate(nums):
        body = parts[i + 1] if i + 1 < len(parts) else ""
        sections[num] = (sections.get(num, "") + " " + body).strip()
    return sections

def _infer_page_from_marked_context(value: str, context: str) -> int | None:
    """Best page whose marker section contains the value tokens (text-extract mode)."""
    sections = _split_context_by_page_markers(context)
    if not sections:
        return None
    tokens = _context_match_tokens(value, context)
    if not tokens:
        return None
    best_page: int | None = None
    best_hits = 0
    for page, section in sections.items():
        sl = section.lower()
        hits = sum(1 for t in tokens if t in sl)
        if hits > best_hits:
            best_hits, best_page = hits, page
    if best_hits == 0:
        return None
    return best_page

def _finalize_page_numbers(
    rows: list[dict],
    *,
    pdf_page_count: int | None = None,
    text_has_page_markers: bool = False,
) -> None:
    """
    Normalize page_number; blank invalid pages; optionally correct page 1 guesses
    using ===== PAGE N ===== sections in context (large-PDF text mode).
    """
    page_one_populated = 0
    populated = 0

    for row in rows:
        if is_empty_value(row.get("value", "")):
            row["page_number"] = ""
            continue
        if _confidence_skip_row(row["attribute_name"], row):
            row["page_number"] = ""
            continue

        populated += 1
        reported = _normalize_page_number(row.get("page_number"))
        ctx = str(row.get("context") or "")
        val = row.get("value", "")

        if text_has_page_markers and ctx.upper().find("===== PAGE") >= 0:
            inferred = _infer_page_from_marked_context(val, ctx)
            if inferred is not None:
                if reported == "" or reported == 1:
                    if inferred != 1:
                        row["page_number"] = inferred
                        log.debug(
                            "Page fix: %s → page %s (from context marker)",
                            row["attribute_name"], inferred,
                        )
                    else:
                        row["page_number"] = 1
                elif reported != inferred:
                    tokens = _context_match_tokens(val, ctx)
                    sections = _split_context_by_page_markers(ctx)
                    rep_sec = sections.get(int(reported), "").lower() if reported != "" else ""
                    inf_sec = sections.get(inferred, "").lower()
                    rep_hits = sum(1 for t in tokens if t in rep_sec) if rep_sec else 0
                    inf_hits = sum(1 for t in tokens if t in inf_sec) if inf_sec else 0
                    if inf_hits > rep_hits:
                        row["page_number"] = inferred
                        log.debug(
                            "Page fix: %s %s → %s (marker sections)",
                            row["attribute_name"], reported, inferred,
                        )
                    else:
                        row["page_number"] = reported
                else:
                    row["page_number"] = reported
            else:
                row["page_number"] = reported if reported != "" else ""
        else:
            row["page_number"] = reported

        if pdf_page_count and pdf_page_count > 0 and row["page_number"] != "":
            try:
                pn = int(row["page_number"])
                if pn > pdf_page_count:
                    row["page_number"] = ""
            except (TypeError, ValueError):
                row["page_number"] = ""

        if row["page_number"] == 1:
            page_one_populated += 1

    if (
        populated >= 10
        and pdf_page_count
        and pdf_page_count > 3
        and page_one_populated / populated > 0.85
    ):
        log.warning(
            "Page numbers: %.0f%% of populated fields are page 1 on a %s-page PDF — "
            "check model output (expected varied pages).",
            100.0 * page_one_populated / populated, pdf_page_count,
        )

def _page_at_text_offset(document_text: str, idx: int) -> int | None:
    before = document_text[: max(0, idx)]
    markers = list(re.finditer(r"===== PAGE (\d+) =====", before))
    if not markers:
        return None
    try:
        return int(markers[-1].group(1))
    except (TypeError, ValueError):
        return None

def _search_needles_for_value(value: str) -> list[str]:
    v = str(value).strip()
    if not v or is_empty_value(v):
        return []
    needles = [v]
    if re.search(r"[\d,]", v):
        plain = re.sub(r"[^\d.]", "", v.replace(",", ""))
        if plain and plain not in needles:
            needles.append(plain)
        if "," not in v and plain:
            try:
                num = float(plain)
                if num >= 1000:
                    needles.append(f"{num:,.2f}")
                    if num == int(num):
                        needles.append(f"{int(num):,}")
            except ValueError:
                pass
    return needles

def _snippet_from_document(
    document_text: str,
    value: str,
    *,
    window: int = 320,
) -> tuple[str, int | None]:
    """Find value in extracted text; return quote snippet and page from markers."""
    for needle in _search_needles_for_value(value):
        idx = document_text.find(needle)
        if idx < 0:
            idx = document_text.lower().find(needle.lower())
        if idx < 0:
            continue
        start = max(0, idx - window)
        end = min(len(document_text), idx + len(needle) + window)
        snippet = re.sub(r"\s+", " ", document_text[start:end]).strip()
        page = _page_at_text_offset(document_text, idx)
        if page is None:
            page = _infer_page_from_marked_context(value, snippet)
        return snippet[:2000], page
    return "", None

def _backfill_missing_context(
    rows: list[dict],
    document_text: str | None,
) -> None:
    if not document_text:
        return
    for row in rows:
        if is_empty_value(row.get("value", "")):
            continue
        if _confidence_skip_row(row["attribute_name"], row):
            continue
        ctx = str(row.get("context") or "").strip()
        if len(ctx) >= 40:
            continue
        snippet, page = _snippet_from_document(document_text, row["value"])
        if not snippet:
            continue
        row["context"] = snippet
        row["_conf_provenance"] = "context_backfill"
        if page is not None and (row.get("page_number") in ("", None) or row.get("page_number") == 1):
            row["page_number"] = page
        log.debug("Context backfill: %s", row["attribute_name"])

def _party_domain_hints(rows: list[dict]) -> tuple[set[str], set[str]]:
    landlord_tokens: set[str] = set()
    tenant_tokens: set[str] = set()
    landlord = _find_row_by_suffix(rows, "Landlord Name")
    tenant = _find_row_by_suffix(rows, "Tenant Name")
    if landlord and not is_empty_value(landlord.get("value", "")):
        landlord_tokens = set(re.findall(r"[a-z0-9]{3,}", landlord["value"].lower()))
    if tenant and not is_empty_value(tenant.get("value", "")):
        tenant_tokens = set(re.findall(r"[a-z0-9]{3,}", tenant["value"].lower()))
    return landlord_tokens, tenant_tokens

def _backfill_contact_emails(rows: list[dict], document_text: str | None) -> None:
    if not document_text:
        return
    emails = list(dict.fromkeys(
        re.findall(r"[\w.\-]+@[\w.\-]+\.\w+", document_text, flags=re.I)
    ))
    if not emails:
        return
    landlord_tokens, tenant_tokens = _party_domain_hints(rows)

    def score_email(email: str, party: str) -> int:
        el = email.lower()
        local, _, domain = el.partition("@")
        domain_stem = domain.split(".")[0] if domain else ""
        tokens = landlord_tokens if party == "landlord" else tenant_tokens
        score = 0
        if domain_stem and domain_stem in tokens:
            score += 5
        for p in re.findall(r"[a-z0-9]{3,}", local + domain):
            if p in tokens:
                score += 2
        return score

    landlord_email = max(emails, key=lambda e: score_email(e, "landlord"), default="")
    tenant_email = max(emails, key=lambda e: score_email(e, "tenant"), default="")
    if score_email(landlord_email, "landlord") < 2:
        landlord_email = ""
    if score_email(tenant_email, "tenant") < 2:
        tenant_email = ""

    for slot, email in (("0", landlord_email), ("1", tenant_email)):
        if not email:
            continue
        row = next(
            (r for r in rows
             if r["attribute_name"] == f"Lease_catalyst.Lease_Abstraction.Contact Identification.{slot}.Email"),
            None,
        )
        if row is None:
            continue
        if not is_empty_value(row.get("value", "")):
            continue
        row["value"] = email
        row["_conf_provenance"] = "backfill"
        snippet, page = _snippet_from_document(document_text, email)
        if snippet:
            row["context"] = snippet
        if page is not None:
            row["page_number"] = page
        log.info("Backfill Contact Identification.%s.Email = %s", slot, email)

_CONTACT_SLOT_RE = re.compile(
    r"^Lease_catalyst\.Lease_Abstraction\.Contact Identification\.(\d+)\.(.+)$"
)
_EXPENSE_SLOT_RE = re.compile(
    r"^Lease_catalyst\.Lease_Abstraction\.Expenses\.(\d+)\.(.+)$"
)
_REPEATABLE_SLOT_PREFIX_RE = re.compile(
    r"^Lease_catalyst\.Lease_Abstraction\.(Area|Contact Identification|Options|Expenses|"
    r"Security Deposit|Allowance)\.(\d+)\.(.+)$"
)

_CONTACT_ADDRESS_FIELDS = frozenset({
    "Street", "Street no.", "City", "Zip code", "State / Province", "Country",
    "Suite", "P.O. Box", "Postal code",
})

_CORE_CONTACT_TYPE_KEYWORDS = frozenset({
    "landlord", "lessor", "licensor", "tenant", "lessee", "licensee",
    "notice copy", "payment contact", "payment", "guarantor", "notice",
})

_NONCORE_CONTACT_TYPE_KEYWORDS = frozenset({
    "contractor", "property manager", "manager", "broker", "counsel", "attorney",
    "utility", "vendor", "fire protection", "life safety", "county", "other contact",
    "witness", "assignee",
})

def _contact_slots_map(rows: list[dict]) -> dict[int, dict[str, dict]]:
    by_slot: dict[int, dict[str, dict]] = {}
    for row in rows:
        m = _CONTACT_SLOT_RE.match(row.get("attribute_name", ""))
        if not m:
            continue
        slot, suffix = int(m.group(1)), m.group(2)
        by_slot.setdefault(slot, {})[suffix] = row
    return by_slot

def _is_core_contact_type(contact_type: str) -> bool:
    cl = contact_type.strip().lower()
    if not cl or cl in (NA_VALUE.lower(), NOT_FOUND.lower()):
        return False
    if any(k in cl for k in _NONCORE_CONTACT_TYPE_KEYWORDS):
        return False
    return any(k in cl for k in _CORE_CONTACT_TYPE_KEYWORDS)

def _fix_fragmented_contact_slots(rows: list[dict]) -> None:
    """Merge consecutive contact slots where model put one address field per slot."""
    by_slot = _contact_slots_map(rows)
    if not by_slot:
        return
    sorted_slots = sorted(by_slot.keys())
    merge_target: dict[int, int] = {s: s for s in sorted_slots}

    def _populated_suffixes(slot: int) -> list[str]:
        return [
            s for s, row in by_slot.get(slot, {}).items()
            if not is_empty_value(row.get("value", ""))
        ]

    i = 0
    while i < len(sorted_slots):
        slot = sorted_slots[i]
        filled = _populated_suffixes(slot)
        ct = (by_slot.get(slot, {}).get("Contact Type") or {}).get("value", "")
        if len(filled) > 2 or _is_core_contact_type(str(ct)):
            i += 1
            continue
        if not filled or not all(s in _CONTACT_ADDRESS_FIELDS or s in ("Name", "Email", "Attention") for s in filled):
            i += 1
            continue
        target = slot
        j = i + 1
        while j < len(sorted_slots):
            nxt = sorted_slots[j]
            nxt_filled = _populated_suffixes(nxt)
            nxt_ct = (by_slot.get(nxt, {}).get("Contact Type") or {}).get("value", "")
            if len(nxt_filled) > 2 or _is_core_contact_type(str(nxt_ct)):
                break
            if not nxt_filled:
                j += 1
                continue
            if not all(s in _CONTACT_ADDRESS_FIELDS or s in ("Name", "Email", "Attention") for s in nxt_filled):
                break
            for suffix, row in by_slot[nxt].items():
                tgt_row = by_slot[target].get(suffix)
                if tgt_row and is_empty_value(tgt_row.get("value", "")) and not is_empty_value(row.get("value", "")):
                    tgt_row["value"] = row["value"]
                    if row.get("context") and not tgt_row.get("context"):
                        tgt_row["context"] = row["context"]
                    tgt_row["_conf_provenance"] = "post_fix"
                elif not tgt_row and not is_empty_value(row.get("value", "")):
                    by_slot.setdefault(target, {})[suffix] = row
                    row["attribute_name"] = (
                        f"Lease_catalyst.Lease_Abstraction.Contact Identification.{target}.{suffix}"
                    )
            merge_target[nxt] = target
            j += 1
        i = j if j > i + 1 else i + 1

    drop_slots = {s for s, t in merge_target.items() if s != t}
    if drop_slots:
        rows[:] = [
            r for r in rows
            if not (
                (m := _CONTACT_SLOT_RE.match(r.get("attribute_name", "")))
                and int(m.group(1)) in drop_slots
            )
        ]
        log.info("Merged fragmented contact slots (removed slots %s)", sorted(drop_slots))

def _prune_noncore_contacts(rows: list[dict]) -> None:
    """Drop contact slots that are contractors, utilities, exhibit vendors, etc."""
    by_slot = _contact_slots_map(rows)
    drop: set[int] = set()
    for slot, fields in by_slot.items():
        ct = str((fields.get("Contact Type") or {}).get("value", "")).strip()
        if ct and not _is_core_contact_type(ct):
            drop.add(slot)
            continue
        if not ct or is_empty_value(ct):
            name = str((fields.get("Name") or {}).get("value", "")).lower()
            ctx = " ".join(str((fields.get(s) or {}).get("context", "")) for s in fields).lower()
            blob = name + " " + ctx
            if any(k in blob for k in (
                "fire protection", "life safety", "hartford", "excel fire",
                "orange county utility", "ocu", "county attorney", "exhibit b",
                "property manager", "deno dikeou",
            )):
                drop.add(slot)
    if drop:
        rows[:] = [
            r for r in rows
            if not (
                (m := _CONTACT_SLOT_RE.match(r.get("attribute_name", "")))
                and int(m.group(1)) in drop
            )
        ]
        log.info("Pruned non-core contact slots: %s", sorted(drop))

def _expense_slots_map(rows: list[dict]) -> dict[int, dict[str, dict]]:
    by_slot: dict[int, dict[str, dict]] = {}
    for row in rows:
        m = _EXPENSE_SLOT_RE.match(row.get("attribute_name", ""))
        if not m:
            continue
        slot, suffix = int(m.group(1)), m.group(2)
        by_slot.setdefault(slot, {})[suffix] = row
    return by_slot

def _expense_slot_has_amount(fields: dict[str, dict]) -> bool:
    for key in ("Monthly Amount", "Annual Amount", "Monthly Amount per SF", "Annual amount per SF"):
        row = fields.get(key)
        if row and re.search(r"\d", str(row.get("value", ""))):
            return True
    return False

def _prune_empty_expense_slots(rows: list[dict]) -> None:
    """Remove expense slots with no stated dollar amounts."""
    by_slot = _expense_slots_map(rows)
    drop = {slot for slot, fields in by_slot.items() if not _expense_slot_has_amount(fields)}
    if drop:
        rows[:] = [
            r for r in rows
            if not (
                (m := _EXPENSE_SLOT_RE.match(r.get("attribute_name", "")))
                and int(m.group(1)) in drop
            )
        ]
        log.info("Pruned empty expense slots: %s", sorted(drop))

def _compact_repeatable_slots(rows: list[dict], group: str) -> None:
    """Renumber populated repeatable slots to contiguous .0, .1, .2 …"""
    prefix = f"Lease_catalyst.Lease_Abstraction.{group}."
    slot_re = re.compile(rf"^{re.escape(prefix)}(\d+)\.(.+)$")
    by_slot: dict[int, list[dict]] = {}
    for row in rows:
        m = slot_re.match(row.get("attribute_name", ""))
        if not m:
            continue
        by_slot.setdefault(int(m.group(1)), []).append(row)

    populated = sorted(
        s for s, slot_rows in by_slot.items()
        if any(not is_empty_value(r.get("value", "")) for r in slot_rows)
    )
    if not populated or populated == list(range(len(populated))):
        return

    renumber = {old: new for new, old in enumerate(populated)}
    for row in rows:
        m = slot_re.match(row.get("attribute_name", ""))
        if not m:
            continue
        old_slot = int(m.group(1))
        if old_slot in renumber and renumber[old_slot] != old_slot:
            row["attribute_name"] = f"{prefix}{renumber[old_slot]}.{m.group(2)}"
    log.info("Compacted %s slots %s → 0..%s", group, populated, len(populated) - 1)

def _merge_expense_slot_rows(doc_row_lists: list[list[dict]]) -> list[dict]:
    """
    Merge expense slots across documents: per slot index, newest doc with a stated
    amount wins; older docs fill indices the newest doc did not populate.
    """
    slot_maps = [_expense_slots_map(rows) for rows in doc_row_lists]
    all_slots: set[int] = set()
    for m in slot_maps:
        all_slots.update(m.keys())

    kept: dict[int, dict[str, dict]] = {}
    for slot in sorted(all_slots):
        for m in reversed(slot_maps):
            fields = m.get(slot, {})
            if _expense_slot_has_amount(fields):
                kept[slot] = fields
                break

    out_rows: list[dict] = []
    for new_idx, old_slot in enumerate(sorted(kept.keys())):
        for row in kept[old_slot].values():
            out_rows.append(_renumber_repeatable_row(dict(row), new_idx))
    return out_rows

def _fix_term_duration_on_amendment(rows: list[dict], document_text: str | None = None) -> None:
    if not _is_renewal_lease_document(rows, document_text):
        return
    td = _find_row_by_suffix(rows, "Term Duration")
    if not td:
        return
    td["value"] = NA_VALUE
    td["_conf_provenance"] = "post_fix"
    log.info("Term Duration → N/A on amendment/assignment document")

def _backfill_security_deposit(rows: list[dict], document_text: str | None) -> None:
    if not document_text:
        return
    dep_type = _find_row_by_suffix(rows, "Security Deposit Type")
    dep_curr = _find_row_by_suffix(rows, "Security Deposit Currency")
    if not dep_type and not dep_curr:
        return
    text_l = document_text[:80_000].lower()
    if dep_type and is_empty_value(dep_type.get("value", "")):
        if re.search(r"\b(cash|letter of credit|l/?c)\b.*security deposit|security deposit.*\b(cash|letter of credit)\b", text_l):
            dep_type["value"] = "Cash" if "cash" in text_l else "Letter of Credit"
            dep_type["_conf_provenance"] = "backfill"
            log.info("Backfill Security Deposit Type")
    if dep_curr and is_empty_value(dep_curr.get("value", "")):
        if re.search(r"\busd\b|u\.s\.?\s*dollar|\$", text_l):
            dep_curr["value"] = "USD"
            dep_curr["_conf_provenance"] = "backfill"
            log.info("Backfill Security Deposit Currency → USD")

def _prune_empty_repeatable_slot_rows(rows: list[dict]) -> None:
    """Drop all rows for repeatable slots that have no populated values."""
    slots_with_data: dict[str, set[int]] = {}
    for row in rows:
        m = _REPEATABLE_SLOT_PREFIX_RE.match(row.get("attribute_name", ""))
        if not m:
            continue
        group, slot = m.group(1), int(m.group(2))
        if not is_empty_value(row.get("value", "")):
            slots_with_data.setdefault(group, set()).add(slot)
    rows[:] = [
        r for r in rows
        if not (
            (m := _REPEATABLE_SLOT_PREFIX_RE.match(r.get("attribute_name", "")))
            and int(m.group(2)) not in slots_with_data.get(m.group(1), set())
        )
    ]

def _validate_expense_dates(rows: list[dict]) -> None:
    """Align Expenses.0 term dates with Pass 1 Current Commencement/Expiration when empty."""
    cur_comm = _find_row_by_suffix(rows, "Current Commencement Date")
    cur_exp = _find_row_by_suffix(rows, "Current Expiration Date")
    if not cur_comm or not cur_exp:
        return
    cur_comm_v = str(cur_comm.get("value", "")).strip()
    cur_exp_v = str(cur_exp.get("value", "")).strip()
    if is_empty_value(cur_comm_v) or is_empty_value(cur_exp_v):
        return

    slot0: dict[str, dict] = {}
    for row in rows:
        if "Expenses.0." not in row.get("attribute_name", ""):
            continue
        slot0[_field_suffix(row["attribute_name"])] = row

    start_row = slot0.get("Start date")
    end_row = slot0.get("End date")
    if start_row and is_empty_value(start_row.get("value", "")):
        start_row["value"] = cur_comm_v
        start_row["_conf_provenance"] = "post_fix"
        log.info("Expenses.0 Start date aligned to Current Commencement Date")
    if end_row and is_empty_value(end_row.get("value", "")):
        end_row["value"] = cur_exp_v
        end_row["_conf_provenance"] = "post_fix"
        log.info("Expenses.0 End date aligned to Current Expiration Date")

    if start_row and end_row:
        start_v = str(start_row.get("value", "")).strip()
        end_v = str(end_row.get("value", "")).strip()
        if start_v and end_v and (start_v != cur_comm_v or end_v != cur_exp_v):
            log.warning(
                "Expenses.0 dates (%s – %s) differ from Current term (%s – %s)",
                start_v, end_v, cur_comm_v, cur_exp_v,
            )

def _fix_expense_rent_types(rows: list[dict]) -> None:
    """Infer Rent Type when amounts exist but type was left Not found."""
    by_slot: dict[int, dict[str, dict]] = {}
    for row in rows:
        m = re.search(r"Expenses\.(\d+)\.", row["attribute_name"])
        if not m:
            continue
        slot = int(m.group(1))
        by_slot.setdefault(slot, {})[_field_suffix(row["attribute_name"])] = row

    for slot, fields in by_slot.items():
        rt_row = fields.get("Rent Type")
        if not rt_row:
            continue
        rt = str(rt_row.get("value", "")).strip()
        if rt and rt not in (NOT_FOUND, NA_VALUE):
            continue
        monthly = str((fields.get("Monthly Amount") or {}).get("value", "")).strip()
        annual = str((fields.get("Annual Amount") or {}).get("value", "")).strip()
        has_money = bool(re.search(r"\d", monthly + annual))
        if not has_money:
            continue
        ctx = " ".join(str((fields.get(k) or {}).get("context", "")) for k in fields)
        ctx_l = ctx.lower()
        label = "Fixed Rent"
        if "common area" in ctx_l or "cam" in ctx_l:
            label = "Common Area Charges"
        elif "real estate tax" in ctx_l or "tax" in ctx_l:
            label = "Real Estate Taxes"
        elif "insurance" in ctx_l:
            label = "Insurance"
        elif "percentage rent" in ctx_l:
            label = "Percentage Rent"
        rt_row["value"] = label
        rt_row["_conf_provenance"] = "post_fix"
        log.info("Expense slot %s: Rent Type → %s", slot, label)

# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------

def apply_post_processing(
    rows: list[dict],
    *,
    document_text: str | None = None,
) -> list[dict]:
    _backfill_sparse_fields(rows, document_text=document_text)
    _fix_area_type_rows(rows)
    _fix_area_uom_rows(rows)
    _validate_expense_dates(rows)
    _validate_date_consistency(rows, document_text=document_text)
    _fix_expense_rent_types(rows)
    _backfill_contact_emails(rows, document_text)
    _fix_fragmented_contact_slots(rows)
    _prune_noncore_contacts(rows)
    _prune_empty_expense_slots(rows)
    _compact_repeatable_slots(rows, "Expenses")
    _fix_term_duration_on_amendment(rows, document_text)
    _backfill_security_deposit(rows, document_text)
    _backfill_missing_context(rows, document_text)
    us_dates = _lease_uses_us_dates(rows, document_text)
    for row in rows:
        name = row["attribute_name"]
        val  = row["value"]
        if val in (NOT_FOUND, LEASE_SILENT):
            continue
        short = _field_suffix(name)
        if "date" in short.lower():
            row["value"] = _normalize_date_value(val, us_dates=us_dates)
        if "Name" in short:
            row["value"] = _cleanup_party_name(row["value"])
    _prune_empty_repeatable_slot_rows(rows)
    finalize_confidence(rows)
    return rows

def apply_merged_post_processing(
    rows: list[dict],
    docs: list[LeaseDocument],
    *,
    document_text: str | None = None,
) -> list[dict]:
    """
    Re-prune repeatables and re-normalize dates on merged package output.

    Pass document_text from extract() cache when available — do not reload PDFs here.
    Row-only steps still run when document_text is None (uses merged rows / DQC filenames).
    """
    _ = docs  # kept for API stability; filenames come from merged Lease_DQC rows
    _fix_term_duration_on_amendment(rows, document_text)
    _fix_fragmented_contact_slots(rows)
    _prune_noncore_contacts(rows)
    _prune_empty_expense_slots(rows)
    _compact_repeatable_slots(rows, "Expenses")
    us_dates = _lease_uses_us_dates(rows, document_text)
    for row in rows:
        val = row.get("value", "")
        if val in (NOT_FOUND, LEASE_SILENT):
            continue
        if "date" in _field_suffix(row["attribute_name"]).lower():
            row["value"] = _normalize_date_value(val, us_dates=us_dates)
    _prune_empty_repeatable_slot_rows(rows)
    finalize_confidence(rows)
    return rows

def extract(
    pdf_path: str,
    plan: ExtractionPlan,
) -> tuple[list[dict], dict[str, Any], str | None]:
    """
    Compact mode: 4 passes — core+dates+options+deposit | contacts+area | expenses | clauses.
    Hybrid routing: gemini-3.5-flash (pass 1), gpt-5-mini (passes 2–3), gemini-3.1-flash-lite (clauses).

    Returns (rows, run_stats, document_text) — document_text is cached for package merge.
    """
    client = genai.Client(api_key=get_api_key())
    openai_client: OpenAI | None = None
    model_override, model_fast, model_quality, hybrid = _resolve_extraction_models()
    if model_override:
        log.info("Extraction model (all Gemini passes): %s", model_override)
    elif hybrid:
        log.info(
            "Hybrid models: %s → core+dates+options+deposit | %s/%s → contacts+area, expenses | "
            "%s → clauses",
            model_quality,
            os.environ.get("OPENAI_MODEL_CONTACTS", OPENAI_MODEL_CONTACTS),
            os.environ.get("OPENAI_MODEL_EXPENSES", OPENAI_MODEL_EXPENSES),
            model_fast,
        )
    else:
        log.info("Extraction model (all passes): %s", model_fast)
    pdf_file: Any | None = None
    document_text: str | None = None

    try:
        page_count = _get_pdf_page_count(pdf_path)
    except Exception:
        page_count = -1

    try:
        document_text, page_count, mode = load_document_text(
            pdf_path,
            max_chars=MAX_DOCUMENT_TEXT_CHARS,
            include_pymupdf_tables=True,
        )
        log.info(
            "%s pages — %s text mode with tables (%s chars)",
            page_count, mode, len(document_text),
        )
    except Exception as e:
        log.warning("OCR/text extraction failed (%s). Falling back to PDF upload.", e)

    if document_text is None:
        pdf_file = client.files.upload(file=pdf_path)

    by_name:      dict[str, dict]      = {}
    batch_usages: list[dict[str, int]] = []
    api_calls = 0

    pass_defs = _split_into_passes_compact(plan)
    if not pass_defs:
        raise ValueError("No attributes to extract")

    for pass_id, label, payload in pass_defs:
        provider, pass_model = _model_for_pass(
            pass_id,
            override=model_override,
            fast=model_fast,
            quality=model_quality,
            hybrid=hybrid,
        )
        pass_sys = _system_instruction_for_pass(pass_id)
        anchor_block = ""
        if pass_id.startswith("repeatables") or pass_id == "clauses":
            if by_name:
                anchor_block = _build_anchor_block(by_name)

        if pass_id == "core_full":
            unique = payload["unique"]
            rep = payload.get("repeatable") or {}
            log.info(
                "%s call [%s]: %s (%s flat + %s repeatable groups)",
                provider.title(), pass_model, label, len(unique), len(rep),
            )
            prompt = _build_compact_core_prompt(
                unique, rep, label, anchor_block=anchor_block,
            )
            schema = _compact_core_schema(unique, rep)
            data, usage = _gemini_call_json(
                client, pdf_file, prompt, schema, pass_model,
                document_text=document_text,
                max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
                system_instruction=pass_sys,
            )
            items = data.get("extractions", [])
            if isinstance(items, list):
                _merge_items(by_name, items)
            if rep:
                _merge_compact_groups(by_name, rep, data)
            # If the response truncated, re-request the fixed (named) core attributes that
            # never came back. Repeatable groups above are left to salvage (open-ended count).
            batch_usages.extend(_gapfill_unique_names(
                unique, by_name, label=label, provider=provider, pass_model=pass_model,
                pass_sys=pass_sys, client=client, pdf_file=pdf_file, openai_client=openai_client,
                document_text=document_text, anchor_block=anchor_block,
            ))
        elif pass_id.startswith("repeatables"):
            rep = payload["repeatable"]
            log.info(
                "%s call [%s]: %s (%s repeatable groups)",
                provider.title(), pass_model, label, len(rep),
            )
            if pass_id == "repeatables_expenses":
                prompt = _build_compact_expenses_prompt(
                    rep, label, anchor_block=anchor_block,
                )
                out_tokens = EXPENSES_MAX_OUTPUT_TOKENS
            else:
                hints = _load_field_hints()
                contact_note = (hints.get("contacts_group_note") or "").strip()
                focus = (
                    "CONTACTS + AREA:\n"
                    + _repeatable_slots_prompt_block()
                    + "- CORE PARTIES ONLY: Landlord/Lessor, Tenant/Lessee, Payment Contact, Notice Copy "
                    "for those parties. Do NOT extract contractors, utilities, property managers, county "
                    "attorneys, exhibit vendors, or brokers unless they are a core party notice addressee.\n"
                    + "- ONE items[] object per contact = ALL fields in ONE object (Street, City, Zip "
                    "together). NEVER put Street, City, Zip in separate items.\n"
                    + "- SLOT ORDER: .0 primary Landlord/Lessor → .1 primary Tenant/Lessee → .2+ "
                    "additional Notice Copy or Payment Contact roles for core parties only.\n"
                    + "- Area: .0, .1, .2+ for each distinct premises/suite/space row.\n"
                )
                if contact_note:
                    focus += contact_note + "\n"
                prompt = _build_compact_repeatables_prompt(
                    rep, label, anchor_block=anchor_block,
                    focus_line=focus,
                )
                out_tokens = CONTACTS_MAX_OUTPUT_TOKENS
            schema = _compact_contacts_schema(rep)
            if provider == "openai" and document_text is not None:
                if openai_client is None:
                    openai_client = _openai_client()
                data, usage = _openai_call_json(
                    openai_client, prompt, schema, pass_model,
                    document_text=document_text,
                    max_output_tokens=out_tokens,
                    system_instruction=pass_sys,
                )
            else:
                if provider == "openai":
                    log.warning("OpenAI pass %s: no document text, falling back to Gemini", pass_id)
                gemini_fallback_model = pass_model if provider == "gemini" else model_fast
                data, usage = _gemini_call_json(
                    client, pdf_file, prompt, schema, gemini_fallback_model,
                    document_text=document_text,
                    max_output_tokens=out_tokens,
                    system_instruction=pass_sys,
                )
            _merge_compact_groups(by_name, rep, data)
        else:
            unique = payload["unique"]
            log.info(
                "%s call [%s]: %s (%s attributes)",
                provider.title(), pass_model, label, len(unique),
            )
            if provider == "openai":
                if document_text is None:
                    raise ValueError("OpenAI clauses pass requires document text")
                if openai_client is None:
                    openai_client = _openai_client()
                prompt = _build_prompt(unique, label, anchor_block=anchor_block)
                data, usage = _openai_call_json(
                    openai_client, prompt, EXTRACTION_SCHEMA, pass_model,
                    document_text=document_text,
                    max_output_tokens=CLAUSE_MAX_OUTPUT_TOKENS,
                    system_instruction=pass_sys,
                )
                items = data.get("extractions", [])
                if not isinstance(items, list):
                    raise ValueError(f"{label}: invalid OpenAI response — expected extractions list")
                _merge_items(by_name, items)
            else:
                items, usage = _gemini_call(
                    client, pdf_file, unique, label, pass_model,
                    anchor_block=anchor_block,
                    document_text=document_text,
                    max_output_tokens=CLAUSE_MAX_OUTPUT_TOKENS,
                    system_instruction=pass_sys,
                )
                _merge_items(by_name, items)
            # Clauses are a fixed, named set — re-request any that truncated away.
            batch_usages.extend(_gapfill_unique_names(
                unique, by_name, label=label, provider=provider, pass_model=pass_model,
                pass_sys=pass_sys, client=client, pdf_file=pdf_file, openai_client=openai_client,
                document_text=document_text, anchor_block=anchor_block,
            ))

        api_calls += 1
        batch_usages.append({
            **usage,
            "model": pass_model,
            "provider": provider,
            "pass_id": pass_id,
        })
        log.info(
            "  tokens — in: %s, out: %s, thinking: %s",
            usage["input_tokens"], usage["output_tokens"], usage["thinking_tokens"],
        )

    rows = _rows_for_output_slots(plan, by_name)
    backfill_text = document_text

    if backfill_text is None:
        try:
            backfill_text, _, _, _ = load_document_body(
                pdf_path,
                max_chars=MAX_DOCUMENT_TEXT_CHARS,
                include_pymupdf_tables=True,
            )
            log.info("Loaded PDF text for context/page backfill (%s chars)", len(backfill_text))
        except Exception as e:
            log.warning("Context backfill text extract failed: %s", e)
            backfill_text = None

    rows = apply_post_processing(rows, document_text=backfill_text)
    _finalize_page_numbers(
        rows,
        pdf_page_count=page_count if page_count > 0 else None,
        text_has_page_markers=bool(
            backfill_text and "===== PAGE" in backfill_text
        ),
    )

    total_in       = sum(u["input_tokens"]   for u in batch_usages)
    total_out      = sum(u["output_tokens"]  for u in batch_usages)
    total_thinking = sum(u["thinking_tokens"] for u in batch_usages)
    found          = sum(
        1 for r in rows
        if r["value"] not in (NOT_FOUND, NA_VALUE, "")
    )
    cost_usd = sum(
        _estimate_cost_usd(
            u["input_tokens"], u["output_tokens"], u["thinking_tokens"],
            model=u.get("model", ""),
        )
        for u in batch_usages
    )
    if model_override:
        model_label = model_override
    elif hybrid:
        model_label = (
            f"hybrid (quality={model_quality}: core+dates+options+deposit; "
            f"openai={OPENAI_MODEL_CONTACTS}/{OPENAI_MODEL_EXPENSES}: contacts,expenses; "
            f"fast={model_fast}: clauses)"
        )
    else:
        model_label = model_fast
    pass_model_summary = "; ".join(
        f"{u.get('pass_id', '?')}={u.get('model', '?')}"
        for u in batch_usages
    )

    run_stats = {
        "model":             model_label,
        "pass_models":       pass_model_summary,
        "attribute_set":     plan.label,
        "api_calls":         api_calls,
        "input_tokens":      total_in,
        "output_tokens":     total_out,
        "thinking_tokens":   total_thinking,
        "total_tokens":      total_in + total_out + total_thinking,
        "estimated_cost_usd": cost_usd,
        "fields_populated":  found,
        "fields_total":      len(rows),
    }
    log.info(
        "Done: %s/%s fields populated | ~$%.4f USD (%s tokens)",
        found, len(rows),
        run_stats["estimated_cost_usd"], run_stats["total_tokens"],
    )
    return rows, run_stats, backfill_text

# ---------------------------------------------------------------------------
# Lease package (multi-document)
# ---------------------------------------------------------------------------

_DOC_TYPE_ORDER = {"Base": 0, "Assignment": 1, "Amendment": 2, "Renewal": 3}

def _doc_type_sort_rank(doc_type: str) -> int:
    return _DOC_TYPE_ORDER.get(doc_type, 99)

@dataclass
class LeaseDocument:
    pdf: Path
    doc_type: str
    doc_date: date | None
    sort_key: tuple[date, str]

    @property
    def type_slug(self) -> str:
        return self.doc_type.lower().replace(" ", "_")

def _parse_doc_date_from_name(filename: str) -> date | None:
    m = _DOC_DATE_RE.search(filename)
    if not m:
        return None
    day = int(m.group(1))
    mon = _MONTHS.get(m.group(2).lower().rstrip("."), 0)
    year = int(m.group(3))
    if not mon:
        return None
    try:
        return date(year, mon, day)
    except ValueError:
        return None

def _infer_doc_type(filename: str, *, default_base: bool) -> str:
    upper = filename.upper()
    if "AMENDMENT" in upper or "AMEND " in upper or "AMEND_" in upper:
        return "Amendment"
    if "ADDENDUM" in upper:
        return "Amendment"
    if "RENEWAL" in upper:
        return "Renewal"
    if "ASSIGNMENT" in upper:
        return "Assignment"
    if any(k in upper for k in ("LEASE", "AGREEMENT", "LICENCE", "LICENSE")):
        return "Base"
    return "Base" if default_base else "Amendment"

def _matched_keywords_from_name(filename: str) -> str:
    upper = filename.upper()
    tags: list[str] = []
    for kw in ("LEASE", "AMENDMENT", "ASSIGNMENT", "AGREEMENT", "RENEWAL", "LICENCE", "LICENSE"):
        if kw in upper:
            tags.append(kw)
    return ", ".join(tags) if tags else ""

def discover_lease_documents(lease_dir: Path) -> list[LeaseDocument]:
    lease_dir = lease_dir.resolve()
    if not lease_dir.is_dir():
        raise FileNotFoundError(f"Lease folder not found: {lease_dir}")
    pdfs = sorted(lease_dir.glob("*.pdf"), key=lambda p: p.name.lower())
    if not pdfs:
        raise FileNotFoundError(f"No PDF files in {lease_dir}")

    docs: list[LeaseDocument] = []
    for i, pdf in enumerate(pdfs):
        doc_date = _parse_doc_date_from_name(pdf.name)
        doc_type = _infer_doc_type(pdf.name, default_base=(i == 0))
        fallback = date(9999, 12, 31) if doc_date is None else doc_date
        docs.append(LeaseDocument(
            pdf=pdf,
            doc_type=doc_type,
            doc_date=doc_date,
            sort_key=(fallback, pdf.name.lower()),
        ))

    # Order by lease lifecycle (Base → Assignment → Amendment → Renewal), then by date parsed from
    # the filename, then alphabetically for ties. This makes "newest wins" chronologically correct
    # so amendments/renewals override the base. Matches the original engine's ordering.
    docs.sort(key=lambda d: (
        _doc_type_sort_rank(d.doc_type),
        d.sort_key[0],
        d.sort_key[1],
    ))
    if docs and docs[0].doc_type != "Base":
        log.warning(
            "First document after sort is %s (expected Base): %s",
            docs[0].doc_type, docs[0].pdf.name,
        )
    return docs

def _apply_lease_dqc(rows: list[dict], doc: LeaseDocument, *, mode: str = "extraction") -> None:
    values = {
        "Lease_DQC.document_type": doc.doc_type,
        "Lease_DQC.filename": doc.pdf.name,
        "Lease_DQC.full_path_filename": str(doc.pdf.resolve()),
        "Lease_DQC.matched_keywords": _matched_keywords_from_name(doc.pdf.name),
        "Lease_DQC.mode": mode,
    }
    by_name = {r["attribute_name"]: r for r in rows}
    for attr in LEASE_DQC_ATTRS:
        val = values.get(attr, "")
        if attr in by_name:
            by_name[attr]["value"] = val
            by_name[attr]["confidence_score"] = ""
            by_name[attr]["context"] = f"Set by pipeline from {doc.pdf.name}"
            by_name[attr]["page_number"] = ""

def _apply_lease_dqc_merged(rows: list[dict], docs: list[LeaseDocument]) -> None:
    names = [d.pdf.name for d in docs]
    types = [d.doc_type for d in docs]
    kw_parts: list[str] = []
    for d in docs:
        kw_parts.extend(_matched_keywords_from_name(d.pdf.name).split(", "))
    keywords = ", ".join(dict.fromkeys(k.strip() for k in kw_parts if k.strip()))
    by_name = {r["attribute_name"]: r for r in rows}
    merged_vals = {
        "Lease_DQC.document_type": "Merged",
        "Lease_DQC.filename": "; ".join(names),
        "Lease_DQC.full_path_filename": "; ".join(str(d.pdf.resolve()) for d in docs),
        "Lease_DQC.matched_keywords": keywords,
        "Lease_DQC.mode": "lease_package_merge",
    }
    for attr, val in merged_vals.items():
        if attr in by_name:
            by_name[attr]["value"] = val
            by_name[attr]["confidence_score"] = ""
            by_name[attr]["context"] = f"Merged {len(docs)} documents: {', '.join(types)}"
            by_name[attr]["page_number"] = ""

def _parse_repeatable_slot(attr_name: str) -> tuple[str, int, str] | None:
    m = _REPEATABLE_SLOT_RE.match(attr_name)
    if not m:
        return None
    return m.group(1), int(m.group(2)), m.group(3)

def _renumber_repeatable_row(row: dict, new_slot: int) -> dict:
    parts = _parse_repeatable_slot(row["attribute_name"])
    if not parts:
        return row
    prefix, _, suffix = parts
    out = dict(row)
    out["attribute_name"] = f"{prefix}.{new_slot}.{suffix}"
    return out

def merge_abstraction_rows(
    doc_row_lists: list[list[dict]],
) -> list[dict]:
    """
    Merge per-document abstractions oldest → newest.
    Newest non-empty value wins for every attribute (including same repeatable slot).
    Empty values in a later doc do not erase an earlier populated value.
    """
    merged: dict[str, dict] = {}

    for doc_rows in doc_row_lists:
        for row in doc_rows:
            name = row["attribute_name"]
            val = row.get("value", "")
            if is_empty_value(val):
                if name not in merged:
                    merged[name] = dict(row)
                continue
            merged[name] = dict(row)

    order: list[str] = []
    seen: set[str] = set()
    for doc_rows in doc_row_lists:
        for row in doc_rows:
            n = row["attribute_name"]
            if n in merged and n not in seen:
                order.append(n)
                seen.add(n)
    for n in sorted(merged.keys()):
        if n not in seen:
            order.append(n)

    result = [merged[n] for n in order if not _EXPENSE_SLOT_RE.match(n)]
    result.extend(_merge_expense_slot_rows(doc_row_lists))
    return result

def _abstraction_xlsx_name(pdf: Path, *, label: str) -> str:
    """e.g. US097R01_BARRON_LEASE_dd_1 Oct 2015_lease_abstraction.xlsx"""
    return f"{pdf.stem}_{label}.xlsx"

def _per_doc_output_path(lease_out_dir: Path, doc: LeaseDocument) -> Path:
    return lease_out_dir / _abstraction_xlsx_name(doc.pdf, label="lease_abstraction")

def _final_merged_output_path(lease_out_dir: Path, docs: list[LeaseDocument]) -> Path:
    """Merged file named after the newest document (last in chronological order)."""
    latest = docs[-1]
    return lease_out_dir / _abstraction_xlsx_name(latest.pdf, label="final_abstraction")

def run_lease_package(lease_dir: Path) -> Path:
    """
    Discover PDFs in lease_dir, abstract each (oldest → newest).

    One PDF:  per-doc <stem>_lease_abstraction.xlsx only.
    Two+:     each <stem>_lease_abstraction.xlsx plus <latest_stem>_final_abstraction.xlsx.
    """
    lease_dir = lease_dir.resolve()
    lease_id = lease_dir.name
    docs = discover_lease_documents(lease_dir)
    plan = load_extraction_plan()
    lease_out = OUTPUT_DIR / lease_id
    lease_out.mkdir(parents=True, exist_ok=True)

    log.info(
        "Lease package %s: %s document(s) in %s",
        lease_id, len(docs), lease_dir,
    )
    for i, doc in enumerate(docs, start=1):
        d = doc.doc_date.isoformat() if doc.doc_date else "undated"
        log.info("  %s. [%s] %s — %s", i, doc.doc_type, d, doc.pdf.name)

    per_doc_outputs: list[Path] = []
    doc_row_lists: list[list[dict]] = []
    cached_doc_texts: list[str] = []

    for i, doc in enumerate(docs, start=1):
        log.info("--- Document %s/%s: %s ---", i, len(docs), doc.pdf.name)
        rows, stats, doc_text = extract(str(doc.pdf.resolve()), plan)
        _apply_lease_dqc(rows, doc)
        out_path = _per_doc_output_path(lease_out, doc)
        write_excel(rows, out_path)
        per_doc_outputs.append(out_path)
        doc_row_lists.append(rows)
        if doc_text:
            cached_doc_texts.append(doc_text)
        append_run_log(doc.pdf, out_path, stats)
        log.info("Saved per-doc → %s", out_path)

    if len(doc_row_lists) > 1:
        merged_rows = merge_abstraction_rows(doc_row_lists)
        merged_text = "\n\n".join(cached_doc_texts) if cached_doc_texts else None
        merged_rows = apply_merged_post_processing(
            merged_rows, docs, document_text=merged_text,
        )
        _apply_lease_dqc_merged(merged_rows, docs)
        final_path = _final_merged_output_path(lease_out, docs)
        write_excel(merged_rows, final_path)
        log.info("Saved merged → %s", final_path)
        log.info(
            "Lease package complete (%s documents) → %s per-doc + %s merged",
            len(docs), len(per_doc_outputs), final_path.name,
        )
        return final_path

    last_out = per_doc_outputs[-1]
    log.info(
        "Lease package complete (1 document) → %s",
        last_out,
    )
    return last_out

def append_run_log(pdf_path: Path, out_path: Path, stats: dict[str, Any]) -> None:
    lines = [
        "=" * 72,
        f"Timestamp:          {datetime.now().isoformat(timespec='seconds')}",
        f"Input PDF:          {pdf_path}",
        f"Output Excel:       {out_path}",
        f"Model:              {stats['model']}",
        f"Pass models:        {stats.get('pass_models', '')}",
        f"Attribute set:      {stats['attribute_set']}",
        f"API calls:          {stats.get('api_calls', NUM_PASSES)}",
        f"Input tokens:       {stats['input_tokens']:,}",
        f"Output tokens:      {stats['output_tokens']:,}",
        f"Thinking tokens:    {stats['thinking_tokens']:,}",
        f"Total tokens:       {stats['total_tokens']:,}",
        f"Estimated cost USD: ${stats['estimated_cost_usd']:.6f}",
        f"Fields populated:   {stats['fields_populated']} / {stats['fields_total']}",
        "",
    ]
    with RUNS_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    log.info("Run log appended → %s", RUNS_LOG_PATH)

def _sanitize_excel_cell_text(value: Any) -> str:
    """Strip illegal .xlsx control characters for export."""
    if value is None:
        return ""
    text = str(value)
    return ILLEGAL_CHARACTERS_RE.sub("", text)

def write_excel(rows: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Lease Abstraction"
    ws.append(OUTPUT_COLUMNS)
    for r in rows:
        ws.append([
            _sanitize_excel_cell_text(r["attribute_name"]),
            _sanitize_excel_cell_text(r["value"]),
            _excel_confidence_cell(r),
            _sanitize_excel_cell_text(r["context"]),
            _sanitize_excel_cell_text(r["page_number"]),
        ])
    wb.save(path)
    return path

def run_abstraction(pdf: Path, out: Path | None = None) -> Path:
    pdf = pdf.resolve()
    if not pdf.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf}")

    plan = load_extraction_plan()
    out    = out or (OUTPUT_DIR / f"{pdf.stem}_abstraction.xlsx")
    out    = Path(out).resolve()

    log.info("%s | PDF: %s", plan.label, pdf.name)

    rows, stats, _doc_text = extract(str(pdf), plan)
    doc_date = _parse_doc_date_from_name(pdf.name)
    fallback = doc_date or date(9999, 12, 31)
    meta = LeaseDocument(
        pdf=pdf,
        doc_type=_infer_doc_type(pdf.name, default_base=True),
        doc_date=doc_date,
        sort_key=(fallback, pdf.name.lower()),
    )
    _apply_lease_dqc(rows, meta)
    stats["attribute_set"] = plan.label
    write_excel(rows, out)
    append_run_log(pdf, out, stats)
    log.info("Saved → %s", out)
    return out

def resolve_lease_dir(
    *,
    lease_dir: str | None = None,
    lease_id: str | None = None,
    positional: str | None = None,
) -> Path:
    """
    Resolve input folder for a lease package.
    Folder name = lease id (e.g. input_pdfs/US097R01_BARRON/).
    """
    if lease_dir:
        path = Path(lease_dir)
        if not path.is_absolute():
            path = ROOT / path
        return path.resolve()

    lid = (lease_id or positional or "").strip()
    if not lid:
        raise ValueError("Lease id or --lease-dir is required (e.g. python main.py --lease US097R01_BARRON)")

    if lid.lower().endswith(".pdf"):
        raise ValueError(
            "PDF path is no longer accepted. Put PDF(s) in a lease folder and run:\n"
            f"  python main.py --lease-dir input_pdfs\\<LEASE_ID>\n"
            f"  python main.py --lease <LEASE_ID>"
        )

    candidate = Path(lid)
    if candidate.is_absolute() and candidate.is_dir():
        return candidate.resolve()

    bare_id = lid.replace("\\", "/").strip("/")
    if "/" not in bare_id and (INPUT_PDFS_DIR / bare_id).is_dir():
        return (INPUT_PDFS_DIR / bare_id).resolve()

    if candidate.is_dir():
        return (ROOT / candidate).resolve()
    if candidate.parent != Path(".") and (ROOT / candidate).is_dir():
        return (ROOT / candidate).resolve()

    raise FileNotFoundError(
        f"Lease folder not found for '{lid}'. Expected:\n"
        f"  {INPUT_PDFS_DIR / lid}\n"
        f"Create the folder and place one or more PDFs inside."
    )

def main() -> int:
    """
    Run abstraction for a lease folder (one PDF or base + amendments).

      python main_clean.py --lease <LEASE_ID>
      python main_clean.py --lease-dir input_pdfs/<LEASE_ID>

    Output (1 PDF):   output/<LEASE_ID>/<stem>_lease_abstraction.xlsx
    Output (2+ PDFs): per-doc xlsx + <latest_stem>_final_abstraction.xlsx
    Log:              runs_log.txt (per-document runs only)
    """
    lease_dir_arg: str | None = None
    lease_id_arg: str | None = None
    positional: str | None = None
    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        if argv[i] in ("-h", "--help"):
            print(main.__doc__ or "")
            return 0
        if argv[i] == "--lease-dir" and i + 1 < len(argv):
            lease_dir_arg = argv[i + 1]
            i += 2
            continue
        if argv[i] == "--lease" and i + 1 < len(argv):
            lease_id_arg = argv[i + 1]
            i += 2
            continue
        if not argv[i].startswith("-"):
            positional = argv[i]
        i += 1

    try:
        folder = resolve_lease_dir(
            lease_dir=lease_dir_arg,
            lease_id=lease_id_arg,
            positional=positional,
        )
        run_lease_package(folder)
        return 0

    except SystemExit as e:
        log.error("%s", e)
        return 1
    except EnvironmentError as e:
        log.error("%s", e)
        return 1
    except Exception as e:
        log.exception("%s", e)
        return 1

if __name__ == "__main__":
    sys.exit(main())

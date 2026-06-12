import os
import re
import csv
import io
import base64
import json
import uuid
import unicodedata
import subprocess
import tempfile
from difflib import SequenceMatcher
from datetime import datetime, timezone

import fitz  # PyMuPDF
from anthropic import AsyncAnthropic

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Model selection is fully env-driven (no hardcoding in logic). ANTHROPIC_MODEL is the base
# default; the per-task vars below override it so we can use a cheap text model for typed PDFs
# and a stronger vision model for scanned PDFs. All have sensible fallbacks to ANTHROPIC_MODEL.
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
MODEL_TYPED     = os.getenv("ANTHROPIC_MODEL_TYPED", MODEL)        # digital PDFs (text layer)
MODEL_SCANNED   = os.getenv("ANTHROPIC_MODEL_SCANNED", MODEL)      # scanned PDFs (vision)
MODEL_TRANSLATE = os.getenv("ANTHROPIC_MODEL_TRANSLATE", MODEL_TYPED)  # clause translation (text)

# Model pricing per 1M tokens: (input, output). Matched by substring against MODEL, so both
# dated IDs (claude-sonnet-4-20250514) and aliases (claude-sonnet-4-6) resolve correctly.
_MODEL_PRICING = {
    "claude-haiku-4":  (0.80, 4.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-opus-4":   (15.00, 75.00),
}
_DEFAULT_PRICING = (3.00, 15.00)  # fall back to Sonnet rates if unknown


def _prices_per_m(model: str) -> tuple[float, float]:
    for key, prices in _MODEL_PRICING.items():
        if key in model:
            return prices
    return _DEFAULT_PRICING

# ── Scanned PDF rendering config ──────────────────────────────────────────────
# DPI for rendering scanned pages (higher = sharper, more tokens).
SCANNED_DPI           = int(os.getenv("SCANNED_DPI", "200"))
# Max strips per page when document is small enough. Auto-reduced for large docs.
SCANNED_PAGE_STRIPS   = int(os.getenv("SCANNED_PAGE_STRIPS", "3"))
# Overlap in pixels between adjacent strips.
SCANNED_STRIP_OVERLAP = int(os.getenv("SCANNED_STRIP_OVERLAP", "50"))
# Max image tokens per batch (leave headroom for prompt + output).
SCANNED_MAX_TOKENS    = int(os.getenv("SCANNED_MAX_TOKENS", "150000"))
# Max output tokens for extraction response. 130 attributes × ~120 chars each ≈ 32k tokens.
EXTRACTION_MAX_OUTPUT_TOKENS = int(os.getenv("EXTRACTION_MAX_OUTPUT_TOKENS", "32000"))
# Approx image tokens per page per strip at SCANNED_DPI (A4 @ 200dpi).
_TOKENS_PER_STRIP     = 1828


def _plan_batches(page_count: int) -> list[tuple[int, int, int]]:
    """Return a list of (start_page, end_page, strips) for batching a scanned document.
    Auto-reduces strips so each batch stays within SCANNED_MAX_TOKENS.
    start_page and end_page are 1-based inclusive."""
    prompt_tokens = 5_000
    available = SCANNED_MAX_TOKENS - prompt_tokens

    # Try strips from max down to 1; pick the highest that fits a reasonable batch size.
    for strips in range(SCANNED_PAGE_STRIPS, 0, -1):
        tokens_per_page = _TOKENS_PER_STRIP * strips
        pages_per_batch = max(1, available // tokens_per_page)
        if pages_per_batch >= 10 or strips == 1:
            break

    batches = []
    for start in range(1, page_count + 1, pages_per_batch):
        end = min(start + pages_per_batch - 1, page_count)
        batches.append((start, end, strips))
    return batches

# Scripts where Tesseract is unreliable for highlighting — skip OCR and go straight to
# Claude-vision for these. Comma-separated Unicode block names. Arabic covers Urdu/Persian too.
# Set to empty string "" to disable and always try OCR first.
_OCR_SKIP_SCRIPTS = {s.strip().lower() for s in os.getenv("OCR_SKIP_SCRIPTS", "arabic").split(",") if s.strip()}

# Unicode ranges for each named script in OCR_SKIP_SCRIPTS.
_SKIP_SCRIPT_RANGES: dict[str, list[tuple[int, int]]] = {
    "arabic": [(0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)],
    "tibetan": [(0x0F00, 0x0FFF)],
    "myanmar": [(0x1000, 0x109F)],
}

def _should_skip_ocr(source_text: str) -> bool:
    """Return True if the source_clause contains characters from a script that Tesseract
    handles poorly — these go straight to Claude-vision for highlighting."""
    for script in _OCR_SKIP_SCRIPTS:
        ranges = _SKIP_SCRIPT_RANGES.get(script, [])
        for ch in (source_text or ""):
            cp = ord(ch)
            if any(lo <= cp <= hi for lo, hi in ranges):
                return True
    return False

# ── OCR config (scanned PDFs — highlighting only) ─────────────────────────────
# Tesseract is invoked as a local CLI (no Python binding, no paid API).
OCR_DPI = int(os.getenv("OCR_DPI", "200"))
# Minimum match confidence to trust an OCR-located box. Below this we return nothing
# (and the caller falls back to vision) — never show a guessed box in a legal tool.
OCR_MIN_QUALITY = float(os.getenv("OCR_MIN_QUALITY", "0.72"))

# Unicode block -> candidate Tesseract language(s), in preference order. Generic across ALL
# world scripts (not just Indian). One model per script reads every glyph of that script
# (e.g. `hin` reads all Devanagari, `ara` reads all Arabic-script incl. Urdu/Persian), so a
# few candidates per block are enough. The script of the clause (which Claude returns in the
# original language) picks the model; we then keep only packs that are actually installed.
_SCRIPT_RANGES: list[tuple[int, int, tuple[str, ...]]] = [
    # ── Indian subcontinent ──
    (0x0900, 0x097F, ("hin",)),                 # Devanagari (Hindi/Marathi/Sanskrit/Nepali)
    (0x0980, 0x09FF, ("ben", "asm")),           # Bengali / Assamese
    (0x0A00, 0x0A7F, ("pan",)),                 # Gurmukhi (Punjabi)
    (0x0A80, 0x0AFF, ("guj",)),                 # Gujarati
    (0x0B00, 0x0B7F, ("ori",)),                 # Odia
    (0x0B80, 0x0BFF, ("tam",)),                 # Tamil
    (0x0C00, 0x0C7F, ("tel",)),                 # Telugu
    (0x0C80, 0x0CFF, ("kan",)),                 # Kannada
    (0x0D00, 0x0D7F, ("mal",)),                 # Malayalam
    (0x0D80, 0x0DFF, ("sin",)),                 # Sinhala
    # ── Middle East ──
    (0x0600, 0x06FF, ("urd", "ara", "fas")),    # Arabic script (Urdu/Arabic/Persian)
    (0x0750, 0x077F, ("urd", "ara")),           # Arabic Supplement
    (0xFB50, 0xFDFF, ("ara", "urd")),           # Arabic Presentation Forms-A
    (0xFE70, 0xFEFF, ("ara", "urd")),           # Arabic Presentation Forms-B
    (0x0590, 0x05FF, ("heb",)),                 # Hebrew
    # ── Europe / Central Asia ──
    (0x0400, 0x04FF, ("rus", "ukr")),           # Cyrillic
    (0x0370, 0x03FF, ("ell",)),                 # Greek
    (0x0530, 0x058F, ("hye",)),                 # Armenian
    (0x10A0, 0x10FF, ("kat",)),                 # Georgian
    # ── East / Southeast Asia ──
    (0x4E00, 0x9FFF, ("chi_sim", "chi_tra")),   # CJK Han
    (0x3400, 0x4DBF, ("chi_sim", "chi_tra")),   # CJK Ext-A
    (0xF900, 0xFAFF, ("chi_sim", "chi_tra")),   # CJK Compatibility
    (0x3040, 0x30FF, ("jpn",)),                 # Hiragana / Katakana
    (0xAC00, 0xD7A3, ("kor",)),                 # Hangul syllables
    (0x1100, 0x11FF, ("kor",)),                 # Hangul Jamo
    (0x0E00, 0x0E7F, ("tha",)),                 # Thai
    (0x0E80, 0x0EFF, ("lao",)),                 # Lao
    (0x1000, 0x109F, ("mya",)),                 # Myanmar
    (0x1780, 0x17FF, ("khm",)),                 # Khmer
    (0x0F00, 0x0FFF, ("bod",)),                 # Tibetan
    (0x1200, 0x137F, ("amh", "tir")),           # Ethiopic
    # ── Extended Latin (accented European: French/German/Vietnamese/…) ──
    # Can't tell the language from codepoints; the generic Latin-script model handles them.
    (0x00C0, 0x024F, ("script/Latin",)),
    (0x1E00, 0x1EFF, ("script/Latin",)),
]

# Cap how many non-English models we pass at once (more = slower OCR, lower accuracy).
_MAX_OCR_LANGS = 3

# Maps Claude attribute name → (category, attribute_key, data_type)
ATTR_MAP = {
    # ── Core Lease Terms ──────────────────────────────────────────────────────
    "Property name":                    ("Core Lease Terms", "property_name",               "text"),
    "Street":                           ("Core Lease Terms", "street",                      "text"),
    "Street no.":                       ("Core Lease Terms", "street_no",                   "text"),
    "Postal code":                      ("Core Lease Terms", "postal_code",                 "text"),
    "City":                             ("Core Lease Terms", "city",                        "text"),
    "County":                           ("Core Lease Terms", "county",                      "text"),
    "State / province":                 ("Core Lease Terms", "state_province",              "text"),
    "Country":                          ("Core Lease Terms", "country",                     "text"),
    "Building Type":                    ("Core Lease Terms", "building_type",               "text"),
    "Total building area":              ("Core Lease Terms", "total_building_area",         "number"),
    "UOM":                              ("Core Lease Terms", "total_building_area_uom",     "text"),
    "Landlord Name":                    ("Core Lease Terms", "landlord_name",               "text"),
    "Tenant Name":                      ("Core Lease Terms", "tenant_name",                 "text"),
    "Effective Date":                   ("Core Lease Terms", "effective_date",              "date"),
    "Execution Date":                   ("Core Lease Terms", "execution_date",              "date"),
    "Original Commencement Date":       ("Core Lease Terms", "original_commencement_date",  "date"),
    "Rent Commencement Date":           ("Core Lease Terms", "rent_commencement_date",      "date"),
    "Current Commencement Date":        ("Core Lease Terms", "commencement_date",           "date"),
    "Current Expiration Date":          ("Core Lease Terms", "expiry_date",                 "date"),
    "Original Expiration Date":         ("Core Lease Terms", "original_expiry_date",        "date"),
    "Possession Date":                  ("Core Lease Terms", "possession_date",             "date"),
    "Delivery Date":                    ("Core Lease Terms", "delivery_date",               "date"),
    "Term Duration":                    ("Core Lease Terms", "term_duration",               "text"),
    "Lease Status":                     ("Core Lease Terms", "lease_status",                "text"),
    "Unit/suite number":                ("Core Lease Terms", "unit_suite_number",           "text"),
    "Type":                             ("Core Lease Terms", "lease_type",                  "text"),
    "Gross area":                       ("Core Lease Terms", "gross_area",                  "number"),
    "Gross Area UOM":                   ("Core Lease Terms", "gross_area_uom",              "text"),
    "Net area":                         ("Core Lease Terms", "net_area",                    "number"),
    "Net Area UOM":                     ("Core Lease Terms", "net_area_uom",                "text"),
    "Floor no.":                        ("Core Lease Terms", "floor_no",                    "text"),
    # ── Financial Obligations ─────────────────────────────────────────────────
    "Start date":                       ("Financial Obligations", "rent_start_date",           "date"),
    "End date":                         ("Financial Obligations", "rent_end_date",             "date"),
    "Duration":                         ("Financial Obligations", "rent_duration",             "text"),
    "PRS":                              ("Financial Obligations", "prs",                       "text"),
    "Base Year":                        ("Financial Obligations", "base_year",                 "text"),
    "Base Rent - Start date":           ("Financial Obligations", "base_rent_start_date",      "date"),
    "Base Rent - End date":             ("Financial Obligations", "base_rent_end_date",        "date"),
    "Monthly Amount":                   ("Financial Obligations", "base_rent_monthly",         "number"),
    "Monthly Amount per SF":            ("Financial Obligations", "monthly_amount_per_sf",     "number"),
    "Annual Amount":                    ("Financial Obligations", "base_rent_annual",          "number"),
    "Annual amount per SF":             ("Financial Obligations", "annual_amount_per_sf",      "number"),
    "Currency":                         ("Financial Obligations", "currency",                  "text"),
    "On Day":                           ("Financial Obligations", "payment_day",               "text"),
    "Payment Frequency":                ("Financial Obligations", "payment_frequency",         "text"),
    "Increase amount":                  ("Financial Obligations", "escalation_rate_pct",       "percentage"),
    "Increase amount Unit":             ("Financial Obligations", "escalation_amount_unit",    "text"),
    "Increase amount per area":         ("Financial Obligations", "escalation_amount_per_area","number"),
    "Increase amount per area Unit":    ("Financial Obligations", "escalation_amount_per_area_unit", "text"),
    "Basis of increase":                ("Financial Obligations", "basis_of_increase",         "text"),
    "Rent Increase Start date":         ("Financial Obligations", "rent_increase_start_date",  "date"),
    "Rent Increase End date":           ("Financial Obligations", "rent_increase_end_date",    "date"),
    "Interval":                         ("Financial Obligations", "escalation_interval",       "text"),
    "Base Rent Comments":               ("Financial Obligations", "base_rent_comments",        "text"),
    # ── CAM and Operating Expenses ────────────────────────────────────────────
    "Operating Expenses":               ("CAM and Operating Expenses", "operating_expenses",   "text"),
    "RE Taxes":                         ("CAM and Operating Expenses", "re_taxes",             "text"),
    "Property Insurance":               ("CAM and Operating Expenses", "property_insurance",   "text"),
    "Parking":                          ("CAM and Operating Expenses", "parking",              "text"),
    "Signage":                          ("CAM and Operating Expenses", "signage",              "text"),
    "Utilities":                        ("CAM and Operating Expenses", "utilities_included",   "text"),
    "Electricals and Lightings":        ("CAM and Operating Expenses", "electricals_lightings","text"),
    "Plumbing":                         ("CAM and Operating Expenses", "plumbing",             "text"),
    "Landscaping":                      ("CAM and Operating Expenses", "landscaping",          "text"),
    "Snow Removal":                     ("CAM and Operating Expenses", "snow_removal",         "text"),
    "HVAC":                             ("CAM and Operating Expenses", "hvac_included",        "text"),
    "Roof":                             ("CAM and Operating Expenses", "roof",                 "text"),
    "Sewage":                           ("CAM and Operating Expenses", "sewage",               "text"),
    # ── Restrictive Clauses ───────────────────────────────────────────────────
    "Assignment/Sublet":                ("Restrictive Clauses", "assignment_permitted",        "text"),
    "Alterations":                      ("Restrictive Clauses", "alterations",                "text"),
    "Default":                          ("Restrictive Clauses", "default_clause",             "text"),
    "Estoppel":                         ("Restrictive Clauses", "estoppel",                   "text"),
    "Business Hours":                   ("Restrictive Clauses", "business_hours",             "text"),
    "Financial Statement":              ("Restrictive Clauses", "financial_statement",        "text"),
    "Late Charges":                     ("Restrictive Clauses", "late_charges",               "text"),
    "Repair and Maintenance":           ("Restrictive Clauses", "repairs_maintenance",        "text"),
    "Insurance Requirements":           ("Restrictive Clauses", "insurance_requirements",     "text"),
    "Surrender":                        ("Restrictive Clauses", "surrender",                  "text"),
    "Holdover":                         ("Restrictive Clauses", "holdover_rate_pct",          "text"),
    "Permitted Use":                    ("Restrictive Clauses", "permitted_use",              "text"),
    "Restricted Uses":                  ("Restrictive Clauses", "restricted_uses",            "text"),
    "Prohibited Uses":                  ("Restrictive Clauses", "prohibited_uses",            "text"),
    "Exclusive Use":                    ("Restrictive Clauses", "exclusive_use",              "text"),
    "Percentage Rent (Payment)":        ("Restrictive Clauses", "percentage_rent_payment",    "text"),
    "Gross Sales (Reporting)":          ("Restrictive Clauses", "gross_sales_reporting",      "text"),
    "Go dark":                          ("Restrictive Clauses", "go_dark",                    "text"),
    "Co-Tenancy":                       ("Restrictive Clauses", "co_tenancy",                 "text"),
    "Radius Restrictions":              ("Restrictive Clauses", "radius_restriction_miles",   "text"),
    "Brokers":                          ("Restrictive Clauses", "brokers",                    "text"),
    "Notices":                          ("Restrictive Clauses", "notices",                    "text"),
    "Governing Law":                    ("Restrictive Clauses", "governing_law",              "text"),
    # ── Critical Dates ────────────────────────────────────────────────────────
    "Renewal Option":                   ("Critical Dates", "renewal_options_count",           "text"),
    "Auto-Renewal Option":              ("Critical Dates", "auto_renewal_option",             "text"),
    "Termination Option - One-Time":    ("Critical Dates", "termination_option_one_time",     "text"),
    "Termination Option - Ongoing":     ("Critical Dates", "termination_option_ongoing",      "text"),
    "Expansion Option":                 ("Critical Dates", "expansion_option",                "text"),
    "Contraction Option":               ("Critical Dates", "contraction_option",              "text"),
    "ROFO":                             ("Critical Dates", "rofo",                            "text"),
    "ROFR":                             ("Critical Dates", "rofr",                            "text"),
    "Purchase":                         ("Critical Dates", "purchase_option",                 "text"),
    "Relocation":                       ("Critical Dates", "relocation_option",               "text"),
    "Tenant Improvement Allowance":     ("Critical Dates", "tenant_improvement_allowance",    "text"),
    # ── Security Deposit ──────────────────────────────────────────────────────
    "Security Deposit Type":            ("Security Deposit", "security_deposit_type",         "text"),
    "Security Deposit Amount":          ("Security Deposit", "security_deposit",              "number"),
    "Security Deposit Currency":        ("Security Deposit", "security_deposit_currency",     "text"),
    "Payment Date":                     ("Security Deposit", "security_deposit_payment_date", "date"),
    "Return Due Date":                  ("Security Deposit", "security_deposit_return_date",  "date"),
    "Security Deposit Comments":        ("Security Deposit", "security_deposit_comments",     "text"),
    # ── Allowances ────────────────────────────────────────────────────────────
    "Allowance Type":                   ("Allowances", "allowance_type",                     "text"),
    "Allowance Amount":                 ("Allowances", "allowance_amount",                   "number"),
    "Payment Deadline":                 ("Allowances", "allowance_payment_deadline",         "date"),
    "Allowance Comments":               ("Allowances", "allowance_comments",                 "text"),
    # ── Contacts ──────────────────────────────────────────────────────────────
    "Contact type":                     ("Contacts", "contact_type",                         "text"),
    "Name":                             ("Contacts", "contact_name",                         "text"),
    "Attention":                        ("Contacts", "contact_attention",                    "text"),
    "Care of":                          ("Contacts", "contact_care_of",                      "text"),
    "DBA":                              ("Contacts", "contact_dba",                          "text"),
    "Contacts - Street":                ("Contacts", "contact_street",                       "text"),
    "Contacts - Street no.":            ("Contacts", "contact_street_no",                    "text"),
    "Suite":                            ("Contacts", "contact_suite",                        "text"),
    "P.O. Box":                         ("Contacts", "contact_po_box",                       "text"),
    "Zip code":                         ("Contacts", "contact_zip_code",                     "text"),
    "Contacts - City":                  ("Contacts", "contact_city",                         "text"),
    "Contacts - County":                ("Contacts", "contact_county",                       "text"),
    "Contacts - State / province":      ("Contacts", "contact_state_province",               "text"),
    "Contacts - Country":               ("Contacts", "contact_country",                      "text"),
    "Additional address details":       ("Contacts", "contact_additional_address",           "text"),
    "Phone":                            ("Contacts", "contact_phone",                        "text"),
    "Mobile":                           ("Contacts", "contact_mobile",                       "text"),
    "Fax":                              ("Contacts", "contact_fax",                          "text"),
    "Email":                            ("Contacts", "contact_email",                        "text"),
}

# Vision prompt for scanned PDF bounding-box location
_LOCATE_PROMPT = """You are looking at a scanned lease document page.

Find the exact location of this text in the image:
"{source_text}"

Return ONLY valid JSON — no markdown, no explanation:
{{"top": 0.0, "left": 0.0, "bottom": 0.0, "right": 0.0}}

Each value is a decimal 0.0–1.0 representing the fraction of image height/width (top-left origin).
If the text is not found, return:
{{"top": null, "left": null, "bottom": null, "right": null}}"""


def _calc_cost(input_tokens: int, output_tokens: int, model: str) -> float:
    in_price, out_price = _prices_per_m(model)
    return round(
        (input_tokens  / 1_000_000 * in_price) +
        (output_tokens / 1_000_000 * out_price),
        6,
    )


def _confidence_level(score: float) -> str:
    if score >= 0.80:
        return "high"
    if score >= 0.60:
        return "medium"
    return "low"


# ── Bounding-box helpers (typed PDFs: exact geometry, no LLM) ──────────────────

# Zero-width characters that pollute Indic text and break exact matching.
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿"), None)


def _normalize_text(s: str) -> str:
    """Normalize for matching: NFC, drop zero-width joiners, casefold, collapse whitespace."""
    s = unicodedata.normalize("NFC", s or "")
    s = s.translate(_ZERO_WIDTH)
    s = s.casefold()
    s = re.sub(r"\s+", " ", s).strip()
    return s


_PAGE_MARKER_RE = re.compile(r"=+\s*page\s*\d+\s*=+", re.IGNORECASE)


def _clean_source_text(source_text: str) -> str:
    """
    Prepare a source_clause for OCR/text matching. The xts engine sometimes embeds page markers
    ('===== PAGE 31 =====') and stitches non-contiguous quotes with ellipses — both wreck exact
    matching and let the fuzzy matcher drift onto a similar-but-wrong clause. Strip the markers
    and return the LONGEST contiguous fragment (the most reliable anchor on the page).
    """
    s = _PAGE_MARKER_RE.sub(" ", source_text or "")
    fragments = [f.strip() for f in re.split(r"\.{2,}|…|\s\.\s", s)]
    fragments = [f for f in fragments if len(f) >= 12]
    if fragments:
        return max(fragments, key=len)
    return s.strip()


def _normalize_bbox(raw: dict | None) -> dict | None:
    """Clamp a {top,left,bottom,right} dict to [0,1] and validate; return None if invalid."""
    if not isinstance(raw, dict):
        return None
    keys = ("top", "left", "bottom", "right")
    if any(raw.get(k) is None for k in keys):
        return None
    try:
        b = {k: round(max(0.0, min(1.0, float(raw[k]))), 4) for k in keys}
    except (TypeError, ValueError):
        return None
    if b["top"] >= b["bottom"] or b["left"] >= b["right"]:
        return None
    return b


def _rect_to_bbox(rect: fitz.Rect, page_rect: fitz.Rect) -> dict | None:
    """Convert a PyMuPDF rect (points, top-left origin) to normalized 0-1 fractions."""
    pw, ph = page_rect.width, page_rect.height
    if pw <= 0 or ph <= 0:
        return None
    return _normalize_bbox({
        "top":    rect.y0 / ph,
        "left":   rect.x0 / pw,
        "bottom": rect.y1 / ph,
        "right":  rect.x1 / pw,
    })


def _union_rect(rects: list[fitz.Rect]) -> fitz.Rect | None:
    if not rects:
        return None
    r = fitz.Rect(rects[0])
    for other in rects[1:]:
        r |= other
    return r


def locate_clause_in_typed_page(
    pdf_bytes: bytes,
    source_text: str,
    page_number: int,
    extracted_value: str | None = None,
    fuzzy_threshold: float = 0.75,
) -> dict | None:
    """
    Find `source_text` on a typed/digital PDF page using exact word geometry from the
    PDF content stream (no LLM/vision). Returns:
        {"bbox": {top,left,bottom,right}, "bbox_rects": [ {..}, .. ]}   (0-1 fractions)
    or None if not found. `bbox_rects` is one tight box per text line; `bbox` is their union.
    """
    if not _normalize_text(source_text):
        return None

    doc = None
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if page_number < 1 or page_number > doc.page_count:
            return None
        page = doc[page_number - 1]
        # words: (x0, y0, x1, y1, "word", block, line, word_no)
        words = page.get_text("words")
        if not words:
            return None
        return _locate_in_words(words, page.rect, source_text, extracted_value, fuzzy_threshold)
    except Exception as e:
        print(f"Warning: locate_clause_in_typed_page failed (page {page_number}): {e}")
        return None
    finally:
        if doc is not None:
            doc.close()


def _locate_in_words(
    words: list,
    page_rect: fitz.Rect,
    source_text: str,
    extracted_value: str | None,
    fuzzy_threshold: float = 0.75,
) -> dict | None:
    """
    Shared locator core used by BOTH typed (PyMuPDF) and scanned (OCR) paths.
    `words` is a list of tuples (x0, y0, x1, y1, text, block, line, word_no) in the same
    coordinate space as `page_rect`. Returns {"bbox", "bbox_rects", "quality"} or None.
    `quality` is the match confidence (1.0 = exact substring) — callers can gate on it.
    """
    needle = _normalize_text(_clean_source_text(source_text))
    if not needle or not words:
        return None

    # Build a normalized char stream and map each char index -> word index.
    norm_words = [_normalize_text(w[4]) for w in words]
    char_to_word: list[int] = []
    stream_parts: list[str] = []
    for wi, nw in enumerate(norm_words):
        if not nw:
            continue
        if stream_parts:
            stream_parts.append(" ")
            char_to_word.append(-1)  # the joining space belongs to no word
        stream_parts.append(nw)
        char_to_word.extend([wi] * len(nw))
    stream = "".join(stream_parts)
    if not stream:
        return None

    # 1) Exact substring match against the page's own normalized text.
    word_idxs = _match_exact(stream, char_to_word, needle)
    quality = 1.0 if word_idxs else 0.0

    # 2) Fuzzy fallback over sliding word windows.
    if word_idxs is None:
        word_idxs, quality = _match_fuzzy(norm_words, needle, fuzzy_threshold)

    # 3) First-words prefix fallback.
    if word_idxs is None and len(needle) > 12:
        prefix = " ".join(needle.split()[:6])
        word_idxs = _match_exact(stream, char_to_word, prefix)
        if word_idxs:
            quality = len(prefix) / len(needle)

    # 4) Truncated-clause fallback: the engine sometimes stores a clause that starts (or ends)
    #    mid-word, e.g. "er, executive/shared office suites…" (tail of "provider,"). Retry with the
    #    leading/trailing token dropped — but ONLY while the remaining phrase stays long enough to be
    #    unique (≥6 words / ≥25 chars), so a short ambiguous fragment can never match the wrong spot.
    if word_idxs is None:
        tokens = needle.split()
        for trimmed in (tokens[1:], tokens[:-1], tokens[1:-1]):
            if len(trimmed) < 6:
                continue
            cand = " ".join(trimmed)
            if len(cand) < 25:
                continue
            idxs = _match_exact(stream, char_to_word, cand)
            q = len(cand) / len(needle) if idxs else 0.0
            if idxs is None:
                idxs, q = _match_fuzzy(norm_words, cand, fuzzy_threshold)
            if idxs:
                word_idxs, quality = idxs, q
                break

    if not word_idxs:
        return None

    # Optional tightening: if the extracted value appears verbatim inside the matched
    # span, shrink to just those words (good for numbers / dates / Latin codes).
    tight = _tighten_to_value(norm_words, word_idxs, extracted_value)
    if tight:
        word_idxs = tight

    # Group matched words by (block, line); one union rect per line.
    by_line: dict[tuple[int, int], list[fitz.Rect]] = {}
    for wi in sorted(set(word_idxs)):
        w = words[wi]
        key = (w[5], w[6])
        by_line.setdefault(key, []).append(fitz.Rect(w[0], w[1], w[2], w[3]))

    line_rects = [_union_rect(rs) for rs in by_line.values()]
    bbox_rects = [b for r in line_rects if r and (b := _rect_to_bbox(r, page_rect))]
    if not bbox_rects:
        return None

    union = _union_rect([r for r in line_rects if r])
    bbox = _rect_to_bbox(union, page_rect) if union else None
    return {"bbox": bbox, "bbox_rects": bbox_rects, "quality": round(quality, 3)}


def _match_exact(stream: str, char_to_word: list[int], needle: str) -> list[int] | None:
    idx = stream.find(needle)
    if idx == -1:
        return None
    span = char_to_word[idx: idx + len(needle)]
    word_idxs = [w for w in span if w >= 0]
    return word_idxs or None


def _match_fuzzy(norm_words: list[str], needle: str, threshold: float) -> tuple[list[int] | None, float]:
    """Return (best_word_window, best_ratio). window is None if best_ratio < threshold."""
    needle_words = needle.split()
    n = len(needle_words)
    if n == 0:
        return None, 0.0
    # Real word indices (skip blanks), preserving order.
    real = [i for i, w in enumerate(norm_words) if w]
    if not real:
        return None, 0.0

    best_ratio = 0.0
    best_window: list[int] | None = None
    # Try windows of size n-1, n, n+1 to absorb tokenization differences.
    for size in {max(1, n - 1), n, n + 1}:
        for start in range(0, len(real) - size + 1):
            window = real[start: start + size]
            candidate = " ".join(norm_words[i] for i in window)
            ratio = SequenceMatcher(None, needle, candidate).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_window = window

    if best_window is not None and best_ratio >= threshold:
        return best_window, best_ratio
    return None, best_ratio


def _tighten_to_value(
    norm_words: list[str],
    word_idxs: list[int],
    extracted_value: str | None,
) -> list[int] | None:
    """
    Shrink a matched span to just the word(s) that carry the extracted value, so highlights
    hug the value rather than the whole context line.

    Best-effort and tolerant (important for SCANNED PDFs, where OCR garbles text and the LLM
    normalizes values): tries, in order, (1) exact substring, (2) digit-only match for numeric
    values — amounts/dates/codes — so "1,66,605" matches OCR "166605", and (3) a fuzzy ratio
    match to absorb minor OCR errors. Returns the minimal contiguous sub-run, or None to keep
    the full span when nothing confidently matches.
    """
    val = _normalize_text(extracted_value or "")
    if not val or len(val) < 2:
        return None
    span = sorted(set(word_idxs))
    if not span:
        return None

    val_digits = re.sub(r"\D", "", val)
    # Treat as numeric when digits dominate (amounts, dates, parking counts, postal codes…).
    is_numeric = len(val_digits) >= 2 and len(val_digits) >= 0.5 * len(val.replace(" ", ""))

    def _is_match(sub_text: str) -> bool:
        if val in sub_text:                                   # 1) exact
            return True
        if is_numeric:                                        # 2) digit-only (format-agnostic)
            sub_digits = re.sub(r"\D", "", sub_text)
            if sub_digits and val_digits and val_digits in sub_digits:
                return True
        if len(val) >= 4 and SequenceMatcher(None, val, sub_text).ratio() >= 0.82:  # 3) fuzzy
            return True
        return False

    # Smallest contiguous sub-run of matched words that satisfies the matcher.
    best: list[int] | None = None
    for i in range(len(span)):
        for j in range(i, len(span)):
            sub = span[i: j + 1]
            if _is_match(" ".join(norm_words[k] for k in sub)):
                if best is None or len(sub) < len(best):
                    best = sub
                break  # shortest run starting at i — no need to extend further
    return best


# ── Scanned PDFs: exact boxes via local Tesseract OCR (free, no API) ────────────

_available_langs_cache: set[str] | None = None


def _available_langs() -> set[str]:
    """Tesseract language packs installed on this machine (queried once)."""
    global _available_langs_cache
    if _available_langs_cache is None:
        try:
            out = subprocess.run(["tesseract", "--list-langs"],
                                 capture_output=True, check=True).stdout.decode("utf-8", "replace")
            _available_langs_cache = {
                ln.strip() for ln in out.splitlines()
                if ln.strip() and not ln.startswith("List")  # keep script/* entries too
            }
        except Exception:
            _available_langs_cache = set()
    return _available_langs_cache


def _langs_for_text(source_text: str) -> str:
    """Pick Tesseract language(s) from the clause's script — generic across ANY language/script
    (Indian, Arabic, CJK, Cyrillic, Latin-European, …). One model per script reads that whole
    script. Always include English (docs mix English headings / Latin numerals). Keeps only
    installed packs, caps the count, and falls back to English if nothing matches."""
    avail = _available_langs()
    picked: list[str] = []
    for ch in source_text or "":
        cp = ord(ch)
        for lo, hi, candidates in _SCRIPT_RANGES:
            if lo <= cp <= hi:
                for lang in candidates:
                    if lang in avail and lang not in picked:
                        picked.append(lang)
                        break  # one installed model per script is enough
                break
    langs = picked[:_MAX_OCR_LANGS]
    if "eng" in avail and "eng" not in langs:
        langs.append("eng")
    return "+".join(langs) if langs else "eng"


def _ocr_page_words(pdf_bytes: bytes, page_number: int, langs: str) -> dict | None:
    """Render a page and OCR it with Tesseract. Returns {words, img_w, img_h} where each
    word is (x0, y0, x1, y1, text, block, line, word_no) in image-pixel coordinates."""
    doc = None
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if page_number < 1 or page_number > doc.page_count:
            return None
        page = doc[page_number - 1]
        pix = page.get_pixmap(matrix=fitz.Matrix(OCR_DPI / 72, OCR_DPI / 72))
        img_w, img_h = pix.width, pix.height
        # Tesseract resolves a relative filename more reliably than an absolute path, so we
        # run it from the temp directory with just the basename.
        with tempfile.TemporaryDirectory() as tmp:
            png_name = "page.png"
            pix.save(os.path.join(tmp, png_name))
            proc = subprocess.run(
                ["tesseract", png_name, "stdout", "-l", langs, "--psm", "3", "tsv"],
                capture_output=True, check=True, cwd=tmp,
            )
        out = proc.stdout.decode("utf-8", "replace")
    except Exception as e:
        print(f"Warning: OCR failed (page {page_number}, langs={langs}): {e}")
        return None
    finally:
        if doc is not None:
            doc.close()

    words = []
    reader = csv.reader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE)
    next(reader, None)  # header
    for row in reader:
        if len(row) < 12:
            continue
        try:
            conf = float(row[10])
        except ValueError:
            continue
        text = row[11].strip()
        if conf < 0 or not text:
            continue
        left, top, w, h = int(row[6]), int(row[7]), int(row[8]), int(row[9])
        block, line, wno = int(row[2]), int(row[4]), int(row[5])
        words.append((left, top, left + w, top + h, text, block, line, wno))
    return {"words": words, "img_w": img_w, "img_h": img_h}


def locate_clause_in_scanned_page_ocr(
    pdf_bytes: bytes,
    source_text: str,
    page_number: int,
    extracted_value: str | None = None,
    page_cache: dict | None = None,
) -> dict | None:
    """
    Locate a clause on a scanned page using OCR word geometry + the shared matcher.
    Returns {"bbox", "bbox_rects", "quality"} or None. Returns None (rather than a guess)
    when the match confidence is below OCR_MIN_QUALITY — caller may then fall back to vision.

    `page_cache` (optional, e.g. db["ocr_pages"][lease_id]) caches OCR per page so repeat
    clicks on the same page are instant.
    """
    if not _normalize_text(source_text):
        return None

    # Skip OCR for scripts where Tesseract is unreliable (e.g. Urdu/Arabic Nastaliq).
    # Returning None triggers the Claude-vision fallback in the caller.
    if _should_skip_ocr(source_text):
        return None

    langs = _langs_for_text(source_text)
    cache_key = f"{page_number}:{langs}"
    data = page_cache.get(cache_key) if page_cache is not None else None
    if data is None:
        data = _ocr_page_words(pdf_bytes, page_number, langs)
        if data is not None and page_cache is not None:
            page_cache[cache_key] = data
    if not data or not data["words"]:
        return None

    img_rect = fitz.Rect(0, 0, data["img_w"], data["img_h"])
    # OCR text is noisier than a real text layer, but too-loose fuzzy matching lands the box on a
    # *similar but wrong* clause. Keep the fuzzy bar reasonably high and let OCR_MIN_QUALITY gate
    # the rest — better to show NO box than a wrong one.
    fuzzy = float(os.getenv("OCR_FUZZY_THRESHOLD", "0.72"))
    located = _locate_in_words(data["words"], img_rect, source_text, extracted_value, fuzzy_threshold=fuzzy)
    if not located or located["quality"] < OCR_MIN_QUALITY:
        return None
    return located


def detect_pdf_type(pdf_bytes: bytes) -> str:
    """Return 'typed' if the PDF has a text layer, 'scanned' if it is image-only."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total_chars = sum(len(page.get_text().strip()) for page in doc)
    doc.close()
    return "scanned" if total_chars < 100 else "typed"


def render_page_as_png_b64(pdf_bytes: bytes, page_number: int, dpi: int = 150) -> str:
    """Render one PDF page to a base64-encoded PNG for vision calls."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[page_number - 1]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    png_bytes = pix.tobytes("png")
    doc.close()
    return base64.standard_b64encode(png_bytes).decode("utf-8")


def render_page_as_strips(
    pdf_bytes: bytes,
    page_number: int,
    dpi: int = SCANNED_DPI,
    n_strips: int = SCANNED_PAGE_STRIPS,
    overlap_px: int = SCANNED_STRIP_OVERLAP,
) -> list[str]:
    """Render one scanned page as N horizontal strips (base64 PNGs).
    Each strip is a direct crop of the pixel buffer — no data is lost.
    A small pixel overlap between adjacent strips prevents text on a boundary
    being split across two images. All strips together cover 100% of the page.
    Returns a list of N base64-encoded PNG strings."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[page_number - 1]
    pr = page.rect          # page dimensions in PDF points
    strip_height = pr.height / n_strips
    strips: list[str] = []

    for i in range(n_strips):
        # Compute the clip rect in PDF points with overlap.
        top    = max(0.0,       i * strip_height - (overlap_px * 72 / dpi))
        bottom = min(pr.height, (i + 1) * strip_height + (overlap_px * 72 / dpi))
        clip = fitz.Rect(0, top, pr.width, bottom)
        mat  = fitz.Matrix(dpi / 72, dpi / 72)
        pix  = page.get_pixmap(matrix=mat, clip=clip)
        strips.append(base64.standard_b64encode(pix.tobytes("png")).decode("utf-8"))

    doc.close()
    return strips


async def locate_text_in_scanned_page(
    client: AsyncAnthropic,
    pdf_bytes: bytes,
    source_text: str,
    page_number: int,
) -> tuple[dict | None, dict]:
    """
    Ask Claude Haiku vision to return a bounding box for source_text on a scanned page.
    Returns (bbox | None, token_usage_dict).
    bbox keys: top, left, bottom, right  (all 0.0–1.0 fractions of image dimensions)
    """
    png_b64 = render_page_as_png_b64(pdf_bytes, page_number)
    prompt = _LOCATE_PROMPT.format(source_text=source_text)

    response = await client.messages.create(
        model=MODEL_SCANNED,
        max_tokens=150,
        temperature=0,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": png_b64},
                },
                {"type": "text", "text": prompt},
            ],
        }],
    )

    tokens = {
        "model": MODEL_SCANNED,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cost_usd": _calc_cost(response.usage.input_tokens, response.usage.output_tokens, MODEL_SCANNED),
        "called_at": datetime.now(timezone.utc).isoformat(),
    }

    raw = response.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    try:
        bbox = json.loads(raw)
        if bbox.get("top") is None:
            return None, tokens
        # Clamp values to [0, 1]
        for k in ("top", "left", "bottom", "right"):
            bbox[k] = max(0.0, min(1.0, float(bbox[k])))
        return bbox, tokens
    except Exception:
        return None, tokens


from app.services.claude_allattributes import PROMPT as _PROMPT


async def _translate_non_english_clauses(attrs: list[dict], client: AsyncAnthropic) -> list[dict]:
    """Translate non-English source_clauses to English using Claude."""
    # Collect non-English clauses that need translation
    to_translate = {
        i: attr["source_clause"]
        for i, attr in enumerate(attrs)
        if attr.get("source_clause") and attr["translation"] is None
    }

    if not to_translate:
        return attrs

    # Batch translate all clauses in one call
    clauses_text = "\n".join(
        f"{idx}: {clause}" for idx, clause in to_translate.items()
    )

    translate_prompt = f"""You are a translator. Translate the following lease text snippets to English.
Only translate non-English text. If already in English, return as-is.
Return ONLY a JSON object with numeric keys and English translations as values.
Example: {{"0": "translated text", "1": "already english", "2": "translated text"}}

Text to translate:
{clauses_text}"""

    try:
        response = await client.messages.create(
            model=MODEL_TRANSLATE,
            max_tokens=2000,
            temperature=0,
            messages=[{"role": "user", "content": translate_prompt}],
        )
        result_text = response.content[0].text.strip()
        result_text = result_text.replace("```json", "").replace("```", "").strip()
        translations = json.loads(result_text)

        # Apply translations back to attributes
        for idx_str, translated in translations.items():
            try:
                idx = int(idx_str)
                if idx in to_translate:
                    attrs[idx]["translation"] = str(translated)
            except (ValueError, KeyError):
                pass
    except Exception as e:
        print(f"Warning: translation failed: {e}")

    return attrs


async def extract_from_pdf(pdf_bytes: bytes, lease_id: str) -> tuple[list[dict], dict, str]:
    """
    Extract all lease attributes from a PDF.
    Returns (attrs, token_usage, pdf_type).
    token_usage keys: model, input_tokens, output_tokens, cost_usd
    pdf_type: 'typed' | 'scanned'
    """
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY is not set in environment")

    pdf_type = detect_pdf_type(pdf_bytes)

    # Typed PDFs are read from the text layer (cheap text model is enough); scanned PDFs need
    # vision, so use the stronger vision model. Both are configured via env.
    model = MODEL_SCANNED if pdf_type == "scanned" else MODEL_TYPED

    client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    if pdf_type == "scanned":
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page_count = doc.page_count
        doc.close()

        batches = _plan_batches(page_count)
        print(f"Scanned PDF: {page_count} pages → {len(batches)} batch(es) running in parallel: "
              f"{[(s,e,n) for s,e,n in batches]}")

        async def _run_batch(batch_num: int, start_page: int, end_page: int, strips: int) -> dict:
            """Render and extract one page-range batch. Returns raw Claude response dict."""
            print(f"  Batch {batch_num}/{len(batches)}: pages {start_page}-{end_page}, {strips} strip(s)")
            content: list[dict] = []
            for page_num in range(start_page, end_page + 1):
                page_strips = render_page_as_strips(pdf_bytes, page_num, n_strips=strips)
                content.append({"type": "text",
                                 "text": f"--- Page {page_num} of {page_count} ---"})
                for strip_b64 in page_strips:
                    content.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png",
                                   "data": strip_b64},
                    })
            content.append({"type": "text", "text": _PROMPT})

            async with client.messages.stream(
                model=model, max_tokens=EXTRACTION_MAX_OUTPUT_TOKENS, temperature=0,
                messages=[{"role": "user", "content": content}],
            ) as stream:
                resp = await stream.get_final_message()

            raw = resp.content[0].text.strip().replace("```json", "").replace("```", "").strip()
            try:
                batch_data = json.loads(raw)
            except json.JSONDecodeError as e:
                if resp.stop_reason == "max_tokens":
                    raise ValueError(
                        f"Batch {batch_num} response truncated. Raise EXTRACTION_MAX_OUTPUT_TOKENS."
                    ) from e
                raise
            return {
                "data": batch_data,
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
            }

        # Fire all batches simultaneously — total time = slowest single batch, not sum.
        import asyncio as _asyncio
        batch_results = await _asyncio.gather(*[
            _run_batch(i + 1, s, e, n) for i, (s, e, n) in enumerate(batches)
        ])

        # Merge all batch results: highest confidence value wins per attribute.
        merged_data: dict = {}
        total_input_tokens = 0
        total_output_tokens = 0
        for result in batch_results:
            total_input_tokens  += result["input_tokens"]
            total_output_tokens += result["output_tokens"]
            for attr_name, entry in result["data"].items():
                if not isinstance(entry, dict) or entry.get("value") is None:
                    merged_data.setdefault(attr_name, entry)
                    continue
                existing = merged_data.get(attr_name, {})
                existing_score = (existing or {}).get("confidence_score") or 0.0
                new_score = entry.get("confidence_score") or 0.0
                if new_score >= existing_score:
                    merged_data[attr_name] = entry

        data = merged_data
        extraction_usage = {
            "model": model,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "cost_usd": _calc_cost(total_input_tokens, total_output_tokens, model),
        }
    else:
        # Typed/digital PDF — send as a document (Claude reads the real text layer).
        # Text is extremely token-efficient; even 150-page docs fit in one call.
        pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
        content = [
            {
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64},
            },
            {"type": "text", "text": _PROMPT},
        ]
        # Use streaming to avoid the 10-minute SDK timeout on large documents.
        async with client.messages.stream(
            model=model, max_tokens=EXTRACTION_MAX_OUTPUT_TOKENS, temperature=0,
            messages=[{"role": "user", "content": content}],
        ) as stream:
            response = await stream.get_final_message()
        raw = response.content[0].text.strip().replace("```json","").replace("```","").strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            if response.stop_reason == "max_tokens":
                raise ValueError(
                    "Extraction response was truncated. Raise EXTRACTION_MAX_OUTPUT_TOKENS."
                ) from e
            raise
        extraction_usage = {
            "model": model,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cost_usd": _calc_cost(response.usage.input_tokens, response.usage.output_tokens, model),
        }

    attrs = []
    for attr_name, (category, attr_key, dtype) in ATTR_MAP.items():
        entry = data.get(attr_name) or {}
        value = entry.get("value")
        score_raw = entry.get("confidence_score") or 0.0
        source_text = entry.get("source_clause")
        page_num    = entry.get("page_number")
        reason      = entry.get("confidence_reason")
        bbox        = _normalize_bbox(entry.get("bbox"))

        if value is None:
            score_int, level = 0, "low"
        else:
            score_int = round(score_raw * 100)
            level = _confidence_level(score_raw)

        attrs.append({
            "attribute_id": str(uuid.uuid4()),
            "lease_id": lease_id,
            "category": category,
            "attribute_key": attr_key,
            "attribute_name": attr_name,
            "data_type": dtype,
            "extracted_value": str(value) if value is not None else None,
            "user_edited_value": None,
            "confidence_score": score_int,
            "confidence_level": level,
            "confidence_reason": str(reason) if reason is not None else None,
            "is_verified": False,
            "verified_by": None,
            "verified_at": None,
            "page_number": int(page_num) if page_num is not None else None,
            "source_clause": str(source_text) if source_text is not None else None,
            "translation": None,  # Will be filled if source_clause is non-English
            "bbox": bbox,
            "bbox_rects": None,
            "is_key_field": False,
        })

    # Compute exact bounding boxes for typed/digital PDFs using PyMuPDF geometry.
    # (Scanned PDFs are located lazily via vision; bbox stays null here.)
    if pdf_type == "typed":
        for attr in attrs:
            if attr["source_clause"] and attr["page_number"]:
                located = locate_clause_in_typed_page(
                    pdf_bytes,
                    attr["source_clause"],
                    attr["page_number"],
                    attr["extracted_value"],
                )
                if located:
                    attr["bbox"] = located["bbox"]
                    attr["bbox_rects"] = located["bbox_rects"]

    # Translate non-English source_clauses
    attrs = await _translate_non_english_clauses(attrs, client)

    return attrs, extraction_usage, pdf_type

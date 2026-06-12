"""
Adapter: lease_abstraction_xts engine  →  LeaseArc attribute contract.

This is the NEW extraction engine (Gemini 2.5-flash + GPT-4.1-mini + Gemini 2.0-flash-lite,
multi-pass, 87 unique + repeatable groups). It replaces the old Claude-based
`lease_extractor.extract_from_pdf` while keeping the EXACT same return contract so the rest
of the app (persistence, reload-on-restart, review UI, /locate-field highlighting) is untouched.

    extract_from_pdf(pdf_bytes, lease_id) -> (attrs, usage, pdf_type)

Annotation/highlighting and translation are REUSED from the old module (lease_extractor.py),
which is kept intact (only its extract_from_pdf is bypassed by leases.py).

Prerequisites (.env):  GEMINI_API_KEY, OPENAI_API_KEY  (+ the xts deps: google-genai, openai,
pymupdf, pytesseract, pdf2image). ANTHROPIC_API_KEY remains optional — only used for translation.
"""

from __future__ import annotations

import os
import re
import sys
import time
import uuid
import asyncio
import logging
import tempfile
from logging.handlers import RotatingFileHandler

# ── Reuse the old engine's annotation + translation helpers (module kept intact) ──────────────
from app.services.lease_extractor import (
    detect_pdf_type,
    locate_clause_in_typed_page,
    _translate_non_english_clauses,
    ANTHROPIC_API_KEY,
)

# ── Dedicated step log for the extraction flow (debug + success/failure tracking) ─────────────
# Writes to backend/logs/extraction_xts.log. Also captures the xts engine's own loggers
# (main / ocr_pdf_v2 / pdf_document_loader) so per-pass model routing and OCR notes land here.
_LOG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../logs"))
_LOG_PATH = os.path.join(_LOG_DIR, "extraction_xts.log")

log = logging.getLogger("lease_extraction_xts")


def _setup_logging() -> None:
    if getattr(_setup_logging, "_done", False):
        return
    # Logging must never crash the app. If the log dir/file isn't writable (e.g. a deployed server
    # where logs/ is owned by another user), fall back to console logging instead of raising —
    # otherwise importing this module would 500 every endpoint that depends on it.
    handler: logging.Handler
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        handler = RotatingFileHandler(_LOG_PATH, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    except OSError as e:
        handler = logging.StreamHandler()
        logging.getLogger("lease_extraction_xts").warning(
            "Could not open log file %s (%s) — falling back to console logging.", _LOG_PATH, e,
        )
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"))
    for name in ("lease_extraction_xts", "main", "ocr_pdf_v2", "pdf_document_loader"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.INFO)
        # Avoid duplicate handlers on reload.
        if not any(getattr(h, "_xts_marker", False) for h in lg.handlers):
            handler._xts_marker = True  # type: ignore[attr-defined]
            lg.addHandler(handler)
    _setup_logging._done = True  # type: ignore[attr-defined]


_setup_logging()

# ── lease_abstraction_xts engine — copied into the backend (app/services/lease_abstraction_xts) ─
# Kept as a plain directory (not a package) so its bare imports — `from pdf_document_loader import`,
# `from ocr_pdf_v2 import` — resolve once the directory is on sys.path.
_XTS_DIR = os.path.join(os.path.dirname(__file__), "lease_abstraction_xts")

# New engine's dotted attribute namespaces and sentinels. Names arrive in several forms, e.g.
# "Lease_catalyst.Lease_Abstraction.Landlord Name", "...Lease_Abstraction.Lease_Abstraction.Business Hours"
# (clauses, doubled), and "...Clauses.Business Hours" — strip every leading namespace segment.
_ATTR_PREFIX = "Lease_catalyst.Lease_Abstraction."
_NAMESPACE_SEGMENTS = ("Lease_catalyst.", "Lease_Abstraction.", "Clauses.")
_DQC_PREFIX = "Lease_DQC."   # internal data-quality metadata rows — not shown as lease attributes
_NOT_FOUND = "Not found"


def _strip_namespace(full_name: str) -> str:
    """Remove all leading engine namespace segments, leaving the human attribute name."""
    name = full_name
    changed = True
    while changed:
        changed = False
        for seg in _NAMESPACE_SEGMENTS:
            if name.startswith(seg):
                name = name[len(seg):]
                changed = True
    return name


def _is_dqc_row(row: dict) -> bool:
    """Internal QC metadata (document_type/filename/matched_keywords/mode) — excluded from the UI."""
    return str(row.get("attribute_name", "")).startswith(_DQC_PREFIX)
_REPEATABLE_GROUPS = (
    "Area", "Contact Identification", "Options", "Expenses", "Security Deposit", "Allowance", "Parties",
)
_SLOT_RE = re.compile(
    r"^(?:Area|Contact Identification|Options|Expenses|Security Deposit|Allowance)\.(\d+)\.(.+)$"
)
# "Parties" slots are the reverse of the other repeatable groups: the index comes LAST,
# e.g. "Parties.Landlord Name.0", "Parties.Landlord Name.1".
_PARTY_SLOT_RE = re.compile(r"^Parties\.(.+)\.(\d+)$")

# New group name → UI category label (new sections in the review page).
_GROUP_CATEGORY = {
    "Area": "Areas",
    "Contact Identification": "Contacts",
    "Options": "Options",
    "Expenses": "Expenses",
    "Security Deposit": "Security Deposit",
    "Allowance": "Allowance",
    "Parties": "Parties",
}

# A handful of summary fields the rest of the app reads off attribute_key (leases.py enrichment,
# dashboard, leases list). Map the new field suffix → the legacy key so those keep populating.
_LEGACY_KEY_ALIASES = {
    "landlord name": "landlord_name",
    "tenant name": "tenant_name",
    "current commencement": "commencement_date",
    "current expiration": "expiry_date",
    "city": "city",
    "state/province": "state_province",
    "state / province": "state_province",
}


def _slugify(text: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", text.strip().lower())).strip("_")


def _category_for(field_suffix: str) -> str:
    """Lightweight category guess for unique fields (repeatable groups handled separately)."""
    s = field_suffix.lower()
    if any(k in s for k in ("date", "commencement", "expiration", "term", "possession", "delivery")):
        return "Critical Dates"
    if any(k in s for k in ("rent", "amount", "deposit", "allowance", "payment", "charge", "currency")):
        return "Financial Obligations"
    if any(k in s for k in ("expense", "cam", "tax", "operating", "utilit", "insurance")):
        return "CAM and Operating Expenses"
    if any(k in s for k in (
        "default", "estoppel", "holdover", "surrender", "assignment", "sublet", "use",
        "parking", "signage", "maintenance", "restrict", "exclusive", "co-tenancy",
        "radius", "prohibited", "go dark", "percentage", "alteration",
    )):
        return "Restrictive Clauses"
    return "Core Lease Terms"


def _dedupe_attrs(attrs: list[dict]) -> list[dict]:
    """
    Collapse duplicate attributes by attribute_key, preserving order. The engine's config lists
    each clause twice (…Lease_Abstraction.Lease_Abstraction.X and …Clauses.X); after namespace
    stripping these become identical, so we keep the better copy (populated > empty, then higher
    confidence).
    """
    best: dict[str, dict] = {}
    order: list[str] = []
    for a in attrs:
        key = a["attribute_key"]
        prev = best.get(key)
        if prev is None:
            best[key] = a
            order.append(key)
            continue
        a_pop = a["extracted_value"] is not None
        p_pop = prev["extracted_value"] is not None
        if (a_pop, a["confidence_score"]) > (p_pop, prev["confidence_score"]):
            best[key] = a
    return [best[k] for k in order]


def _confidence_level(score_int: int) -> str:
    if score_int >= 80:
        return "high"
    if score_int >= 50:
        return "medium"
    return "low"


def _adapt_row(row: dict, lease_id: str) -> dict:
    """Map one xts row {attribute_name,value,confidence_score,context,page_number} → attribute dict."""
    full_name = str(row.get("attribute_name", "")).strip()

    # QC metadata (document_type, filename, …) — kept defensively; normally filtered before adapting.
    if full_name.startswith(_DQC_PREFIX):
        suffix = full_name[len(_DQC_PREFIX):]
        return _base_attr(
            lease_id, row,
            display_name=suffix.replace("_", " ").title(),
            attr_key=_slugify(f"dqc_{suffix}"),
            category="Document Info",
        )

    # Strip all engine namespace prefixes; keep the attribute itself.
    name = _strip_namespace(full_name)

    # Repeatable slot?  e.g. "Area.0.Gross area"
    m = _SLOT_RE.match(name)
    if m:
        group = next(g for g in _REPEATABLE_GROUPS if name.startswith(g + "."))
        slot = int(m.group(1))
        suffix = m.group(2)
        display_name = f"{group} {slot + 1} — {suffix}"
        attr_key = _slugify(f"{group}_{slot}_{suffix}")
        category = _GROUP_CATEGORY.get(group, "Core Lease Terms")
        # Structured group fields let the UI pivot repeatable rows into per-slot accordion cards.
        return _base_attr(lease_id, row, display_name=display_name, attr_key=attr_key,
                          category=category, group=group, slot_index=slot, sub_field=suffix)

    # Parties slot? e.g. "Parties.Landlord Name.0" — same attribute_key can repeat (one row per
    # landlord/tenant); each becomes its own uniquely-keyed attribute, same as other repeatable
    # groups, so it gets its own accordion card in the review page.
    m = _PARTY_SLOT_RE.match(name)
    if m:
        field_name = m.group(1)
        slot = int(m.group(2))
        display_name = f"{field_name} {slot + 1}"
        attr_key = _slugify(f"{field_name}_{slot}")
        return _base_attr(lease_id, row, display_name=display_name, attr_key=attr_key,
                          category="Parties", group="Parties", slot_index=slot, sub_field=field_name)

    suffix = name
    display_name = suffix
    attr_key = _LEGACY_KEY_ALIASES.get(suffix.lower(), _slugify(suffix))
    category = _category_for(suffix)
    return _base_attr(lease_id, row, display_name=display_name, attr_key=attr_key, category=category)


def _base_attr(lease_id: str, row: dict, *, display_name: str, attr_key: str, category: str,
               group: str | None = None, slot_index: int | None = None,
               sub_field: str | None = None, role: str | None = None) -> dict:
    """Build the UI attribute dict from an xts row, given the resolved name/key/category."""
    raw_value = row.get("value")
    if isinstance(raw_value, dict):
        # New-format "Parties" rows carry {"Value": "...", "Role": "LICENSOR I"} — unwrap to the
        # plain scalar value; Role is kept as metadata (extra.role) for the UI label.
        role = role or raw_value.get("Role")
        raw_value = raw_value.get("Value")
    value = None if (raw_value is None or str(raw_value).strip() in ("", _NOT_FOUND)) else str(raw_value).strip()

    try:
        score_int = int(round(float(row.get("confidence_score") or 0)))
    except (TypeError, ValueError):
        score_int = 0
    if value is None:
        score_int = 0

    context = str(row.get("context") or "").strip() or None
    pg = row.get("page_number")
    try:
        page_number = int(pg) if str(pg).strip() not in ("", "None") else None
    except (TypeError, ValueError):
        page_number = None

    return {
        "attribute_id": str(uuid.uuid4()),
        "lease_id": lease_id,
        "category": category,
        "attribute_key": attr_key,
        "attribute_name": display_name,
        "data_type": "text",
        "extracted_value": value,
        "user_edited_value": None,
        "confidence_score": score_int,
        "confidence_level": _confidence_level(score_int),
        "confidence_reason": None,
        "is_verified": False,
        "verified_by": None,
        "verified_at": None,
        "page_number": page_number,
        "source_clause": context,   # verbatim quote → drives /locate-field highlighting
        "translation": None,
        "bbox": None,
        "bbox_rects": None,
        "is_key_field": attr_key in set(_LEGACY_KEY_ALIASES.values()),
        # Folder uploads: which source document this value came from (index → documentId in router).
        "source_doc_index": row.get("_source_doc_index"),
        "source_document_id": None,
        # Repeatable-group metadata (None for flat fields) → drives the accordion UI.
        "group": group,
        "slot_index": slot_index,
        "sub_field": sub_field,
        # "Parties" rows: the licensor/licensee role, e.g. "LICENSOR I" (None elsewhere).
        "role": role,
        # New attribute-format metadata, passed through when the engine provides it.
        "schema_path": row.get("schema_path"),
        "source_type": row.get("source_type"),
        "source_file_name": row.get("source_file_name"),
        "s3_file_path": row.get("s3_file_path"),
    }


def _run_xts(pdf_path: str) -> tuple[list[dict], dict]:
    """Blocking call into the xts engine. Returns (raw_rows, run_stats)."""
    if _XTS_DIR not in sys.path:
        sys.path.insert(0, _XTS_DIR)
    try:
        import main as xts  # lease_abstraction_xts/main.py
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            f"Could not import lease_abstraction_xts engine from {_XTS_DIR}: {e}"
        ) from e

    # The app sets GEMINI_MODEL for the Location-Analysis feature, but the xts engine treats
    # GEMINI_MODEL as a GLOBAL override that disables its hybrid routing (quality/fast/OpenAI).
    # Temporarily hide it so this extraction always uses the intended multi-model pipeline.
    saved_gemini_model = os.environ.pop("GEMINI_MODEL", None)
    try:
        plan = xts.load_extraction_plan()
        rows, run_stats, _doc_text = xts.extract(pdf_path, plan)
    finally:
        if saved_gemini_model is not None:
            os.environ["GEMINI_MODEL"] = saved_gemini_model
    return rows, run_stats


async def extract_from_pdf(pdf_bytes: bytes, lease_id: str) -> tuple[list[dict], dict, str]:
    """
    NEW engine, OLD contract. Returns (attrs, token_usage, pdf_type).
    token_usage keys: model, input_tokens, output_tokens, cost_usd
    pdf_type: 'typed' | 'scanned'
    """
    t0 = time.time()
    log.info("[%s] ===== SINGLE EXTRACTION START (%.2f MB) =====", lease_id, len(pdf_bytes) / 1e6)
    try:
        pdf_type = detect_pdf_type(pdf_bytes)
        log.info("[%s] STEP 1 pdf-type detect ✓ → %s", lease_id, pdf_type)
    except Exception:
        log.exception("[%s] STEP 1 pdf-type detect ✗", lease_id)
        raise

    # xts.extract() takes a file PATH — write the upload to a temp .pdf.
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    try:
        tmp.write(pdf_bytes)
        tmp.flush()
        tmp.close()
        log.info("[%s] STEP 2 temp pdf written ✓ → %s", lease_id, tmp.name)
        try:
            rows, run_stats = await asyncio.to_thread(_run_xts, tmp.name)
            log.info(
                "[%s] STEP 3 engine extract ✓ → %d rows | %s | ~$%.4f",
                lease_id, len(rows), run_stats.get("model", "?"),
                run_stats.get("estimated_cost_usd", 0.0),
            )
        except Exception:
            log.exception("[%s] STEP 3 engine extract ✗ (xts.extract failed)", lease_id)
            raise
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    try:
        attrs = _dedupe_attrs([_adapt_row(r, lease_id) for r in rows if not _is_dqc_row(r)])
        populated = sum(1 for a in attrs if a["extracted_value"] is not None)
        log.info("[%s] STEP 4 adapt rows ✓ → %d attrs (%d populated)", lease_id, len(attrs), populated)
    except Exception:
        log.exception("[%s] STEP 4 adapt rows ✗", lease_id)
        raise

    # Eager bbox for typed PDFs (reuse old PyMuPDF geometry). Scanned → lazy via /locate-field.
    if pdf_type == "typed":
        located_n = 0
        for attr in attrs:
            if attr["source_clause"] and attr["page_number"]:
                try:
                    located = locate_clause_in_typed_page(
                        pdf_bytes, attr["source_clause"], attr["page_number"], attr["extracted_value"],
                    )
                except Exception:
                    log.exception("[%s] STEP 5 bbox locate error for %s", lease_id, attr["attribute_key"])
                    located = None
                if located:
                    attr["bbox"] = located["bbox"]
                    attr["bbox_rects"] = located["bbox_rects"]
                    located_n += 1
        log.info("[%s] STEP 5 typed bbox ✓ → %d highlighted", lease_id, located_n)
    else:
        log.info("[%s] STEP 5 scanned → bbox deferred to /locate-field (lazy OCR/vision)", lease_id)

    # Translation (best-effort — only if an Anthropic key is configured).
    attrs = await _translate(attrs)

    usage = _usage_from_stats(run_stats)
    log.info("[%s] ===== SINGLE EXTRACTION DONE in %.1fs =====", lease_id, time.time() - t0)
    return attrs, usage, pdf_type


def _usage_from_stats(stats: dict) -> dict:
    return {
        "model": stats.get("model", "lease_abstraction_xts (hybrid)"),
        "input_tokens": stats.get("input_tokens", 0),
        "output_tokens": stats.get("output_tokens", 0),
        "cost_usd": stats.get("estimated_cost_usd", stats.get("cost_usd", 0.0)),
    }


async def _translate(attrs: list[dict]) -> list[dict]:
    """Best-effort non-English clause translation (only if an Anthropic key is configured)."""
    if not ANTHROPIC_API_KEY:
        log.info("STEP 6 translation skipped (no ANTHROPIC_API_KEY)")
        return attrs
    try:
        from anthropic import AsyncAnthropic
        out = await _translate_non_english_clauses(attrs, AsyncAnthropic(api_key=ANTHROPIC_API_KEY))
        log.info("STEP 6 translation ✓")
        return out
    except Exception:  # translation is non-critical
        log.exception("STEP 6 translation ✗ (skipped — non-critical)")
        return attrs


# ── Folder / package upload — many PDFs → one merged lease ─────────────────────────────────────

def _tag_source_doc_index(merged_rows: list[dict], doc_row_lists: list[list[dict]]) -> None:
    """
    Tag each merged row with `_source_doc_index` = the index of the document its value came from
    (the newest doc holding a matching non-empty value). doc_row_lists is Base → newest order.
    """
    per_doc = [{str(r.get("attribute_name", "")): r for r in rows} for rows in doc_row_lists]
    newest = len(doc_row_lists) - 1
    for row in merged_rows:
        name = str(row.get("attribute_name", ""))
        val = str(row.get("value", "")).strip().lower()
        idx = newest
        if val and val != "not found":
            for i in range(newest, -1, -1):
                cand = per_doc[i].get(name)
                if cand and str(cand.get("value", "")).strip().lower() == val:
                    idx = i
                    break
        row["_source_doc_index"] = idx


def _run_xts_package(dir_path: str) -> tuple[list[dict], dict, str, list[dict]]:
    """
    Blocking. Runs the xts lease-package flow over every PDF in dir_path (base + amendments +
    renewals), merges newest-wins, and returns
    (merged_rows, combined_stats, primary_pdf_path, doc_metas).
    doc_metas is [{fileName, docType, path}] in display order (Base → newest); the last is primary.
    Mirrors main.run_lease_package() but returns rows instead of writing Excel.
    """
    if _XTS_DIR not in sys.path:
        sys.path.insert(0, _XTS_DIR)
    try:
        import main as xts
    except Exception as e:  # pragma: no cover
        raise RuntimeError(f"Could not import lease_abstraction_xts engine from {_XTS_DIR}: {e}") from e

    saved_gemini_model = os.environ.pop("GEMINI_MODEL", None)
    try:
        from pathlib import Path
        plan = xts.load_extraction_plan()
        docs = xts.discover_lease_documents(Path(dir_path))  # sorted Base → newest
        log.info(
            "PKG discovered %d doc(s): %s",
            len(docs), ", ".join(f"{d.pdf.name}[{d.doc_type}]" for d in docs),
        )

        doc_row_lists: list[list[dict]] = []
        cached_doc_texts: list[str] = []
        tot_in = tot_out = 0
        cost = 0.0
        model_label = ""
        for i, doc in enumerate(docs, start=1):
            try:
                rows, stats, doc_text = xts.extract(str(doc.pdf.resolve()), plan)
                log.info("PKG doc %d/%d extract ✓ → %s (%d rows)", i, len(docs), doc.pdf.name, len(rows))
            except Exception:
                log.exception("PKG doc %d/%d extract ✗ → %s", i, len(docs), doc.pdf.name)
                raise
            xts._apply_lease_dqc(rows, doc)
            doc_row_lists.append(rows)
            if doc_text:
                cached_doc_texts.append(doc_text)
            tot_in += stats.get("input_tokens", 0)
            tot_out += stats.get("output_tokens", 0)
            cost += stats.get("estimated_cost_usd", 0.0)
            model_label = stats.get("model", model_label)

        if len(doc_row_lists) > 1:
            merged_rows = xts.merge_abstraction_rows(doc_row_lists)
            # Provenance: tag each merged row with the index of the document its value came from
            # (newest doc with a matching non-empty value) BEFORE post-processing reshapes values.
            _tag_source_doc_index(merged_rows, doc_row_lists)
            merged_text = "\n\n".join(cached_doc_texts) if cached_doc_texts else None
            merged_rows = xts.apply_merged_post_processing(merged_rows, docs, document_text=merged_text)
            xts._apply_lease_dqc_merged(merged_rows, docs)
            log.info("PKG merge ✓ → %d merged rows (newest wins)", len(merged_rows))
        else:
            merged_rows = doc_row_lists[0]
            for r in merged_rows:
                r["_source_doc_index"] = 0
            log.info("PKG single doc → no merge needed")

        combined_stats = {
            "model": f"{model_label} (package merge, {len(docs)} docs)",
            "input_tokens": tot_in,
            "output_tokens": tot_out,
            "estimated_cost_usd": cost,
        }
        # Display order Base → newest; the newest (last) is the primary/viewable document.
        doc_metas = [
            {"fileName": d.pdf.name, "docType": d.doc_type, "path": str(d.pdf.resolve())}
            for d in docs
        ]
        primary_pdf_path = doc_metas[-1]["path"]
        return merged_rows, combined_stats, primary_pdf_path, doc_metas
    finally:
        if saved_gemini_model is not None:
            os.environ["GEMINI_MODEL"] = saved_gemini_model


async def extract_package_from_pdfs(
    files: list[tuple[str, bytes]], lease_id: str,
) -> tuple[list[dict], dict, str, list[dict]]:
    """
    Folder upload: many PDFs (base + amendments + renewals) → ONE merged lease.
    `files` is a list of (filename, bytes); filenames matter — the engine classifies/sorts by them.
    Returns (attrs, usage, primary_pdf_type, doc_set) where doc_set is a list, in display order
    (Base → newest), of {fileName, docType, pdfType, bytes, isPrimary}. The last (newest) is the
    primary/viewable document. bbox is left lazy (/locate-field), since each doc has its own pages.
    """
    t0 = time.time()
    log.info("[%s] ===== PACKAGE EXTRACTION START (%d files) =====", lease_id, len(files))
    tmpdir = tempfile.mkdtemp(prefix=f"lease_pkg_{lease_id[:8]}_")
    try:
        for name, data in files:
            safe = os.path.basename(name) or f"{uuid.uuid4().hex}.pdf"
            if not safe.lower().endswith(".pdf"):
                safe += ".pdf"
            with open(os.path.join(tmpdir, safe), "wb") as f:
                f.write(data)

        try:
            rows, stats, primary_pdf_path, doc_metas = await asyncio.to_thread(_run_xts_package, tmpdir)
        except Exception:
            log.exception("[%s] PACKAGE extract ✗ (engine package run failed)", lease_id)
            raise

        doc_set: list[dict] = []
        for meta in doc_metas:
            with open(meta["path"], "rb") as f:
                data = f.read()
            doc_set.append({
                "fileName": meta["fileName"],
                "docType": meta["docType"],
                "pdfType": detect_pdf_type(data),
                "bytes": data,
                "isPrimary": meta["path"] == primary_pdf_path,
            })
        log.info(
            "[%s] PKG stored %d doc(s); primary (newest) → %s",
            lease_id, len(doc_set), os.path.basename(primary_pdf_path),
        )
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    attrs = await _translate(_dedupe_attrs([_adapt_row(r, lease_id) for r in rows if not _is_dqc_row(r)]))
    primary_pdf_type = next((d["pdfType"] for d in doc_set if d["isPrimary"]), "typed")
    log.info(
        "[%s] ===== PACKAGE EXTRACTION DONE in %.1fs → %d attrs, primary=%s =====",
        lease_id, time.time() - t0, len(attrs), primary_pdf_type,
    )
    return attrs, _usage_from_stats(stats), primary_pdf_type, doc_set

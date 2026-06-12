from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File, Form, Query, Response
from pydantic import BaseModel
from app.mock_db import db
from typing import Optional
import uuid
import json as _json
import os as _os
from datetime import datetime
import logging as _logging
from app.seed.seed_leases import _make_india_demo_attributes
from app.db_models import User, UserSession
from app.routers.auth import get_current_user
from app.services.org_isolation import current_org_id, current_user_info, require_admin, scoped, require_same_org, require_lease_access, user_scoped_leases
from app.services.lease_persistence_pg import (
    finalize_lease_persistence, mark_lease_failed, persist_uploaded_files,
    sync_lease_attributes, sync_token_usage,
)

# Shares the extraction step-log file (backend/logs/extraction_xts.log) configured by the adapter.
_xlog = _logging.getLogger("lease_extraction_xts")

_EXTRACTIONS_DIR = _os.path.join(_os.path.dirname(__file__), "../../extractions")


def _extraction_path(lease_id: str) -> str:
    return _os.path.join(_EXTRACTIONS_DIR, f"{lease_id}.json")


def _usage_path(lease_id: str) -> str:
    return _os.path.join(_EXTRACTIONS_DIR, f"{lease_id}_usage.json")


def _ocr_cache_path(lease_id: str) -> str:
    return _os.path.join(_EXTRACTIONS_DIR, f"{lease_id}_ocr.json")


def _persist_ocr_pages(lease_id: str) -> None:
    """Persist the per-page OCR words so a scanned page is never re-OCR'd after a restart."""
    pages = db["ocr_pages"].get(lease_id)
    if not pages:
        return
    try:
        _os.makedirs(_EXTRACTIONS_DIR, exist_ok=True)
        with open(_ocr_cache_path(lease_id), "w", encoding="utf-8") as f:
            _json.dump(pages, f, ensure_ascii=False)
    except Exception as e:
        print(f"Warning: could not persist OCR cache for {lease_id}: {e}")


def _persist_extraction(lease_id: str) -> None:
    """Sync current in-memory attributes + lease record to durable storage.

    Postgres-backed leases (new uploads, see pg_lease_ids) sync to Postgres; legacy
    JSON-backed leases rewrite {lease_id}.json as before (no-op if no such file).
    """
    if lease_id in db["pg_lease_ids"]:
        try:
            sync_lease_attributes(lease_id)
        except Exception as e:
            print(f"Warning: could not sync attributes to Postgres for {lease_id}: {e}")
        return
    path = _extraction_path(lease_id)
    if not _os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            data = _json.load(f)
        data["attributes"]    = db["attributes"].get(lease_id, [])
        data["lease_record"]  = db["leases"].get(lease_id, {})
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(data, f, indent=4, ensure_ascii=False, default=str)
    except Exception as e:
        print(f"Warning: could not persist extraction for {lease_id}: {e}")


def _persist_usage(lease_id: str) -> None:
    """Sync current token_usage record to durable storage (Postgres or {lease_id}_usage.json)."""
    if lease_id in db["pg_lease_ids"]:
        try:
            sync_token_usage(lease_id)
        except Exception as e:
            print(f"Warning: could not sync token usage to Postgres for {lease_id}: {e}")
        return
    usage = db["token_usage"].get(lease_id)
    if not usage:
        return
    try:
        _os.makedirs(_EXTRACTIONS_DIR, exist_ok=True)
        with open(_usage_path(lease_id), "w", encoding="utf-8") as f:
            _json.dump(usage, f, indent=4, ensure_ascii=False, default=str)
    except Exception as e:
        print(f"Warning: could not persist usage for {lease_id}: {e}")


def _persist_hash_cache() -> None:
    """Write the full content_hash_cache to extractions/hash_cache.json."""
    try:
        _os.makedirs(_EXTRACTIONS_DIR, exist_ok=True)
        path = _os.path.join(_EXTRACTIONS_DIR, "hash_cache.json")
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(db["content_hash_cache"], f)
    except Exception as e:
        print(f"Warning: could not persist hash cache: {e}")

router = APIRouter()

# ── Track how many times each job has been polled ──────────────────────────
_job_poll_count: dict = {}


async def _run_extraction(pdf_bytes: bytes, lease_id: str, job_id: str, file_hash: str = "",
                           user_id: str | None = None, org_id: str | None = None) -> None:
    try:
        # ── OLD engine (Claude / Anthropic) — kept for reference, swapped out ──────────────
        # from app.services.lease_extractor import extract_from_pdf
        # ── NEW engine (lease_abstraction_xts: Gemini + GPT, multi-pass) ───────────────────
        # Same (attrs, usage, pdf_type) contract — annotation + translation reused from the
        # old module, so persistence, reload-on-restart, review UI and /locate-field are intact.
        from app.services.lease_extractor_xts import extract_from_pdf
        attrs, usage, pdf_type = await extract_from_pdf(pdf_bytes, lease_id)
        _finalize_extraction(lease_id, job_id, attrs, usage, pdf_type, pdf_bytes, file_hash=file_hash,
                              user_id=user_id, org_id=org_id)
    except Exception as exc:
        _xlog.exception("[%s] JOB %s FAILED (single upload)", lease_id, job_id)
        db["upload_jobs"][job_id]["status"] = "failed"
        db["upload_jobs"][job_id]["errorMessage"] = str(exc)
        try:
            mark_lease_failed(lease_id, str(exc))
        except Exception:
            _xlog.exception("[%s] mark_lease_failed also failed", lease_id)


async def _run_package_extraction(files: list[tuple[str, bytes]], lease_id: str, job_id: str,
                                   phase_a_doc_ids: dict[str, str], file_hash: str = "",
                                   user_id: str | None = None, org_id: str | None = None) -> None:
    """Folder upload — many PDFs merged into one lease via the xts package flow."""
    try:
        from app.services.lease_extractor_xts import extract_package_from_pdfs
        attrs, usage, pdf_type, doc_set = await extract_package_from_pdfs(files, lease_id)

        # Store every document so the viewer can switch between them. The primary (newest) doc
        # is keyed by the lease_id itself, so the existing /document/{lease_id} viewer default
        # keeps working; the rest reuse the documentId assigned to their lease_files row in
        # Phase A (persist_uploaded_files), so Phase B can update those rows in place.
        primary_bytes = None
        manifest: list[dict] = []
        for d in doc_set:
            document_id = lease_id if d["isPrimary"] else phase_a_doc_ids.get(d["fileName"], str(uuid.uuid4()))
            db["lease_documents"][document_id] = d["bytes"]
            if d["isPrimary"]:
                primary_bytes = d["bytes"]
            manifest.append({
                "documentId": document_id,
                "fileName": d["fileName"],
                "docType": d["docType"],
                "pdfType": d["pdfType"],
                "isPrimary": d["isPrimary"],
            })
        db["lease_doc_sets"][lease_id] = manifest

        # Resolve each attribute's source-document index → its documentId, so the review page can
        # auto-switch the viewer to the doc a value came from when the attribute is clicked.
        idx_to_docid = [m["documentId"] for m in manifest]
        for a in attrs:
            si = a.pop("source_doc_index", None)
            if si is not None and 0 <= si < len(idx_to_docid):
                a["source_document_id"] = idx_to_docid[si]

        _finalize_extraction(lease_id, job_id, attrs, usage, pdf_type, primary_bytes, file_hash=file_hash,
                              user_id=user_id, org_id=org_id)
    except Exception as exc:
        _xlog.exception("[%s] JOB %s FAILED (folder upload)", lease_id, job_id)
        db["upload_jobs"][job_id]["status"] = "failed"
        db["upload_jobs"][job_id]["errorMessage"] = str(exc)
        try:
            mark_lease_failed(lease_id, str(exc))
        except Exception:
            _xlog.exception("[%s] mark_lease_failed also failed", lease_id)


def _normalize_date_to_iso(s: str):
    """Convert an extracted date string to a `date` (for ISO storage), or None if unparseable.

    The engine emits mixed formats — ISO, spelled-out months, and ambiguous numeric slash dates
    ("09/13/2019" is clearly MM/DD; "12/09/2029" is ambiguous). For ambiguous slash dates we assume
    DD/MM/YYYY (common for Indian leases). Storing ISO lets the UI's formatDate render them.
    """
    import re
    from datetime import date as _date
    s = (s or "").strip()
    if not s:
        return None
    # Non-slash formats only here — slash dates are disambiguated by the regex below.
    for fmt in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a > 12:        # first field can only be a day → DD/MM/YYYY
            day, mon = a, b
        elif b > 12:      # second field can only be a day → MM/DD/YYYY
            mon, day = a, b
        else:             # ambiguous → assume day-first (DD/MM/YYYY)
            day, mon = a, b
        try:
            return _date(y, mon, day)
        except ValueError:
            return None
    return None


def _find_lease_for_doc(doc_key: str) -> dict | None:
    """Return the lease record for a document key (primary lease_id or folder-upload secondary documentId)."""
    lease = db["leases"].get(doc_key)
    if lease:
        return lease
    for lid, doc_set in db.get("lease_doc_sets", {}).items():
        if any(d.get("documentId") == doc_key for d in doc_set):
            return db["leases"].get(lid)
    return None


def _job_visible_to(job: dict, org_id: str, user_id: str, is_admin: bool) -> bool:
    """Return True if this upload job is visible to the requesting user (org + user isolation)."""
    lease = db["leases"].get(job.get("leaseId") or "")
    if not lease:
        return False
    if lease.get("org_id") != org_id:
        return False
    if not is_admin:
        creator = lease.get("created_by")
        if creator is not None and creator != user_id:
            return False
    return True


def _enrich_lease_record(lease_id: str, attrs: list) -> None:
    """Map extracted attribute values onto the lease summary record (commencement, expiry, rent,
    address, parties, …). Accepts both legacy and xts-engine key names. Idempotent — safe to re-run
    for a backfill on an already-extracted lease.
    """
    import re
    key_fields = {a["attribute_key"]: a["extracted_value"] for a in attrs if a.get("extracted_value")}
    lease = db["leases"].get(lease_id, {})

    def parse_amount(val: str):
        cleaned = re.sub(r"[₹$€£,\s]", "", val)
        try:
            return float(cleaned)
        except ValueError:
            return None

    # Some keys differ between the legacy extractor and the xts engine — accept either.
    def first_field(*keys: str):
        for k in keys:
            if key_fields.get(k):
                return key_fields[k]
        return None

    # "Parties" rows use per-slot keys (landlord_name_0, landlord_name_1, …, one per
    # landlord/tenant); slot 0 is the primary/first one shown in the lease summary. Existing
    # leases without slots fall back to the legacy bare key.
    _landlord = first_field("landlord_name_0", "landlord_name")
    if _landlord:
        lease["landlord_name"] = _landlord
    _tenant = first_field("tenant_name_0", "tenant_name")
    if _tenant:
        lease["tenant_name"] = _tenant
    from datetime import date as _date
    _commencement = first_field("commencement_date", "current_commencement_date")
    if _commencement:
        _c_iso = _normalize_date_to_iso(_commencement)
        # Store ISO so the UI's formatDate can render it; fall back to the raw string if unparseable.
        lease["commencement_date"] = _c_iso.isoformat() if _c_iso else _commencement
    _expiry = first_field("expiry_date", "current_expiration_date")
    if _expiry:
        _e_iso = _normalize_date_to_iso(_expiry)
        lease["expiry_date"] = _e_iso.isoformat() if _e_iso else _expiry
        if _e_iso:
            lease["days_to_expiry"] = (_e_iso - _date.today()).days
    if key_fields.get("currency"):
        lease["currency"] = key_fields["currency"]
    _rent = first_field("base_rent_monthly", "base_rent_amount", "monthly_rent")
    if _rent:
        amount = parse_amount(_rent)
        if amount is not None:
            lease["monthly_rent"] = amount
    if key_fields.get("city"):
        lease["city"] = key_fields["city"]
    if key_fields.get("lease_type"):
        lease["lease_type"] = key_fields["lease_type"]
    if key_fields.get("state_province"):
        lease["country"] = key_fields["state_province"]  # used as grouping key on leases page

    # Build address from extracted parts
    addr_parts = [key_fields.get(f) for f in ["street", "city", "state_province"] if key_fields.get(f)]
    if addr_parts:
        lease["address"] = ", ".join(addr_parts)

    # Normalize currency to ISO code
    _currency_map = {
        "indian rupee": "INR", "rupee": "INR", "rupees": "INR",
        "us dollar": "USD", "dollar": "USD",
        "euro": "EUR", "british pound": "GBP", "pound": "GBP",
    }
    raw_currency = lease.get("currency", "")
    if raw_currency:
        lease["currency"] = _currency_map.get(raw_currency.lower(), raw_currency.upper())

    db["leases"][lease_id] = lease


_CLAUSE_SILENT = {"lease is silent", "not found", "n/a", "not applicable", "none", "nil"}


def _populate_clauses_from_attrs(lease_id: str, attrs: list) -> int:
    """Populate the Clauses tab (db["lease_clauses"]) from the extraction's clause attributes.
    Idempotent — removes this lease's existing clauses first so a re-run doesn't duplicate.
    """
    for cid in [c for c, v in db["lease_clauses"].items() if v.get("lease_id") == lease_id]:
        del db["lease_clauses"][cid]
    count = 0
    for a in attrs:
        if a.get("category") != "Restrictive Clauses":
            continue
        val = (a.get("extracted_value") or "").strip()
        if not val or val.lower().rstrip(".") in _CLAUSE_SILENT:
            continue
        cid = str(uuid.uuid4())
        db["lease_clauses"][cid] = {
            "clause_id": cid,
            "lease_id": lease_id,
            "clause_ref": "",
            "clause_name": a.get("attribute_name", "Clause"),
            "clause_type": "Lease Clause",
            "full_text": val,
            "is_risky": False,
            "risk_level": None,
            "risk_reason": None,
            "risk_recommendation": None,
        }
        count += 1
    return count


def _finalize_extraction(lease_id: str, job_id: str, attrs: list, usage: dict, pdf_type: str, pdf_bytes: bytes, file_hash: str = "",
                          user_id: str | None = None, org_id: str | None = None) -> None:
    """Shared post-extraction: enrich the lease record + persist to disk (single & package)."""
    try:
        db["attributes"][lease_id] = attrs

        # Store pdf_type on the lease record
        lease = db["leases"].get(lease_id, {})
        lease["pdf_type"] = pdf_type

        # Initialise token usage record for this lease
        db["token_usage"][lease_id] = {
            "lease_id": lease_id,
            "extraction": usage,
            "locate_calls": [],
            "total_cost_usd": usage["cost_usd"],
        }

        # Enrich the lease summary record (commencement, expiry, rent, address, …) from extraction.
        _enrich_lease_record(lease_id, attrs)
        # Populate the Clauses tab from the extracted clause attributes.
        _populate_clauses_from_attrs(lease_id, attrs)

        db["upload_jobs"][job_id]["status"] = "review_pending"

        # Phase B: update the leases/lease_files rows created by Phase A (persist_uploaded_files
        # at upload time) now that classification/enrichment is known. No file bytes to upload —
        # S3 already has them.
        _doc_set = db.get("lease_doc_sets", {}).get(lease_id)
        _file_name = db["upload_jobs"][job_id].get("fileName", lease_id)

        file_updates = []
        if _doc_set:
            for _d in _doc_set:
                _did = _d["documentId"]
                file_updates.append({
                    "documentId": _did, "fileName": _d["fileName"], "pdfType": _d.get("pdfType"),
                    "isPrimary": _d.get("isPrimary", _did == lease_id), "docType": _d.get("docType"),
                    "pageCount": db["leases"].get(lease_id, {}).get("page_count") if _did == lease_id else None,
                })
        else:
            file_updates.append({
                "documentId": lease_id, "fileName": _file_name, "pdfType": pdf_type, "isPrimary": True,
                "docType": "Base",
                "pageCount": db["leases"].get(lease_id, {}).get("page_count"),
            })

        try:
            finalize_lease_persistence(
                lease_id=lease_id, org_id=org_id,
                lease_record=db["leases"].get(lease_id, {}), attrs=attrs,
                usage=db["token_usage"][lease_id], file_updates=file_updates,
            )
            _xlog.info("[%s] STEP 7 persist ✓ → Postgres + S3 (%d file(s)) | job %s review_pending",
                       lease_id, len(file_updates), job_id)
        except Exception:
            _xlog.exception("[%s] STEP 7 persist ✗ (Postgres + S3) — in-memory state intact, job %s review_pending",
                            lease_id, job_id)
            try:
                mark_lease_failed(lease_id, "STEP 7 persist failed — see server logs.")
            except Exception:
                _xlog.exception("[%s] mark_lease_failed also failed", lease_id)

        # Register in content hash cache so future uploads of identical bytes skip extraction
        if file_hash:
            db["content_hash_cache"][file_hash] = lease_id
            _persist_hash_cache()

        # ── Populate Administration module from extraction results ─────────
        try:
            from app.services.administration_transformer import populate_administration_from_extraction
            result = populate_administration_from_extraction(
                lease_id=lease_id,
                attrs=attrs,
                lease_record=db["leases"].get(lease_id, {}),
                file_name=db["upload_jobs"][job_id].get("fileName", lease_id),
            )
            if result.get("warnings"):
                _xlog.warning("[%s] admin transformer warnings: %s", lease_id, result["warnings"])
            _xlog.info("[%s] admin populated: obligations=%d renewals=%d payments=%d",
                       lease_id,
                       sum(1 for o in db["obligations"].values() if o.get("leaseId") == lease_id),
                       sum(1 for r in db["renewals"].values()    if r.get("leaseId") == lease_id),
                       sum(1 for p in db["payments"].values()    if p.get("leaseId") == lease_id))
        except Exception as adm_exc:
            _xlog.warning("[%s] admin transformer error (non-fatal): %s", lease_id, adm_exc)

    except Exception as exc:
        _xlog.exception("[%s] STEP 7 persist/enrich ✗", lease_id)
        db["upload_jobs"][job_id]["status"] = "failed"
        db["upload_jobs"][job_id]["errorMessage"] = str(exc)
        try:
            mark_lease_failed(lease_id, str(exc))
        except Exception:
            _xlog.exception("[%s] mark_lease_failed also failed", lease_id)


class AttributeUpdateRequest(BaseModel):
    userEditedValue: Optional[str] = None
    isVerified: Optional[bool] = None
    verifiedBy: Optional[str] = None


# ── IMPORTANT: Specific routes MUST come before /{leaseId} ─────────────────

@router.post("/upload")
async def upload_lease(
    file: UploadFile = File(...), background_tasks: BackgroundTasks = None,
    current: tuple[User, UserSession] = Depends(get_current_user),
):
    user, _session = current
    job_id = str(uuid.uuid4())

    # ── Sample document → existing mock path (unchanged) ──────────────────
    if file.filename == "India_Commercial_Lease_Mumbai.pdf":
        lease_ids = list(db["leases"].keys())
        india_lease_id = next(
            (lid for lid, l in db["leases"].items() if l.get("store_code") == "IN-001"), None
        )
        target_lease_id = india_lease_id or (lease_ids[len(db["upload_jobs"]) % len(lease_ids)] if lease_ids else str(uuid.uuid4()))
        target_lease = db["leases"].get(target_lease_id, {})
        db["upload_jobs"][job_id] = {
            "jobId": job_id,
            "fileName": file.filename,
            "fileSizeMb": 2.4,
            "pageCount": target_lease.get("page_count", 48),
            "status": "extracting",
            "progressPercent": 0,
            "leaseId": target_lease_id,
            "errorMessage": None,
        }
        _job_poll_count[job_id] = 0
        return {"jobId": job_id, "fileName": file.filename, "fileSizeMb": 2.4, "pageCount": 48, "status": "extracting", "estimatedSeconds": 8}

    # ── Real upload → Claude extraction ───────────────────────────────────
    pdf_bytes = await file.read()
    if len(pdf_bytes) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty (0 bytes).")

    # ── Cache check: identical PDF bytes → reuse prior completed extraction ──
    # import hashlib
    # file_hash = hashlib.sha256(pdf_bytes).hexdigest()
    # existing_lease_id = db["content_hash_cache"].get(file_hash)
    # if existing_lease_id:
    #     existing_job = next(
    #         (j for j in db["upload_jobs"].values()
    #          if j.get("leaseId") == existing_lease_id and j.get("status") == "review_pending"),
    #         None,
    #     )
    #     if existing_job:
    #         return {
    #             "cache_hit": True,
    #             "leaseId": existing_lease_id,
    #             "jobId": existing_job["jobId"],
    #             "fileName": file.filename,
    #             "status": "review_pending",
    #         }

    lease_id = str(uuid.uuid4())
    file_size_mb = round(len(pdf_bytes) / (1024 * 1024), 2)

    db["leases"][lease_id] = {
        "lease_id": lease_id,
        "store_name": file.filename.replace(".pdf", "").replace("_", " "),
        "store_code": f"UP-{lease_id[:6].upper()}",
        "landlord_name": "Extracting...",
        "status": "draft",
        "currency": "INR",
        "commencement_date": None,
        "expiry_date": None,
        "days_to_expiry": 9999,
        "monthly_rent": 0,
        "portfolio_id": None,
        "page_count": 0,
        "has_unreviewed_fields": False,
        "created_by": str(user.id),
        "assigned_to": str(user.id),
        "org_id": str(user.org_id),
    }

    # Phase A: persist the upload (S3 + leases/lease_files rows, extraction_status='extracting')
    # synchronously, before extraction starts. If this fails, fail the upload outright.
    try:
        persist_uploaded_files(
            lease_id=lease_id, org_id=str(user.org_id), created_by=str(user.id),
            lease_record=db["leases"][lease_id],
            files=[{
                "documentId": lease_id, "fileName": file.filename, "bytes": pdf_bytes,
                "isPrimary": True, "docType": "Base",
            }],
        )
    except Exception as exc:
        del db["leases"][lease_id]
        _xlog.exception("[%s] Phase A persist failed (upload rejected)", lease_id)
        raise HTTPException(status_code=500, detail=f"Failed to persist upload to storage: {exc}")

    db["pg_lease_ids"].add(lease_id)
    db["lease_documents"][lease_id] = pdf_bytes
    db["upload_jobs"][job_id] = {
        "jobId": job_id,
        "fileName": file.filename,
        "fileSizeMb": file_size_mb,
        "pageCount": 0,
        "status": "extracting",
        "progressPercent": 0,
        "leaseId": lease_id,
        "errorMessage": None,
    }
    _job_poll_count[job_id] = 0

    background_tasks.add_task(_run_extraction, pdf_bytes, lease_id, job_id, file_hash, user_id=str(user.id), org_id=str(user.org_id))

    return {"jobId": job_id, "fileName": file.filename, "fileSizeMb": file_size_mb, "pageCount": 0, "status": "extracting", "estimatedSeconds": 30}


@router.post("/upload-package")
async def upload_lease_package(
    files: list[UploadFile] = File(...), background_tasks: BackgroundTasks = None,
    current: tuple[User, UserSession] = Depends(get_current_user),
):
    """
    Folder upload — multiple related PDFs (base + amendments + renewals) → ONE merged lease.
    The xts engine classifies/sorts by filename and merges newest-wins. Reuses the same job
    polling (/status/{job_id}) and persistence as single upload.
    """
    user, _session = current
    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")

    payloads: list[tuple[str, bytes]] = []
    for f in files:
        data = await f.read()
        if not data:
            continue
        name = f.filename or ""
        if name.lower().endswith(".pdf"):
            payloads.append((name, data))
    if not payloads:
        raise HTTPException(status_code=400, detail="No non-empty PDF files in the folder.")

    # ── Cache check: same set of PDFs (by sorted filename+bytes) → reuse prior extraction ──
    # import hashlib
    # sorted_payloads = sorted(payloads, key=lambda x: x[0])
    # folder_hash = hashlib.sha256(b"".join(data for _, data in sorted_payloads)).hexdigest()
    # existing_lease_id = db["content_hash_cache"].get(folder_hash)
    # if existing_lease_id:
    #     existing_job = next(
    #         (j for j in db["upload_jobs"].values()
    #          if j.get("leaseId") == existing_lease_id and j.get("status") == "review_pending"),
    #         None,
    #     )
    #     if existing_job:
    #         return {
    #             "cache_hit": True,
    #             "leaseId": existing_lease_id,
    #             "jobId": existing_job["jobId"],
    #             "fileName": existing_job["fileName"],
    #             "status": "review_pending",
    #         }

    job_id = str(uuid.uuid4())
    lease_id = str(uuid.uuid4())
    total_mb = round(sum(len(d) for _, d in payloads) / (1024 * 1024), 2)
    # Folder/lease display name: use the parent folder if the browser sent paths, else first file.
    first_path = files[0].filename or "lease package"
    folder_name = first_path.split("/")[0] if "/" in first_path else payloads[0][0].replace(".pdf", "")

    db["leases"][lease_id] = {
        "lease_id": lease_id,
        "store_name": folder_name.replace("_", " "),
        "store_code": f"PKG-{lease_id[:6].upper()}",
        "landlord_name": "Extracting...",
        "status": "draft",
        "currency": "INR",
        "commencement_date": None,
        "expiry_date": None,
        "days_to_expiry": 9999,
        "monthly_rent": 0,
        "portfolio_id": None,
        "page_count": 0,
        "has_unreviewed_fields": False,
        "created_by": str(user.id),
        "assigned_to": str(user.id),
        "org_id": str(user.org_id),
    }
    # Phase A: persist the upload (S3 + leases/lease_files rows, extraction_status='extracting')
    # synchronously, before extraction starts. Per-file classification (isPrimary/docType) isn't
    # known yet — Phase B (finalize_lease_persistence) fills those in once extraction completes.
    # phase_a_doc_ids maps fileName -> the lease_files row id, so Phase B can update it in place.
    phase_a_doc_ids: dict[str, str] = {name: str(uuid.uuid4()) for name, _ in payloads}
    try:
        persist_uploaded_files(
            lease_id=lease_id, org_id=str(user.org_id), created_by=str(user.id),
            lease_record=db["leases"][lease_id],
            files=[{
                "documentId": phase_a_doc_ids[name], "fileName": name, "bytes": data,
                "isPrimary": False, "docType": None,
            } for name, data in payloads],
        )
    except Exception as exc:
        del db["leases"][lease_id]
        _xlog.exception("[%s] Phase A persist failed (upload rejected)", lease_id)
        raise HTTPException(status_code=500, detail=f"Failed to persist upload to storage: {exc}")

    db["pg_lease_ids"].add(lease_id)
    db["upload_jobs"][job_id] = {
        "jobId": job_id,
        "fileName": f"{folder_name} ({len(payloads)} documents)",
        "fileSizeMb": total_mb,
        "pageCount": 0,
        "status": "extracting",
        "progressPercent": 0,
        "leaseId": lease_id,
        "errorMessage": None,
    }
    _job_poll_count[job_id] = 0

    background_tasks.add_task(_run_package_extraction, payloads, lease_id, job_id, phase_a_doc_ids, "", user_id=str(user.id), org_id=str(user.org_id))

    return {
        "jobId": job_id,
        "fileName": db["upload_jobs"][job_id]["fileName"],
        "fileCount": len(payloads),
        "fileSizeMb": total_mb,
        "status": "extracting",
        "estimatedSeconds": 30 * len(payloads),
    }


@router.get("/status/{job_id}")
def get_job_status(job_id: str, _: str = Depends(current_org_id)):
    job = db["upload_jobs"].get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # If background extraction already completed (or failed), return immediately
    if job.get("status") == "review_pending":
        return {"jobId": job_id, "status": "review_pending", "progressPercent": 100, "errorMessage": None, "leaseId": job["leaseId"]}
    if job.get("status") == "failed":
        return {"jobId": job_id, "status": "failed", "progressPercent": 0, "errorMessage": job.get("errorMessage"), "leaseId": None}

    # Real upload — only the background task can mark it done; keep returning extracting
    if job.get("fileName") != "India_Commercial_Lease_Mumbai.pdf":
        return {"jobId": job_id, "status": "extracting", "progressPercent": 50, "errorMessage": None, "leaseId": None}

    # Sample doc: self-repair + 3-poll mock counter
    india_lease_id = next(
        (lid for lid, l in db["leases"].items() if l.get("store_code") == "IN-001"), None
    )
    if india_lease_id and job.get("leaseId") != india_lease_id:
        job["leaseId"] = india_lease_id
        db["upload_jobs"][job_id]["leaseId"] = india_lease_id

    count = _job_poll_count.get(job_id, 0) + 1
    _job_poll_count[job_id] = count

    if count <= 2:
        return {"jobId": job_id, "status": "extracting", "progressPercent": min(30 + count * 25, 75), "errorMessage": None, "leaseId": None}
    else:
        db["upload_jobs"][job_id]["status"] = "review_pending"
        return {"jobId": job_id, "status": "review_pending", "progressPercent": 100, "errorMessage": None, "leaseId": job["leaseId"]}


@router.get("/document/{lease_id}")
def get_lease_document(lease_id: str, user_info: dict = Depends(current_user_info)):
    # `lease_id` here is a document key — the lease's primary PDF (keyed by lease_id) OR a
    # specific documentId from a folder upload's manifest. Both live in db["lease_documents"].
    lease = _find_lease_for_doc(lease_id)
    if lease:
        require_lease_access(lease, user_info["org_id"], user_info["user_id"], user_info["is_admin"])
    pdf = db.get("lease_documents", {}).get(lease_id)
    if not pdf:
        raise HTTPException(status_code=404, detail="No document stored for this lease")
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": f"inline; filename={lease_id}.pdf"})


@router.get("/page-image/{doc_key}/{page}")
def get_page_image(doc_key: str, page: int, dpi: int = 150, user_info: dict = Depends(current_user_info)):
    """
    Server-side rasterized page (PNG) via MuPDF. Used for scanned PDFs, whose JPEG+soft-mask
    images pdf.js renders blank — MuPDF (like Preview/Chrome) composites them correctly.
    `doc_key` is a document key (lease_id for the primary, or a folder documentId). `page` is 1-based.
    """
    lease = _find_lease_for_doc(doc_key)
    if lease:
        require_lease_access(lease, user_info["org_id"], user_info["user_id"], user_info["is_admin"])
    pdf_bytes = db.get("lease_documents", {}).get(doc_key)
    if not pdf_bytes:
        raise HTTPException(status_code=404, detail="No document stored for this key")
    import fitz
    dpi = max(72, min(dpi, 300))
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        if page < 1 or page > doc.page_count:
            raise HTTPException(status_code=404, detail="Page out of range")
        pix = doc[page - 1].get_pixmap(dpi=dpi)
        png = pix.tobytes("png")
    finally:
        doc.close()
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "max-age=3600"})


@router.post("/clear-highlights")
def clear_highlights(user=Depends(require_admin)):
    """
    Drop all cached highlight boxes (attribute bbox/bbox_rects + field_bboxes) so fields re-locate
    with the current matching logic. Keeps the OCR word cache (ocr_pages), so re-locating is fast.
    Scoped to the admin's own organisation.
    """
    org_id = str(user.org_id)
    cleared = 0
    for lease_id, attrs in db.get("attributes", {}).items():
        if db["leases"].get(lease_id, {}).get("org_id") != org_id:
            continue
        changed = False
        for a in attrs:
            if a.get("bbox") is not None or a.get("bbox_rects") is not None:
                a["bbox"] = None
                a["bbox_rects"] = None
                changed = True
        if changed:
            _persist_extraction(lease_id)
            cleared += 1
    for lease_id in [lid for lid, l in db["leases"].items() if l.get("org_id") == org_id]:
        db.get("field_bboxes", {}).pop(lease_id, None)
    return {"clearedLeases": cleared}


@router.post("/reenrich")
def reenrich_leases(lease_id: Optional[str] = None, user=Depends(require_admin)):
    """
    Re-run the post-extraction enrichment (lease summary fields + Administration tabs) against the
    already-stored attributes — used to backfill leases extracted before a mapping fix, without
    re-uploading. Pass ?lease_id=… for one lease, or omit to backfill every extracted lease.
    Scoped to the admin's own organisation.
    """
    org_id = str(user.org_id)
    if lease_id:
        lease = db["leases"].get(lease_id)
        if not lease or lease.get("org_id") != org_id:
            raise HTTPException(status_code=404, detail="Lease not found")
        targets = [lease_id]
    else:
        targets = [lid for lid in db.get("attributes", {}) if db["leases"].get(lid, {}).get("org_id") == org_id]
    updated = []
    for lid in targets:
        attrs = db.get("attributes", {}).get(lid)
        if not attrs:
            continue
        _enrich_lease_record(lid, attrs)
        _populate_clauses_from_attrs(lid, attrs)
        try:
            from app.services.administration_transformer import populate_administration_from_extraction
            populate_administration_from_extraction(
                lease_id=lid,
                attrs=attrs,
                lease_record=db["leases"].get(lid, {}),
                file_name=db["leases"].get(lid, {}).get("store_name", lid),
            )
        except Exception as e:
            _xlog.warning("[%s] reenrich admin transformer error (non-fatal): %s", lid, e)
        _persist_extraction(lid)
        lr = db["leases"].get(lid, {})
        updated.append({
            "leaseId": lid,
            "commencement": lr.get("commencement_date"),
            "expiry": lr.get("expiry_date"),
            "daysToExpiry": lr.get("days_to_expiry"),
            "monthlyRent": lr.get("monthly_rent"),
        })
    return {"updated": updated, "count": len(updated)}


@router.get("/{lease_id}/documents")
def get_lease_documents(lease_id: str, user_info: dict = Depends(current_user_info)):
    """
    List the PDFs that make up a lease, for the document-viewer switcher.
    Folder uploads return the full manifest (Base → newest). Single uploads / seeded leases
    return a single primary entry so the frontend renders uniformly (no switcher shown).
    """
    org_id = user_info["org_id"]
    lease = db["leases"].get(lease_id, {})
    if lease:
        require_lease_access(lease, org_id, user_info["user_id"], user_info["is_admin"])
    manifest = db.get("lease_doc_sets", {}).get(lease_id)
    if manifest:
        return {"documents": manifest}
    has_doc = lease_id in db.get("lease_documents", {})
    return {"documents": [{
        "documentId": lease_id,
        "fileName": f"{lease.get('store_name', 'Lease')}.pdf",
        "docType": "Base",
        "pdfType": lease.get("pdf_type", "typed"),
        "isPrimary": True,
        "hasDocument": has_doc,
    }]}


@router.post("/bulk-upload")
async def bulk_upload(files: list[UploadFile] = File(...), user=Depends(require_admin)):
    batch_id = str(uuid.uuid4())
    file_entries = []
    for f in files:
        file_id = str(uuid.uuid4())
        file_entries.append({"fileId": file_id, "fileName": f.filename, "status": "queued"})

    db["bulk_batches"][batch_id] = {
        "batchId": batch_id,
        "files": file_entries,
        "createdAt": datetime.utcnow().isoformat(),
    }
    return {"batchId": batch_id, "files": file_entries}


@router.get("/bulk-status/{batch_id}")
def get_bulk_status(batch_id: str, org_id: str = Depends(current_org_id)):
    batch = db["bulk_batches"].get(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    lease_ids = list(db["leases"].keys())
    files = []
    for i, f in enumerate(batch["files"]):
        files.append({
            "fileId": f["fileId"],
            "fileName": f["fileName"],
            "status": "review_pending",
            "leaseId": lease_ids[i % len(lease_ids)] if lease_ids else None,
            "errorMessage": None,
        })

    return {"batchId": batch_id, "files": files}


@router.get("/upload-history")
def get_upload_history(user_info: dict = Depends(current_user_info)):
    org_id = user_info["org_id"]
    user_id = user_info["user_id"]
    is_admin = user_info["is_admin"]
    visible = [j for j in db["upload_jobs"].values() if _job_visible_to(j, org_id, user_id, is_admin)]
    return {"jobs": visible}


@router.delete("/upload-history")
def clear_upload_history(user_info: dict = Depends(current_user_info)):
    """
    Remove completed/failed jobs from upload history.
    In-progress jobs are kept. Marks each cleared lease JSON with
    dismissed_from_history=true so the entry does not come back after a restart.
    """
    org_id = user_info["org_id"]
    user_id = user_info["user_id"]
    is_admin = user_info["is_admin"]
    removable_statuses = {"review_pending", "saved", "failed"}
    to_remove = [
        job_id for job_id, job in db["upload_jobs"].items()
        if job.get("status") in removable_statuses
        and _job_visible_to(job, org_id, user_id, is_admin)
    ]
    for job_id in to_remove:
        del db["upload_jobs"][job_id]
        # Persist the dismissal so it survives a backend restart
        json_path = _extraction_path(job_id)
        if _os.path.exists(json_path):
            try:
                with open(json_path, encoding="utf-8") as f:
                    data = _json.load(f)
                data["dismissed_from_history"] = True
                with open(json_path, "w", encoding="utf-8") as f:
                    _json.dump(data, f, indent=4, ensure_ascii=False, default=str)
            except Exception as e:
                print(f"Warning: could not mark {job_id} as dismissed: {e}")
    return {"cleared": len(to_remove)}


@router.get("")
@router.get("/")
def list_leases(
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    portfolio_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    expiry_month: Optional[str] = Query(None),
    user_info: dict = Depends(current_user_info),
):
    org_id = user_info["org_id"]
    leases = user_scoped_leases(db["leases"].values(), org_id, user_info["user_id"], user_info["is_admin"])

    if status:
        leases = [l for l in leases if l["status"] == status]
    if portfolio_id:
        leases = [l for l in leases if l.get("portfolio_id") == portfolio_id]
    if search:
        s = search.lower()
        leases = [l for l in leases if s in l.get("store_name", "").lower() or s in l.get("landlord_name", "").lower() or s in l.get("store_code", "").lower()]
    if expiry_month:
        leases = [l for l in leases if l.get("expiry_date", "").startswith(expiry_month)]

    # Sort by days_to_expiry ascending
    leases.sort(key=lambda x: x.get("days_to_expiry", 9999))

    total = len(leases)
    start = (page - 1) * pageSize
    page_leases = leases[start: start + pageSize]

    # Annotate each lease with how many source PDFs it was built from, so the list can flag
    # merged folder uploads (e.g. a "2 docs" badge) instead of looking like a single document.
    _doc_sets = db.get("lease_doc_sets", {})
    page_leases = [
        {**l, "document_count": len(_doc_sets.get(l["lease_id"], [])) or 1}
        for l in page_leases
    ]

    all_leases = user_scoped_leases(db["leases"].values(), org_id, user_info["user_id"], user_info["is_admin"])
    stats = {
        "total": len(all_leases),
        "expiringSoon": len([l for l in all_leases if l["status"] == "expiring"]),
        "holdover": len([l for l in all_leases if l["status"] == "holdover"]),
        "active": len([l for l in all_leases if l["status"] == "active"]),
    }

    return {
        "leases": page_leases,
        "total": total,
        "page": page,
        "pageSize": pageSize,
        "stats": stats,
    }


@router.get("/{lease_id}/extracted")
def get_extracted(lease_id: str, user_info: dict = Depends(current_user_info)):
    org_id = user_info["org_id"]
    lease = db["leases"].get(lease_id)
    if not lease:
        raise HTTPException(status_code=404, detail="Lease not found")
    require_lease_access(lease, org_id, user_info["user_id"], user_info["is_admin"])

    # For the India demo lease, ensure attributes are always the hardcoded demo set.
    # This self-repairs if the server was running with old seed data.
    if lease.get("store_code") == "IN-001":
        cached = db["attributes"].get(lease_id, [])
        landlord_attr = next((a for a in cached if a.get("attribute_key") == "landlord_name"), None)
        if landlord_attr is None or landlord_attr.get("extracted_value") != "Prestige Estates Pvt Ltd":
            db["attributes"][lease_id] = _make_india_demo_attributes(lease_id)

    attrs = db["attributes"].get(lease_id, [])

    # Collapse duplicate attribute_keys (the engine config lists clauses twice). Fixes leases
    # extracted before the adapter-side dedup, without needing a re-upload. Persist if it changed.
    if attrs:
        from app.services.lease_extractor_xts import _dedupe_attrs
        deduped = _dedupe_attrs(attrs)
        if len(deduped) != len(attrs):
            db["attributes"][lease_id] = deduped
            attrs = deduped
            _persist_extraction(lease_id)

    low_conf = len([a for a in attrs if a["confidence_level"] == "low"])
    not_found = len([a for a in attrs if a.get("extracted_value") is None])

    scores = [a["confidence_score"] for a in attrs if a.get("confidence_score")]
    overall = round(sum(scores) / len(scores)) if scores else 75

    return {
        "leaseId": lease_id,
        "fileName": f"{lease.get('store_name', 'lease')}.pdf",
        "pageCount": lease.get("page_count", 48),
        "extractedAt": datetime.utcnow().isoformat(),
        "overallConfidence": overall,
        "totalAttributes": len(attrs),
        "extractedCount": len(attrs) - not_found,
        "lowConfidenceCount": low_conf,
        "notFoundCount": not_found,
        "hasDocument": lease_id in db.get("lease_documents", {}),
        "pdfType": lease.get("pdf_type", "typed"),
        "attributes": attrs,
    }


@router.patch("/{lease_id}/attributes/{attribute_id}")
def update_attribute(lease_id: str, attribute_id: str, body: AttributeUpdateRequest, user_info: dict = Depends(current_user_info)):
    org_id = user_info["org_id"]
    lease = db["leases"].get(lease_id)
    if lease:
        require_lease_access(lease, org_id, user_info["user_id"], user_info["is_admin"])
    attrs = db["attributes"].get(lease_id)
    if not attrs:
        raise HTTPException(status_code=404, detail="Lease attributes not found")

    for attr in attrs:
        if attr["attribute_id"] == attribute_id:
            if body.userEditedValue is not None:
                attr["user_edited_value"] = body.userEditedValue
            if body.isVerified is not None:
                attr["is_verified"] = body.isVerified
                if body.isVerified:
                    attr["verified_by"] = body.verifiedBy or "James Wilson"
                    attr["verified_at"] = datetime.utcnow().isoformat()
            _persist_extraction(lease_id)
            return attr

    raise HTTPException(status_code=404, detail="Attribute not found")


# ── Token usage ───────────────────────────────────────────────────────────────

@router.get("/{lease_id}/token-usage")
def get_token_usage(lease_id: str, user_info: dict = Depends(current_user_info)):
    org_id = user_info["org_id"]
    lease = db["leases"].get(lease_id)
    if lease:
        require_lease_access(lease, org_id, user_info["user_id"], user_info["is_admin"])
    usage = db["token_usage"].get(lease_id)
    if not usage:
        raise HTTPException(status_code=404, detail="No token usage recorded for this lease")
    return usage


# ── Scanned PDF field location (lazy bbox via Claude vision) ──────────────────

class LocateFieldRequest(BaseModel):
    attribute_key: str
    source_text: str
    page_number: int
    extracted_value: Optional[str] = None
    document_id: Optional[str] = None   # folder uploads: locate against the displayed document


@router.post("/{lease_id}/locate-field")
async def locate_field(lease_id: str, body: LocateFieldRequest, user_info: dict = Depends(current_user_info)):
    org_id = user_info["org_id"]
    lease_check = db["leases"].get(lease_id, {})
    if lease_check:
        require_lease_access(lease_check, org_id, user_info["user_id"], user_info["is_admin"])
    # For folder uploads the viewer may show a non-primary document; locate against whichever
    # document is on screen. Defaults to the lease's primary PDF (document_id == lease_id).
    doc_key = body.document_id or lease_id
    pdf_bytes = db.get("lease_documents", {}).get(doc_key)
    if not pdf_bytes:
        raise HTTPException(status_code=404, detail="No PDF stored for this document")

    # pdf_type can differ per document — read it from the folder manifest when present.
    pdf_type = db["leases"].get(lease_id, {}).get("pdf_type", "typed")
    for _d in db.get("lease_doc_sets", {}).get(lease_id, []):
        if _d.get("documentId") == doc_key:
            pdf_type = _d.get("pdfType", pdf_type)
            break

    # Return cached bbox if already located (cache is per-document to avoid cross-doc collisions).
    cached = db["field_bboxes"].get(doc_key, {}).get(body.attribute_key)
    if cached is not None:
        cached_rects = next(
            (a.get("bbox_rects") for a in db["attributes"].get(lease_id, [])
             if a.get("attribute_key") == body.attribute_key),
            None,
        ) if doc_key == lease_id else None
        return {"bbox": cached, "bbox_rects": cached_rects, "cached": True, "tokens": None}

    bbox_rects = None
    tokens = None

    if pdf_type == "typed":
        # Digital PDF → exact geometry from the PDF content stream (no LLM cost).
        from app.services.lease_extractor import locate_clause_in_typed_page
        located = locate_clause_in_typed_page(
            pdf_bytes, body.source_text, body.page_number, body.extracted_value
        )
        bbox = located["bbox"] if located else None
        bbox_rects = located["bbox_rects"] if located else None
    else:
        # Scanned PDF → 1) free local OCR geometry (exact); 2) Claude vision fallback.
        from app.services.lease_extractor import (
            locate_clause_in_scanned_page_ocr, locate_text_in_scanned_page, ANTHROPIC_API_KEY,
        )

        page_cache = db["ocr_pages"].setdefault(doc_key, {})
        pages_before = len(page_cache)
        located = locate_clause_in_scanned_page_ocr(
            pdf_bytes, body.source_text, body.page_number, body.extracted_value, page_cache,
        )
        # If a new page was OCR'd, persist the cache so we never re-OCR it after a restart.
        if len(page_cache) > pages_before:
            _persist_ocr_pages(doc_key)
        if located:
            bbox = located["bbox"]
            bbox_rects = located["bbox_rects"]
        else:
            # OCR couldn't confidently locate it → fall back to Claude vision (approximate).
            from anthropic import AsyncAnthropic
            if not ANTHROPIC_API_KEY:
                raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set")
            client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
            bbox, tokens = await locate_text_in_scanned_page(
                client, pdf_bytes, body.source_text, body.page_number
            )

    # Cache in memory: field_bboxes store (per-document key)
    if doc_key not in db["field_bboxes"]:
        db["field_bboxes"][doc_key] = {}
    db["field_bboxes"][doc_key][body.attribute_key] = bbox

    # Write bbox into the attribute record only for the primary document, so the merged
    # attribute's persisted bbox isn't overwritten by a non-primary doc's coordinates.
    if doc_key == lease_id:
        for attr in db["attributes"].get(lease_id, []):
            if attr.get("attribute_key") == body.attribute_key:
                attr["bbox"] = bbox
                attr["bbox_rects"] = bbox_rects
                break

    # Accumulate token usage in memory (vision path only)
    if tokens is not None:
        usage_record = db["token_usage"].get(lease_id)
        if usage_record:
            usage_record["locate_calls"].append({
                "attribute_key": body.attribute_key,
                **tokens,
            })
            usage_record["total_cost_usd"] = round(
                usage_record["total_cost_usd"] + tokens["cost_usd"], 6
            )

    # Persist both JSONs so bbox and cost survive a restart
    _persist_extraction(lease_id)
    _persist_usage(lease_id)

    return {"bbox": bbox, "bbox_rects": bbox_rects, "cached": False, "tokens": tokens}


@router.post("/{lease_id}/save")
def save_lease(lease_id: str, user_info: dict = Depends(current_user_info)):
    from app.services.task_emitter import emit_task
    org_id = user_info["org_id"]
    lease = db["leases"].get(lease_id)
    if not lease:
        raise HTTPException(status_code=404, detail="Lease not found")
    require_lease_access(lease, org_id, user_info["user_id"], user_info["is_admin"])

    attrs = db["attributes"].get(lease_id, [])
    unreviewed = [a for a in attrs if not a["is_verified"] and a["confidence_level"] == "low"]

    lease["status"] = "active" if lease["status"] == "draft" else lease["status"]
    lease["has_unreviewed_fields"] = len(unreviewed) > 0

    # Workflow 1 — low-confidence extraction
    if len(unreviewed) > 0:
        emit_task(
            title=f"Review {len(unreviewed)} low-confidence fields — {lease.get('store_name', lease_id)}",
            source="abstraction",
            action_type="extraction_review_pending",
            assignee_role="lease_admin",
            priority="medium",
            lease_id=lease_id,
            watched_by_role="re_director",
            due_days=3,
        )

    # Re-populate Administration module with (possibly user-edited) values
    try:
        from app.services.administration_transformer import populate_administration_from_extraction
        job = next(
            (j for j in db.get("upload_jobs", {}).values() if j.get("leaseId") == lease_id),
            {},
        )
        populate_administration_from_extraction(
            lease_id=lease_id,
            attrs=attrs,
            lease_record=lease,
            file_name=job.get("fileName", lease_id),
        )
        _persist_extraction(lease_id)
    except Exception:
        pass

    return {
        "leaseId": lease_id,
        "savedAt": datetime.utcnow().isoformat(),
        "status": lease["status"],
        "hasUnreviewedFields": len(unreviewed) > 0,
    }


@router.get("/{lease_id}/clauses")
def get_lease_clauses(lease_id: str, user_info: dict = Depends(current_user_info)):
    org_id = user_info["org_id"]
    lease = db["leases"].get(lease_id)
    if not lease:
        raise HTTPException(status_code=404, detail="Lease not found")
    require_lease_access(lease, org_id, user_info["user_id"], user_info["is_admin"])
    clauses = [c for c in db["lease_clauses"].values() if c["lease_id"] == lease_id]
    # Risky first, then by clause type
    clauses.sort(key=lambda x: (not x["is_risky"], x["clause_type"]))
    return {
        "clauses": [
            {
                "clauseId": c["clause_id"],
                "clauseRef": c["clause_ref"],
                "clauseName": c["clause_name"],
                "clauseType": c["clause_type"],
                "fullText": c["full_text"],
                "isRisky": c["is_risky"],
                "riskLevel": c["risk_level"],
                "riskReason": c["risk_reason"],
                "riskRecommendation": c["risk_recommendation"],
            }
            for c in clauses
        ]
    }


@router.get("/{lease_id}/rent-schedule")
def get_rent_schedule(lease_id: str, user_info: dict = Depends(current_user_info)):
    org_id = user_info["org_id"]
    lease = db["leases"].get(lease_id)
    if not lease:
        raise HTTPException(status_code=404, detail="Lease not found")
    require_lease_access(lease, org_id, user_info["user_id"], user_info["is_admin"])
    schedule = db["rent_schedules"].get(lease_id, [])

    # Build a quick lookup: period -> payment match for this lease
    matches_by_period: dict = {}
    for m in db["payment_matches"].values():
        if m["lease_id"] == lease_id:
            matches_by_period[m["period"]] = m

    return {
        "schedule": [
            {
                "scheduleId": s["schedule_id"],
                "periodNumber": s["period_number"],
                "startDate": s["start_date"],
                "endDate": s["end_date"],
                "monthlyRent": s["monthly_rent"],
                "annualRent": s["annual_rent"],
                "changePct": s["change_pct"],
                "reviewType": s["review_type"],
                "status": s["status"],
                # Enhanced fields
                "camEstimate": s.get("cam_estimate", round(s["monthly_rent"] * 0.08, 2)),
                "propertyTaxEstimate": s.get("property_tax_estimate", round(s["monthly_rent"] * 0.03, 2)),
                "insuranceEstimate": s.get("insurance_estimate", round(s["monthly_rent"] * 0.015, 2)),
                "totalExpected": s.get("total_expected", round(s["monthly_rent"] * 1.125, 2)),
                "paymentDueDay": s.get("payment_due_day", 1),
                "erpCostCentre": s.get("erp_cost_centre", ""),
                "erpGlAccount": s.get("erp_gl_account", ""),
                # Payment match data (null if no actuals imported for this period)
                "actualPaid": matches_by_period[s["start_date"][:7]]["actual_amount"]
                    if s["start_date"][:7] in matches_by_period else None,
                "variance": matches_by_period[s["start_date"][:7]]["variance"]
                    if s["start_date"][:7] in matches_by_period else None,
                "paymentStatus": matches_by_period[s["start_date"][:7]]["status"]
                    if s["start_date"][:7] in matches_by_period else None,
            }
            for s in schedule
        ]
    }


@router.get("/{lease_id}")
def get_lease(lease_id: str, user_info: dict = Depends(current_user_info)):
    org_id = user_info["org_id"]
    lease = db["leases"].get(lease_id)
    if not lease:
        raise HTTPException(status_code=404, detail="Lease not found")
    require_lease_access(lease, org_id, user_info["user_id"], user_info["is_admin"])

    # Attach amendments count
    amendments = [a for a in db["amendments"].values() if a["lease_id"] == lease_id]
    return {**lease, "amendmentCount": len(amendments)}

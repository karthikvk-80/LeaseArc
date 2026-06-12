import os
from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import auth, dashboard, leases, amendments, administration, negotiation, cam, payments as payments_router
from app.routers import compliance as compliance_router
from app.routers import intelligence as intelligence_router
from app.routers import pre_leasing as pre_leasing_router
from app.seed.seed_users import seed_users
from app.seed.seed_users_pg import seed_users_pg
from app.services.lease_persistence_pg import load_all_from_pg
from app.services.org_isolation import load_org_ids
from app.seed.seed_leases import seed_leases
from app.seed.seed_wework_demo import seed_wework_demo_leases
from app.seed.seed_cam import seed_cam
from app.seed.seed_disputes import seed_disputes
from app.seed.seed_clauses import seed_clauses
from app.seed.seed_rent_schedules import seed_rent_schedules
from app.seed.seed_administration import seed_all as seed_administration
from app.seed.seed_payments_schedule import seed_all as seed_payments
from app.seed.migrate_landlords import migrate_landlord_names_to_entities
from app.seed.seed_compliance import seed_all as seed_compliance
from app.seed.seed_intelligence import seed_all as seed_intelligence
from app.seed.seed_pre_leasing import seed_all as seed_pre_leasing

app = FastAPI(
    title="LeaseArc API",
    description="Mock API for the LeaseArc enterprise lease intelligence platform",
    version="1.0.0",
)

_cors_origins = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Allow large PDF uploads (default uvicorn limit is ~1MB; leases can be 50–200MB).
_max_upload_mb = int(os.getenv("MAX_UPLOAD_MB", "500"))
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > _max_upload_mb * 1024 * 1024:
            return JSONResponse(
                {"error": f"File too large. Max allowed: {_max_upload_mb}MB."},
                status_code=413,
            )
        return await call_next(request)

app.add_middleware(MaxBodySizeMiddleware)


def reload_uploaded_leases():
    """Reload previously uploaded leases from disk into memory on startup."""
    import json, os
    from app.mock_db import db
    extractions_dir = os.path.join(os.path.dirname(__file__), "../extractions")
    if not os.path.isdir(extractions_dir):
        print(f"No extractions directory at {extractions_dir} — skipping reload")
        return
    reloaded = 0
    for fname in os.listdir(extractions_dir):
        # Skip usage files and non-JSON files — handled separately below
        if not fname.endswith(".json") or fname.endswith("_usage.json") or fname.endswith("_ocr.json") or fname == "hash_cache.json":
            continue
        try:
            with open(os.path.join(extractions_dir, fname), encoding="utf-8") as f:
                data = json.load(f)
            lease_id = data.get("lease_id")
            if not lease_id:
                continue
            # Restore lease record
            if data.get("lease_record"):
                lease_record = data["lease_record"]
                if not lease_record.get("org_id"):
                    lease_record["org_id"] = db.get("org_ids", {}).get("Xtract.io")
                db["leases"][lease_id] = lease_record
            # Restore attributes (includes bbox if previously located)
            if data.get("attributes"):
                db["attributes"][lease_id] = data["attributes"]
                # Re-populate field_bboxes cache from persisted attribute bboxes
                bboxes = {
                    a["attribute_key"]: a["bbox"]
                    for a in data["attributes"]
                    if a.get("bbox") is not None and a.get("attribute_key")
                }
                if bboxes:
                    db["field_bboxes"][lease_id] = bboxes
            # Restore PDF bytes if file exists (primary doc is keyed by lease_id)
            pdf_path = os.path.join(extractions_dir, f"{lease_id}.pdf")
            file_size_mb = 0.0
            if os.path.exists(pdf_path):
                with open(pdf_path, "rb") as pf:
                    pdf_bytes = pf.read()
                    db["lease_documents"][lease_id] = pdf_bytes
                    file_size_mb = round(len(pdf_bytes) / (1024 * 1024), 2)
            # Folder uploads: restore the document manifest + each non-primary PDF for the switcher
            doc_set = data.get("documents")
            if doc_set:
                db["lease_doc_sets"][lease_id] = doc_set
                for _d in doc_set:
                    _did = _d.get("documentId")
                    if not _did or _did == lease_id:
                        continue
                    _dpath = os.path.join(extractions_dir, f"{_did}.pdf")
                    if os.path.exists(_dpath):
                        with open(_dpath, "rb") as _pf:
                            db["lease_documents"][_did] = _pf.read()
            # Restore upload job — skip if user explicitly cleared it
            if not data.get("dismissed_from_history"):
                db["upload_jobs"][lease_id] = {
                    "jobId": lease_id,
                    "fileName": data.get("file_name", f"{lease_id}.pdf"),
                    "fileSizeMb": file_size_mb,
                    "pageCount": 0,
                    "status": "review_pending",
                    "progressPercent": 100,
                    "leaseId": lease_id,
                    "errorMessage": None,
                }
            # Restore token usage from companion _usage.json if present
            usage_path = os.path.join(extractions_dir, f"{lease_id}_usage.json")
            if os.path.exists(usage_path):
                try:
                    with open(usage_path, encoding="utf-8") as f:
                        usage = json.load(f)
                    # Normalise to full token_usage schema if it's an older file
                    if "extraction" not in usage:
                        usage = {
                            "lease_id": lease_id,
                            "extraction": {
                                "model": usage.get("model"),
                                "input_tokens": usage.get("input_tokens"),
                                "output_tokens": usage.get("output_tokens"),
                                "cost_usd": usage.get("cost_usd"),
                            },
                            "locate_calls": usage.get("locate_calls", []),
                            "total_cost_usd": usage.get("total_cost_usd", usage.get("cost_usd", 0.0)),
                        }
                    db["token_usage"][lease_id] = usage
                except Exception:
                    pass
            # Restore OCR page cache so scanned pages are never re-OCR'd after a restart
            ocr_path = os.path.join(extractions_dir, f"{lease_id}_ocr.json")
            if os.path.exists(ocr_path):
                try:
                    with open(ocr_path, encoding="utf-8") as f:
                        db["ocr_pages"][lease_id] = json.load(f)
                except Exception:
                    pass
            # Repopulate the Clauses tab from the reloaded extraction (not persisted separately).
            try:
                from app.routers.leases import _populate_clauses_from_attrs
                if data.get("attributes"):
                    _populate_clauses_from_attrs(lease_id, data["attributes"])
            except Exception as cl_e:
                print(f"Warning: clause repopulate error for {lease_id}: {cl_e}")
            # Repopulate Administration module from the reloaded extraction
            try:
                from app.services.administration_transformer import populate_administration_from_extraction
                if data.get("attributes") and data.get("lease_record"):
                    populate_administration_from_extraction(
                        lease_id=lease_id,
                        attrs=data["attributes"],
                        lease_record=data["lease_record"],
                        file_name=data.get("file_name", f"{lease_id}.pdf"),
                    )
            except Exception as adm_e:
                print(f"Warning: admin transformer error for {lease_id}: {adm_e}")

            reloaded += 1
        except Exception as e:
            print(f"Warning: could not reload {fname}: {e}")
    if reloaded > 0:
        print(f"Reloaded {reloaded} previously uploaded lease(s) from disk")

    # Reload content hash cache so restart doesn't lose the dedup index
    hash_cache_path = os.path.join(extractions_dir, "hash_cache.json")
    if os.path.exists(hash_cache_path):
        try:
            with open(hash_cache_path, encoding="utf-8") as f:
                db["content_hash_cache"] = json.load(f)
            print(f"Reloaded {len(db['content_hash_cache'])} content hash cache entries")
        except Exception as e:
            print(f"Warning: could not reload hash cache: {e}")


def _migrate_field_names():
    """Migrate old field names to new ones: source_text_snippet → source_clause, source_page_number → page_number."""
    from app.mock_db import db

    migrated = 0
    for lease_id, attrs in db.get("attributes", {}).items():
        for attr in attrs:
            # Migrate source_text_snippet → source_clause
            if "source_text_snippet" in attr and "source_clause" not in attr:
                attr["source_clause"] = attr.pop("source_text_snippet")

            # Migrate source_page_number → page_number
            if "source_page_number" in attr and "page_number" not in attr:
                attr["page_number"] = attr.pop("source_page_number")

            # Migrate source_highlight_coordinates → bbox
            if "source_highlight_coordinates" in attr and "bbox" not in attr:
                attr["bbox"] = None
                attr.pop("source_highlight_coordinates", None)

            # Ensure translation field exists
            if "translation" not in attr:
                attr["translation"] = None

            migrated += 1

    if migrated > 0:
        print(f"✓ Migrated {migrated} attributes to new field names")


async def _backfill_translations():
    """Add translations to existing attributes that lack them."""
    from app.services.lease_extractor import _translate_non_english_clauses
    from anthropic import AsyncAnthropic
    from app.mock_db import db

    client = AsyncAnthropic()
    backfilled = 0
    total_attrs_needing_translation = 0

    for lease_id, attrs in db.get("attributes", {}).items():
        # Ensure all attrs have translation field
        for attr in attrs:
            if "translation" not in attr:
                attr["translation"] = None

        needs_translation = [a for a in attrs if a.get("source_clause") and not a.get("translation")]
        if not needs_translation:
            continue

        total_attrs_needing_translation += len(needs_translation)
        print(f"  Translating {len(needs_translation)} attributes for lease {lease_id[:8]}...")

        # Run translation
        attrs = await _translate_non_english_clauses(attrs, client)
        db["attributes"][lease_id] = attrs
        backfilled += 1

        # Persist to disk
        from app.routers.leases import _persist_extraction
        _persist_extraction(lease_id)

    if backfilled > 0:
        print(f"✓ Backfilled translations: {backfilled} lease(s), {total_attrs_needing_translation} attribute(s)")


@app.on_event("startup")
async def startup():
    seed_users()
    seed_users_pg()
    load_org_ids()
    seed_leases(count=40)
    seed_clauses()
    seed_wework_demo_leases()
    seed_cam()
    seed_disputes()
    seed_rent_schedules()
    seed_administration()
    seed_payments()
    migrate_landlord_names_to_entities()
    seed_compliance()
    seed_intelligence()
    seed_pre_leasing()
    reload_uploaded_leases()
    load_all_from_pg()
    _migrate_field_names()
    # Skip backfill for now - causes hang on startup
    # await _backfill_translations()
    print("LeaseArc mock data seeded: 4 users, 40 Indian leases, CAM, disputes, clauses, rent schedules, administration, payments, landlords, compliance, intelligence, pre-leasing")


app.include_router(auth.router,           prefix="/api/auth",          tags=["Auth"])
app.include_router(dashboard.router,      prefix="/api/dashboard",     tags=["Dashboard"])
app.include_router(leases.router,         prefix="/api/leases",        tags=["Leases"])
app.include_router(amendments.router,     prefix="/api",               tags=["Amendments"])
app.include_router(administration.router, prefix="/api",               tags=["Administration"])
app.include_router(negotiation.router,    prefix="/api",               tags=["Negotiation"])
app.include_router(cam.router,            prefix="/api/cam",           tags=["CAM"])
app.include_router(payments_router.router, prefix="/api",              tags=["Payments"])
app.include_router(compliance_router.router, prefix="/api",            tags=["Compliance"])
app.include_router(intelligence_router.router, prefix="/api",          tags=["Intelligence"])
app.include_router(pre_leasing_router.router, prefix="/api/pre-leasing", tags=["Pre-Leasing"])


@app.get("/")
def root():
    return {
        "message": "LeaseArc API v1.0.0",
        "docs": "/docs",
        "status": "running",
    }


@app.get("/health")
def health():
    from app.mock_db import db
    return {
        "status": "healthy",
        "data": {
            "leases": len(db["leases"]),
            "amendments": len(db["amendments"]),
            "cam_statements": len(db["cam_statements"]),
            "disputes": len(db["disputes"]),
            "communications": len(db["communications"]),
        },
    }


@app.post("/api/reset")
@app.get("/api/reset")
def reset_db():
    """Force-clear all in-memory data and re-seed with fresh Indian store data."""
    from app.mock_db import db
    for key in list(db.keys()):
        db[key] = {}
    db["pg_lease_ids"] = set()
    seed_users()
    load_org_ids()
    seed_leases(count=40)
    seed_clauses()
    seed_wework_demo_leases()
    seed_cam()
    seed_disputes()
    seed_rent_schedules()
    seed_administration()
    seed_payments()
    migrate_landlord_names_to_entities()
    seed_compliance()
    seed_intelligence()
    seed_pre_leasing()
    return {"status": "ok", "message": "Database reset and re-seeded with 40 Indian leases."}

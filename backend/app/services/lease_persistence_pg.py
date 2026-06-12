"""Postgres + S3 persistence for leases created via /upload and /upload-package (Phase 2).

Seeded/demo leases and pre-Phase-2 JSON-backed leases (backend/extractions/*.json) are
NEVER touched by this module — they keep being served from db["leases"]/db["attributes"]
exactly as before. Only lease_ids in db["pg_lease_ids"] route through here.

The in-memory db dict remains the runtime read path for everything (review UI, clauses,
locate-field, admin transformer, list_leases, …); this module just keeps Postgres + S3
in sync with it for new uploads, and reconstructs db on startup for those leases.
"""
import os
import uuid
from datetime import date, datetime

from sqlalchemy import delete, select, update

from app.db import SessionLocal
from app.db_models import Lease, LeaseAttribute, LeaseFile, LeaseTokenUsage
from app.mock_db import db
from app.services.s3_storage import download_pdf, upload_pdf


def _parse_date(s):
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s))
    except ValueError:
        return None


def _safe_uuid(s):
    if not s:
        return None
    try:
        return uuid.UUID(str(s))
    except (ValueError, AttributeError, TypeError):
        return None


_LEASE_FIELDS = [
    "store_name", "store_code", "landlord_name", "tenant_name", "status",
    "currency", "monthly_rent", "portfolio_id", "city", "country", "address",
    "pdf_type", "page_count", "has_unreviewed_fields", "dismissed_from_history",
]

_ATTR_FIELDS = [
    "category", "attribute_key", "attribute_name", "data_type",
    "extracted_value", "user_edited_value", "confidence_score",
    "confidence_level", "is_verified", "page_number", "source_clause",
    "translation", "bbox", "bbox_rects", "is_key_field",
    "schema_path", "source_type", "source_file_name", "s3_file_path",
]

# Non-schema extractor fields, bundled into lease_attributes.extra.
_ATTR_EXTRA_FIELDS = ["confidence_reason", "source_document_id", "group", "slot_index", "sub_field", "role"]


def _apply_lease_fields(row: Lease, lease: dict) -> None:
    for f in _LEASE_FIELDS:
        if f in lease:
            setattr(row, f, lease[f])
    row.commencement_date = _parse_date(lease.get("commencement_date"))
    row.expiry_date = _parse_date(lease.get("expiry_date"))


def _attr_row(lease_id: str, org_id: uuid.UUID, a: dict) -> LeaseAttribute:
    extra = {k: a.get(k) for k in _ATTR_EXTRA_FIELDS if a.get(k) is not None}
    verified_by_raw = a.get("verified_by")
    verified_by_uuid = _safe_uuid(verified_by_raw)
    if verified_by_raw and not verified_by_uuid:
        # in-memory "verified_by" is a display name, not a user UUID — round-trip it via extra.
        extra["verified_by_name"] = verified_by_raw

    return LeaseAttribute(
        id=uuid.UUID(a["attribute_id"]),
        lease_id=uuid.UUID(lease_id),
        org_id=org_id,
        verified_by=verified_by_uuid,
        verified_at=_parse_dt(a.get("verified_at")),
        extra=extra or None,
        **{f: a.get(f) for f in _ATTR_FIELDS},
    )


def _usage_row(lease_id: str, org_id: uuid.UUID, usage: dict, file_name=None, pdf_type=None) -> LeaseTokenUsage:
    extraction = usage.get("extraction", {})
    return LeaseTokenUsage(
        lease_id=uuid.UUID(lease_id),
        org_id=org_id,
        file_name=file_name,
        pdf_type=pdf_type,
        model=extraction.get("model"),
        input_tokens=extraction.get("input_tokens"),
        output_tokens=extraction.get("output_tokens"),
        cost_usd=extraction.get("cost_usd"),
        locate_calls=usage.get("locate_calls"),
        total_cost_usd=usage.get("total_cost_usd"),
    )


def persist_uploaded_files(lease_id: str, org_id: str, created_by: str, lease_record: dict,
                            files: list[dict]) -> None:
    """Phase A — called synchronously at upload time, before extraction starts. `files` items:
    {documentId, fileName, bytes, isPrimary, docType, pdfType=None, pageCount=None}. Uploads PDF
    bytes to S3 and inserts the `leases` row (extraction_status='extracting') + one `lease_files`
    row per file. Raises on failure — callers must fail the upload request rather than start
    extraction without durable storage."""
    lease_uuid = uuid.UUID(lease_id)
    org_uuid = uuid.UUID(org_id)
    user_uuid = uuid.UUID(created_by)

    session = SessionLocal()
    try:
        lease_row = Lease(id=lease_uuid, org_id=org_uuid, created_by=user_uuid, assigned_to=user_uuid,
                           extraction_status="extracting")
        _apply_lease_fields(lease_row, lease_record)
        session.add(lease_row)

        for f in files:
            key = upload_pdf(lease_id, f["fileName"], f["bytes"])
            session.add(LeaseFile(
                id=uuid.UUID(f["documentId"]), lease_id=lease_uuid, org_id=org_uuid,
                file_name=f["fileName"], file_path=key, pdf_type=f.get("pdfType"),
                page_count=f.get("pageCount"), is_primary=bool(f.get("isPrimary")),
                doc_type=f.get("docType"), uploaded_by=user_uuid,
            ))

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def finalize_lease_persistence(lease_id: str, org_id: str, lease_record: dict, attrs: list,
                                usage: dict, file_updates: list[dict]) -> None:
    """Phase B — STEP 7, called after extraction completes successfully. Updates the `leases` row
    created by persist_uploaded_files() with enriched fields + extraction_status='review_pending',
    inserts lease_attributes + lease_token_usage, and updates each lease_files row (doc_type/
    pdf_type/page_count/is_primary) now that classification is known. `file_updates` items:
    {documentId (Phase-A id), fileName, isPrimary, docType, pdfType, pageCount}. No file bytes —
    S3 upload already happened in Phase A."""
    lease_uuid = uuid.UUID(lease_id)
    org_uuid = uuid.UUID(org_id)

    session = SessionLocal()
    try:
        lease_row = session.get(Lease, lease_uuid)
        _apply_lease_fields(lease_row, lease_record)
        lease_row.extraction_status = "review_pending"
        lease_row.extraction_error = None
        lease_row.updated_at = datetime.utcnow()

        for a in attrs:
            session.add(_attr_row(lease_id, org_uuid, a))

        session.add(_usage_row(lease_id, org_uuid, usage))

        existing_files = session.scalars(
            select(LeaseFile).where(LeaseFile.lease_id == lease_uuid)
        ).all()
        for fu in file_updates:
            # Phase A stores the browser-supplied fileName (folder uploads include a relative
            # path, e.g. "Test/Foo.pdf"); the xts package engine reports just the basename
            # ("Foo.pdf"). Match on basename so folder uploads' lease_files rows get updated.
            fu_base = os.path.basename(fu["fileName"])
            row = next((r for r in existing_files if os.path.basename(r.file_name) == fu_base), None)
            if row is None:
                continue
            row.doc_type = fu.get("docType")
            row.pdf_type = fu.get("pdfType")
            row.page_count = fu.get("pageCount")
            row.is_primary = bool(fu.get("isPrimary"))
            if row.is_primary and row.id != lease_uuid:
                # Preserve the invariant that the primary doc's lease_files.id == leases.id
                # (relied on by load_all_from_pg() / GET /document/{lease_id}).
                old_id = row.id
                session.flush()
                session.execute(update(LeaseFile).where(LeaseFile.id == old_id).values(id=lease_uuid))

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def mark_lease_failed(lease_id: str, error_message: str) -> None:
    """Best-effort: mark a lease as extraction-failed so it stays visible (in upload history,
    with its already-uploaded files intact) instead of silently disappearing."""
    lease_uuid = uuid.UUID(lease_id)
    session = SessionLocal()
    try:
        row = session.get(Lease, lease_uuid)
        if row is None:
            return
        row.extraction_status = "failed"
        row.extraction_error = (error_message or "")[:2000]
        row.updated_at = datetime.utcnow()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def sync_lease_attributes(lease_id: str) -> None:
    """Re-sync leases + lease_attributes from db["leases"]/db["attributes"] (e.g. after
    /save, /locate-field, /reenrich, /clear-highlights, or an attribute edit)."""
    lease_uuid = uuid.UUID(lease_id)
    session = SessionLocal()
    try:
        row = session.get(Lease, lease_uuid)
        if row is None:
            return
        _apply_lease_fields(row, db["leases"].get(lease_id, {}))
        row.updated_at = datetime.utcnow()

        session.execute(delete(LeaseAttribute).where(LeaseAttribute.lease_id == lease_uuid))
        for a in db["attributes"].get(lease_id, []):
            session.add(_attr_row(lease_id, row.org_id, a))

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def sync_token_usage(lease_id: str) -> None:
    """Re-sync locate_calls/total_cost_usd from db["token_usage"] (after /locate-field)."""
    usage = db["token_usage"].get(lease_id)
    if not usage:
        return
    lease_uuid = uuid.UUID(lease_id)
    session = SessionLocal()
    try:
        row = session.scalar(select(LeaseTokenUsage).where(LeaseTokenUsage.lease_id == lease_uuid))
        if row is None:
            return
        row.locate_calls = usage.get("locate_calls")
        row.total_cost_usd = usage.get("total_cost_usd")
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def load_all_from_pg() -> None:
    """Startup reload — reconstructs db[...] for every Postgres-backed lease, mirroring what
    reload_uploaded_leases() does for JSON-backed leases (downloads PDFs from S3)."""
    from app.routers.leases import _enrich_lease_record, _populate_clauses_from_attrs

    session = SessionLocal()
    try:
        leases = session.scalars(select(Lease)).all()
        for lr in leases:
            lease_id = str(lr.id)
            is_complete = lr.extraction_status == "review_pending"

            lease_dict = {
                "lease_id": lease_id,
                "org_id": str(lr.org_id),
                "created_by": str(lr.created_by),
                "assigned_to": str(lr.assigned_to) if lr.assigned_to else None,
                "store_name": lr.store_name,
                "store_code": lr.store_code,
                "landlord_name": lr.landlord_name,
                "tenant_name": lr.tenant_name,
                "status": lr.status,
                "currency": lr.currency,
                "commencement_date": lr.commencement_date.isoformat() if lr.commencement_date else None,
                "expiry_date": lr.expiry_date.isoformat() if lr.expiry_date else None,
                "monthly_rent": float(lr.monthly_rent) if lr.monthly_rent is not None else 0,
                "portfolio_id": lr.portfolio_id,
                "city": lr.city,
                "country": lr.country,
                "address": lr.address,
                "pdf_type": lr.pdf_type,
                "page_count": lr.page_count or 0,
                "has_unreviewed_fields": lr.has_unreviewed_fields,
                "dismissed_from_history": lr.dismissed_from_history,
                "days_to_expiry": (lr.expiry_date - date.today()).days if lr.expiry_date else 9999,
            }
            db["leases"][lease_id] = lease_dict

            # Attributes (only inserted in Phase B, once extraction completes)
            attrs = []
            if is_complete:
                attr_rows = session.scalars(
                    select(LeaseAttribute).where(LeaseAttribute.lease_id == lr.id)
                ).all()
                for ar in attr_rows:
                    extra = ar.extra or {}
                    verified_by = extra.get("verified_by_name") or (str(ar.verified_by) if ar.verified_by else None)
                    attrs.append({
                        "attribute_id": str(ar.id),
                        "lease_id": lease_id,
                        "category": ar.category,
                        "attribute_key": ar.attribute_key,
                        "attribute_name": ar.attribute_name,
                        "data_type": ar.data_type,
                        "extracted_value": ar.extracted_value,
                        "user_edited_value": ar.user_edited_value,
                        "confidence_score": ar.confidence_score,
                        "confidence_level": ar.confidence_level,
                        "confidence_reason": extra.get("confidence_reason"),
                        "is_verified": ar.is_verified,
                        "verified_by": verified_by,
                        "verified_at": ar.verified_at.isoformat() if ar.verified_at else None,
                        "page_number": ar.page_number,
                        "source_clause": ar.source_clause,
                        "translation": ar.translation,
                        "bbox": ar.bbox,
                        "bbox_rects": ar.bbox_rects,
                        "is_key_field": ar.is_key_field,
                        "source_document_id": extra.get("source_document_id"),
                        "group": extra.get("group"),
                        "slot_index": extra.get("slot_index"),
                        "sub_field": extra.get("sub_field"),
                        "role": extra.get("role"),
                        "schema_path": ar.schema_path,
                        "source_type": ar.source_type,
                        "source_file_name": ar.source_file_name,
                        "s3_file_path": ar.s3_file_path,
                    })
            db["attributes"][lease_id] = attrs

            if is_complete:
                # Re-derive fields not stored on `leases` (lease_type, refreshed days_to_expiry)
                _enrich_lease_record(lease_id, attrs)
                _populate_clauses_from_attrs(lease_id, attrs)

                bboxes = {a["attribute_key"]: a["bbox"] for a in attrs if a.get("bbox") is not None}
                if bboxes:
                    db["field_bboxes"][lease_id] = bboxes

                # Token usage
                usage_row = session.scalar(
                    select(LeaseTokenUsage).where(LeaseTokenUsage.lease_id == lr.id)
                )
                if usage_row:
                    db["token_usage"][lease_id] = {
                        "lease_id": lease_id,
                        "extraction": {
                            "model": usage_row.model,
                            "input_tokens": usage_row.input_tokens,
                            "output_tokens": usage_row.output_tokens,
                            "cost_usd": float(usage_row.cost_usd) if usage_row.cost_usd is not None else None,
                        },
                        "locate_calls": usage_row.locate_calls or [],
                        "total_cost_usd": float(usage_row.total_cost_usd) if usage_row.total_cost_usd is not None else 0,
                    }

            # Files / documents (downloads PDFs from S3) — reconstructed regardless of
            # extraction_status, since Phase A persists files before extraction starts.
            file_rows = session.scalars(
                select(LeaseFile).where(LeaseFile.lease_id == lr.id)
            ).all()
            primary_file_name = f"{lease_id}.pdf"
            manifest = []
            for fr in file_rows:
                doc_id = str(fr.id)
                db["lease_documents"][doc_id] = download_pdf(fr.file_path)
                manifest.append({
                    "documentId": doc_id,
                    "fileName": fr.file_name,
                    "docType": fr.doc_type,
                    "pdfType": fr.pdf_type,
                    "isPrimary": fr.is_primary,
                })
                if fr.is_primary:
                    primary_file_name = fr.file_name
            if len(manifest) > 1:
                db["lease_doc_sets"][lease_id] = manifest

            # Upload history entry
            if not lease_dict.get("dismissed_from_history"):
                if is_complete:
                    db["upload_jobs"][lease_id] = {
                        "jobId": lease_id,
                        "fileName": primary_file_name,
                        "fileSizeMb": 0.0,
                        "pageCount": lr.page_count or 0,
                        "status": "review_pending",
                        "progressPercent": 100,
                        "leaseId": lease_id,
                        "errorMessage": None,
                    }
                else:
                    if lr.extraction_status == "extracting":
                        # Orphaned by a server restart mid-extraction — mark failed so it
                        # doesn't loop as "extracting" forever.
                        error_message = "Extraction did not complete (server restarted) — please re-upload."
                        mark_lease_failed(lease_id, error_message)
                    else:
                        error_message = lr.extraction_error or "Extraction failed."
                    db["upload_jobs"][lease_id] = {
                        "jobId": lease_id,
                        "fileName": primary_file_name,
                        "fileSizeMb": 0.0,
                        "pageCount": lr.page_count or 0,
                        "status": "failed",
                        "progressPercent": 100,
                        "leaseId": lease_id,
                        "errorMessage": error_message,
                    }

            db["pg_lease_ids"].add(lease_id)

            if is_complete:
                try:
                    from app.services.administration_transformer import populate_administration_from_extraction
                    populate_administration_from_extraction(
                        lease_id=lease_id, attrs=attrs, lease_record=db["leases"][lease_id],
                        file_name=primary_file_name,
                    )
                except Exception as adm_exc:
                    print(f"Warning: admin transformer error for {lease_id}: {adm_exc}")

        if leases:
            print(f"Reloaded {len(leases)} Postgres-backed lease(s) from Postgres + S3")
    finally:
        session.close()

"""
Unit tests for the NEW backend extraction flow (lease_abstraction_xts engine).

The xts engine itself needs GEMINI_API_KEY + OPENAI_API_KEY to run, so these tests MOCK the
extraction functions (extract_from_pdf / extract_package_from_pdfs) — they verify the wiring we
own: the row→attribute adapter, the upload job lifecycle, persistence, the document manifest /
multi-PDF switcher, and provenance — with NO LLM calls and no keys required.

Run from the backend/ dir:   venv/bin/python -m pytest tests/test_new_upload_flow.py -v
"""
from __future__ import annotations

import os
import glob
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.mock_db import db
from app.routers import leases
import app.services.lease_extractor_xts as xts_adapter

_EXTRACTIONS = os.path.join(os.path.dirname(__file__), "..", "extractions")


# ──────────────────────────────────────────────────────────────────────────────
# Pure adapter unit tests (no app, no keys) — the real new mapping logic
# ──────────────────────────────────────────────────────────────────────────────

def _row(name, value="X", conf=90, ctx="some context", page=2):
    return {"attribute_name": name, "value": value, "confidence_score": conf,
            "context": ctx, "page_number": page}


def test_adapt_unique_field_uses_legacy_key_and_category():
    a = xts_adapter._adapt_row(_row("Lease_catalyst.Lease_Abstraction.Landlord Name", "Acme Corp"), "L1")
    assert a["attribute_key"] == "landlord_name"          # legacy alias preserved
    assert a["attribute_name"] == "Landlord Name"
    assert a["extracted_value"] == "Acme Corp"
    assert a["category"] == "Core Lease Terms"
    assert a["confidence_level"] == "high"
    assert a["is_key_field"] is True


def test_adapt_repeatable_slot_parses_group_and_index():
    a = xts_adapter._adapt_row(_row("Lease_catalyst.Lease_Abstraction.Area.0.Gross area", "1200"), "L1")
    assert a["attribute_name"] == "Area 1 — Gross area"   # slot 0 → "1" for humans
    assert a["attribute_key"] == "area_0_gross_area"
    assert a["category"] == "Areas"


def test_adapt_contact_slot_category():
    a = xts_adapter._adapt_row(_row("Lease_catalyst.Lease_Abstraction.Contact Identification.1.Email", "x@y.com"), "L1")
    assert a["category"] == "Contacts"
    assert a["attribute_name"] == "Contact Identification 2 — Email"


def test_adapt_not_found_sentinel_becomes_null_value():
    a = xts_adapter._adapt_row(_row("Lease_catalyst.Lease_Abstraction.Property name", "Not found", conf=80), "L1")
    assert a["extracted_value"] is None
    assert a["confidence_score"] == 0
    assert a["confidence_level"] == "low"


def test_adapt_context_drives_source_clause_and_page():
    a = xts_adapter._adapt_row(_row("Lease_catalyst.Lease_Abstraction.City", "Mumbai", ctx="located in Mumbai", page=5), "L1")
    assert a["source_clause"] == "located in Mumbai"      # verbatim quote → highlighting
    assert a["page_number"] == 5
    assert a["bbox"] is None                              # bbox resolved later


def test_confidence_levels():
    assert xts_adapter._confidence_level(85) == "high"
    assert xts_adapter._confidence_level(60) == "medium"
    assert xts_adapter._confidence_level(20) == "low"


def test_category_for_clause_and_dates():
    assert xts_adapter._category_for("Current Commencement Date") == "Critical Dates"
    assert xts_adapter._category_for("Holdover") == "Restrictive Clauses"
    assert xts_adapter._category_for("Base Rent Amount") == "Financial Obligations"


def test_tag_source_doc_index_newest_wins():
    # base has the value on doc 0; amendment overrides on doc 1 → provenance should be newest (1)
    base = [_row("Lease_catalyst.Lease_Abstraction.Landlord Name", "Old LL")]
    amd  = [_row("Lease_catalyst.Lease_Abstraction.Landlord Name", "New LL")]
    merged = [_row("Lease_catalyst.Lease_Abstraction.Landlord Name", "New LL")]
    xts_adapter._tag_source_doc_index(merged, [base, amd])
    assert merged[0]["_source_doc_index"] == 1

    # value only present on the base doc → provenance points back to doc 0
    merged2 = [_row("Lease_catalyst.Lease_Abstraction.Tenant Name", "Tenant A")]
    base2 = [_row("Lease_catalyst.Lease_Abstraction.Tenant Name", "Tenant A")]
    amd2  = [_row("Lease_catalyst.Lease_Abstraction.Tenant Name", "Not found")]
    xts_adapter._tag_source_doc_index(merged2, [base2, amd2])
    assert merged2[0]["_source_doc_index"] == 0


# ──────────────────────────────────────────────────────────────────────────────
# Endpoint tests with a mocked engine (FastAPI TestClient) — job lifecycle + wiring
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(leases.router, prefix="/leases")
    return TestClient(app)


@pytest.fixture
def cleanup_extractions():
    """Remove any extraction files created during a test."""
    created: list[str] = []
    yield created
    for lid in created:
        for path in glob.glob(os.path.join(_EXTRACTIONS, f"{lid}*")):
            try:
                os.remove(path)
            except OSError:
                pass


def _attr(key, name, value, category="Core Lease Terms", **extra):
    base = {
        "attribute_id": str(uuid.uuid4()), "lease_id": "x", "category": category,
        "attribute_key": key, "attribute_name": name, "data_type": "text",
        "extracted_value": value, "user_edited_value": None,
        "confidence_score": 90 if value else 0, "confidence_level": "high" if value else "low",
        "confidence_reason": None, "is_verified": False, "verified_by": None, "verified_at": None,
        "page_number": 1, "source_clause": "ctx", "translation": None,
        "bbox": None, "bbox_rects": None, "is_key_field": False,
    }
    base.update(extra)
    return base


_USAGE = {"model": "mock", "input_tokens": 100, "output_tokens": 50, "cost_usd": 0.01}


def test_single_upload_lifecycle(client, cleanup_extractions, monkeypatch):
    attrs = [
        _attr("landlord_name", "Landlord Name", "Acme Corp", is_key_field=True),
        _attr("tenant_name", "Tenant Name", "RetailCo", is_key_field=True),
        _attr("property_name", "Property name", None),   # not found
    ]
    monkeypatch.setattr(xts_adapter, "extract_from_pdf",
                        AsyncMock(return_value=(attrs, _USAGE, "typed")))

    resp = client.post("/leases/upload",
                       files={"file": ("lease.pdf", b"%PDF-1.4 lifecycle " + uuid.uuid4().hex.encode(), "application/pdf")})
    assert resp.status_code == 200
    job_id = resp.json()["jobId"]
    assert resp.json()["status"] == "extracting"

    # Background task runs synchronously under TestClient → job should be done now.
    status = client.get(f"/leases/status/{job_id}").json()
    assert status["status"] == "review_pending"
    lease_id = status["leaseId"]
    cleanup_extractions.append(lease_id)

    # Attributes are exposed to the review UI
    extracted = client.get(f"/leases/{lease_id}/extracted").json()
    assert extracted["totalAttributes"] == 3
    assert extracted["notFoundCount"] == 1
    keys = {a["attribute_key"] for a in extracted["attributes"]}
    assert {"landlord_name", "tenant_name", "property_name"} <= keys

    # Lease record enriched from key fields
    assert db["leases"][lease_id]["landlord_name"] == "Acme Corp"
    assert db["leases"][lease_id]["tenant_name"] == "RetailCo"

    # Persisted to disk for reload-on-restart
    assert os.path.exists(os.path.join(_EXTRACTIONS, f"{lease_id}.json"))

    # Single upload → /documents returns one primary entry (no switcher)
    docs = client.get(f"/leases/{lease_id}/documents").json()["documents"]
    assert len(docs) == 1 and docs[0]["isPrimary"] is True


def test_single_upload_failure_marks_job_failed(client, monkeypatch):
    monkeypatch.setattr(xts_adapter, "extract_from_pdf",
                        AsyncMock(side_effect=RuntimeError("boom 429")))
    resp = client.post("/leases/upload",
                       files={"file": ("bad.pdf", b"%PDF-1.4 failcase " + uuid.uuid4().hex.encode(), "application/pdf")})
    job_id = resp.json()["jobId"]
    status = client.get(f"/leases/status/{job_id}").json()
    assert status["status"] == "failed"
    assert status["leaseId"] is None
    assert "boom 429" in (status["errorMessage"] or "")


def test_folder_upload_merges_and_builds_manifest(client, cleanup_extractions, monkeypatch):
    # Two source docs: base (index 0) and amendment (index 1, primary/newest).
    attrs = [
        _attr("landlord_name", "Landlord Name", "New LL", source_doc_index=1, source_document_id=None),
        _attr("tenant_name", "Tenant Name", "Tenant A", source_doc_index=0, source_document_id=None),
    ]
    doc_set = [
        {"fileName": "base_lease.pdf", "docType": "Base", "pdfType": "typed",
         "bytes": b"%PDF base", "isPrimary": False},
        {"fileName": "amendment_1.pdf", "docType": "Amendment", "pdfType": "typed",
         "bytes": b"%PDF amd", "isPrimary": True},
    ]
    monkeypatch.setattr(xts_adapter, "extract_package_from_pdfs",
                        AsyncMock(return_value=(attrs, _USAGE, "typed", doc_set)))

    resp = client.post("/leases/upload-package", files=[
        ("files", ("base_lease.pdf", b"%PDF base", "application/pdf")),
        ("files", ("amendment_1.pdf", b"%PDF amd", "application/pdf")),
    ])
    assert resp.status_code == 200
    assert resp.json()["fileCount"] == 2
    job_id = resp.json()["jobId"]

    status = client.get(f"/leases/status/{job_id}").json()
    assert status["status"] == "review_pending"
    lease_id = status["leaseId"]
    cleanup_extractions.append(lease_id)

    # Manifest: two docs, amendment is primary (and keyed by lease_id)
    docs = client.get(f"/leases/{lease_id}/documents").json()["documents"]
    assert len(docs) == 2
    primary = [d for d in docs if d["isPrimary"]]
    assert len(primary) == 1
    assert primary[0]["documentId"] == lease_id
    assert primary[0]["docType"] == "Amendment"

    # Provenance resolved: source_doc_index → a real documentId on each attribute
    extracted = client.get(f"/leases/{lease_id}/extracted").json()["attributes"]
    by_key = {a["attribute_key"]: a for a in extracted}
    doc_ids = {d["documentId"] for d in docs}
    assert by_key["landlord_name"]["source_document_id"] in doc_ids
    assert by_key["tenant_name"]["source_document_id"] in doc_ids
    # tenant came from the base doc (index 0) → not the primary lease_id doc
    assert by_key["tenant_name"]["source_document_id"] != lease_id

    # Every document is independently viewable
    for d in docs:
        assert client.get(f"/leases/document/{d['documentId']}").status_code == 200


def test_folder_upload_rejects_empty(client):
    resp = client.post("/leases/upload-package", files=[
        ("files", ("notes.txt", b"hello", "text/plain")),
    ])
    assert resp.status_code == 400


def test_bulk_upload_creates_batch(client):
    resp = client.post("/leases/bulk-upload", files=[
        ("files", ("a.pdf", b"%PDF a", "application/pdf")),
        ("files", ("b.pdf", b"%PDF b", "application/pdf")),
    ])
    assert resp.status_code == 200
    batch_id = resp.json()["batchId"]
    assert len(resp.json()["files"]) == 2

    status = client.get(f"/leases/bulk-status/{batch_id}").json()
    assert len(status["files"]) == 2

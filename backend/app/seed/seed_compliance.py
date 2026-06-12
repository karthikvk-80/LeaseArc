from app.mock_db import db
import uuid
from datetime import datetime, timedelta


def seed_compliance():
    if db.get("compliance_settings"):
        return

    xtract_org_id = db.get("org_ids", {}).get("Xtract.io", "org1")

    # Org compliance settings
    db["compliance_settings"][xtract_org_id] = {
        "orgId": xtract_org_id,
        "standard": "Ind AS 116",
        "reportingCurrency": "INR",
        "jurisdiction": "IN",
        "updatedAt": datetime.utcnow().isoformat(),
        "updatedBy": "user-001",
    }

    # IBR records — cover main jurisdictions
    for ibr in [
        {"jurisdiction": "US",  "rate": 4.2,  "effectiveDate": "2025-01-01", "addedBy": "Priya Mehta",  "addedAt": "2025-01-10T09:00:00"},
        {"jurisdiction": "GB",  "rate": 5.1,  "effectiveDate": "2025-01-01", "addedBy": "Priya Mehta",  "addedAt": "2025-01-10T09:15:00"},
        {"jurisdiction": "AU",  "rate": 5.5,  "effectiveDate": "2025-01-01", "addedBy": "Sarah Chen",   "addedAt": "2025-01-15T10:00:00"},
        {"jurisdiction": "IN",  "rate": 8.5,  "effectiveDate": "2025-01-01", "addedBy": "Sarah Chen",   "addedAt": "2025-01-15T10:05:00"},
        {"jurisdiction": "SG",  "rate": 3.8,  "effectiveDate": "2025-01-01", "addedBy": "Sarah Chen",   "addedAt": "2025-01-15T10:10:00"},
        {"jurisdiction": "AE",  "rate": 4.0,  "effectiveDate": "2025-01-01", "addedBy": "Sarah Chen",   "addedAt": "2025-01-15T10:15:00"},
        {"jurisdiction": "CA",  "rate": 5.0,  "effectiveDate": "2025-03-01", "addedBy": "Priya Mehta",  "addedAt": "2025-03-01T08:00:00"},
        {"jurisdiction": "DE",  "rate": 3.9,  "effectiveDate": "2025-03-01", "addedBy": "Priya Mehta",  "addedAt": "2025-03-01T08:05:00"},
    ]:
        ibr_id = str(uuid.uuid4())
        db["ibrs"][ibr_id] = {"ibrId": ibr_id, "orgId": xtract_org_id, **ibr}

    # Compliance periods (Jan–May 2026)
    for period, locked in [
        ("2026-01", True),
        ("2026-02", True),
        ("2026-03", True),
        ("2026-04", False),
        ("2026-05", False),
    ]:
        db["compliance_periods"][period] = {
            "period": period,
            "isLocked": locked,
            "lockedBy": "Sarah Chen" if locked else None,
            "lockedAt": datetime(2026, int(period[5:7]), 28).isoformat() if locked else None,
            "status": "locked" if locked else "open",
        }

    # GL account mapping for Xtract.io
    db["compliance_gl_mapping"][xtract_org_id] = {
        "rou_asset": "1520",
        "accum_amort_rou": "1521",
        "lease_liability_current": "2310",
        "lease_liability_noncurrent": "2320",
        "interest_expense": "6120",
        "operating_lease_expense": "6110",
        "amortization_expense": "6130",
        "accounts_payable": "2010",
    }

    # Pre-seed journal entry statuses: Jan-Mar 2026 = posted, Apr = approved, May = pending
    # We'll seed a few known lease IDs from the STORE_CITIES list once leases are available.
    # The router's journal endpoint uses db["compliance_journal_status"] at query time,
    # so the seed below provides approved/posted entries for closed periods.
    # We iterate over leases to stamp closed-period statuses.
    leases = list(db.get("leases", {}).values())
    for lease in leases:
        lid = lease.get("lease_id")
        for period, status in [
            ("2026-01", "posted"),
            ("2026-02", "posted"),
            ("2026-03", "posted"),
            ("2026-04", "approved"),
        ]:
            key = f"{lid}_{period}"
            db["compliance_journal_status"][key] = {
                "entry_id": key,
                "status": status,
                "updated_by": "Sarah Chen",
                "updated_at": datetime(2026, int(period[5:7]), 28).isoformat(),
            }


def seed_all():
    seed_compliance()

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from app.mock_db import db
from app.services.task_emitter import emit_task
from app.services.org_isolation import current_org_id, require_same_org
import uuid
import random
from datetime import datetime

router = APIRouter()

FLAGGED_CLAUSES_POOL = [
    {
        "attributeKey": "cam_cap_pct",
        "attributeName": "CAM Cap (%)",
        "riskType": "Uncapped CAM Exposure",
        "severity": "high",
        "explanation": "The amendment removes the 5% CAM cap established in the original lease, exposing the tenant to unlimited operating cost increases with no ceiling.",
        "counterLanguage": "CAM charges shall not exceed five percent (5%) of the base rent in any lease year, calculated on a cumulative basis from the commencement date.",
        "marketBenchmark": "above_market",
    },
    {
        "attributeKey": "renewal_notice_period_days",
        "attributeName": "Renewal Notice Period (Days)",
        "riskType": "Shortened Renewal Notice Period",
        "severity": "medium",
        "explanation": "Renewal notice period reduced from 12 months to 6 months, significantly increasing the risk of the tenant inadvertently missing the renewal option window.",
        "counterLanguage": "Tenant shall provide written notice of its intention to renew no later than twelve (12) months prior to the expiry of the then-current term.",
        "marketBenchmark": "above_market",
    },
    {
        "attributeKey": "co_tenancy_clause",
        "attributeName": "Co-Tenancy Clause",
        "riskType": "Co-tenancy Clause Removed",
        "severity": "high",
        "explanation": "This amendment removes the co-tenancy protection clause entirely. If the anchor tenant vacates, the tenant has no right to reduce rent or terminate the lease.",
        "counterLanguage": "In the event the anchor tenant ceases to operate for a continuous period exceeding ninety (90) days, Tenant shall be entitled to pay percentage rent only until a replacement anchor tenant of equivalent calibre commences trading.",
        "marketBenchmark": "above_market",
    },
    {
        "attributeKey": "admin_fee_pct",
        "attributeName": "Admin Fee (%)",
        "riskType": "Increased Administrative Fee",
        "severity": "medium",
        "explanation": "Administrative fee increased from 12% to 18% of CAM costs, which is above the typical market range of 10–15% for comparable retail properties.",
        "counterLanguage": "Administrative and management fees payable by Tenant shall not exceed fifteen percent (15%) of total CAM costs in any reconciliation period.",
        "marketBenchmark": "above_market",
    },
    {
        "attributeKey": "exclusivity_clause",
        "attributeName": "Exclusivity Clause",
        "riskType": "Exclusivity Scope Narrowed",
        "severity": "low",
        "explanation": "The exclusivity scope has been narrowed in this amendment, potentially allowing competing tenants within the existing radius restriction.",
        "counterLanguage": "Landlord covenants not to lease any other premises within the Shopping Centre to any tenant whose primary business is the retail sale of [category] products.",
        "marketBenchmark": "at_market",
    },
]


@router.post("/leases/{lease_id}/amendments/upload")
async def upload_amendment(lease_id: str, file: UploadFile = File(...), org_id: str = Depends(current_org_id)):
    lease = db["leases"].get(lease_id)
    if not lease:
        raise HTTPException(status_code=404, detail="Lease not found")
    require_same_org(lease, org_id)

    existing = [a for a in db["amendments"].values() if a["lease_id"] == lease_id]
    version_number = len(existing) + 1

    amendment_id = str(uuid.uuid4())
    risk_score = random.randint(30, 85)
    risk_level = "high" if risk_score >= 70 else ("medium" if risk_score >= 40 else "low")

    db["amendments"][amendment_id] = {
        "amendment_id": amendment_id,
        "lease_id": lease_id,
        "org_id": org_id,
        "version_number": version_number,
        "execution_date": datetime.utcnow().date().isoformat(),
        "changed_attributes": ["cam_cap_pct", "admin_fee_pct", "renewal_notice_period_days"],
        "changed_count": 3,
        "pdf_url": f"/mock/amendments/{amendment_id}.pdf",
        "risk_score": risk_score,
        "risk_level": risk_level,
        "status": "active",
    }

    emit_task(
        title=f"Amendment v{version_number} uploaded for {lease.get('store_name', lease_id)[:40]} — review changed terms and update payment schedule.",
        source="amendments",
        action_type="amendment_term_review",
        assignee_role="lease_admin",
        priority="high" if risk_level == "high" else "medium",
        lease_id=lease_id,
        watched_by_role="re_director",
        due_days=5,
    )

    return {
        "jobId": str(uuid.uuid4()),
        "amendmentId": amendment_id,
        "versionNumber": version_number,
        "status": "review_pending",
    }


@router.get("/leases/{lease_id}/amendments")
def list_amendments(lease_id: str, org_id: str = Depends(current_org_id)):
    lease = db["leases"].get(lease_id)
    if not lease:
        raise HTTPException(status_code=404, detail="Lease not found")
    require_same_org(lease, org_id)

    amendments = [
        {
            "amendmentId": a["amendment_id"],
            "leaseId": a["lease_id"],
            "versionNumber": a["version_number"],
            "amendmentType": a.get("amendment_type"),
            "executionDate": a["execution_date"],
            "effectiveDate": a.get("effective_date"),
            "changedAttributes": a.get("changed_attributes", []),
            "changedCount": a.get("changed_count", len(a.get("changed_attributes", []))),
            "riskScore": a["risk_score"],
            "riskLevel": a["risk_level"],
            "status": a["status"],
            "plainEnglishSummary": a.get("plain_english_summary"),
            "beforeAfter": a.get("before_after"),
        }
        for a in db["amendments"].values()
        if a["lease_id"] == lease_id
    ]
    amendments.sort(key=lambda x: x["versionNumber"])
    return {"amendments": amendments}


@router.get("/leases/{lease_id}/amendments/{amendment_id}/diff")
def get_diff(lease_id: str, amendment_id: str, org_id: str = Depends(current_org_id)):
    lease = db["leases"].get(lease_id)
    if lease:
        require_same_org(lease, org_id)
    amendment = db["amendments"].get(amendment_id)
    if not amendment:
        raise HTTPException(status_code=404, detail="Amendment not found")

    attrs = db["attributes"].get(lease_id, [])
    changed_keys = amendment.get("changed_attributes", [])

    changed = []
    for attr in attrs:
        if attr["attribute_key"] in changed_keys:
            changed.append({
                "key": attr["attribute_key"],
                "attributeName": attr["attribute_name"],
                "previousValue": attr.get("extracted_value"),
                "newValue": f"{attr.get('extracted_value', '')} (amended)",
                "changedBy": "Landlord",
                "executionDate": amendment["execution_date"],
            })

    return {"changedAttributes": changed}


@router.get("/leases/{lease_id}/effective-terms")
def get_effective_terms(lease_id: str, org_id: str = Depends(current_org_id)):
    lease = db["leases"].get(lease_id)
    if lease:
        require_same_org(lease, org_id)
    attrs = db["attributes"].get(lease_id, [])
    if not attrs:
        raise HTTPException(status_code=404, detail="Lease attributes not found")

    amendments = [a for a in db["amendments"].values() if a["lease_id"] == lease_id]
    latest_amendment = sorted(amendments, key=lambda x: x["version_number"], reverse=True)[0] if amendments else None
    changed_keys = latest_amendment.get("changed_attributes", []) if latest_amendment else []

    terms = []
    for attr in attrs:
        version_source = "Original Lease"
        if latest_amendment and attr["attribute_key"] in changed_keys:
            version_source = f"Amendment {latest_amendment['version_number']}"
        terms.append({
            "key": attr["attribute_key"],
            "attributeName": attr["attribute_name"],
            "currentValue": attr.get("user_edited_value") or attr.get("extracted_value"),
            "introducedByVersion": version_source,
            "dataType": attr["data_type"],
            "category": attr["category"],
        })

    return {"attributes": terms}


@router.get("/leases/{lease_id}/audit-trail")
def get_audit_trail(lease_id: str, org_id: str = Depends(current_org_id)):
    lease = db["leases"].get(lease_id)
    if not lease:
        raise HTTPException(status_code=404, detail="Lease not found")
    require_same_org(lease, org_id)

    from datetime import timedelta
    import random as _random

    today = datetime.utcnow()
    events = [
        {"eventType": "upload", "userId": "user-002", "userName": "James Wilson", "timestamp": (today - timedelta(days=180)).isoformat(), "description": "Original lease uploaded and extraction initiated"},
        {"eventType": "review", "userId": "user-002", "userName": "James Wilson", "timestamp": (today - timedelta(days=179)).isoformat(), "description": "Extraction review completed — 87 attributes verified"},
        {"eventType": "save", "userId": "user-002", "userName": "James Wilson", "timestamp": (today - timedelta(days=179)).isoformat(), "description": "Lease saved to system of record"},
    ]

    amendments = sorted(
        [a for a in db["amendments"].values() if a["lease_id"] == lease_id],
        key=lambda x: x["version_number"]
    )
    for amend in amendments:
        events.append({
            "eventType": "amendment",
            "userId": "user-002",
            "userName": "James Wilson",
            "timestamp": amend["execution_date"] + "T10:00:00",
            "description": f"Amendment {amend['version_number']} uploaded — {amend.get('changed_count', 0)} attributes changed",
        })

    events.sort(key=lambda x: x["timestamp"], reverse=True)
    return {"events": events}


@router.get("/amendments/{amendment_id}/risk")
def get_amendment_risk(amendment_id: str, org_id: str = Depends(current_org_id)):
    amendment = db["amendments"].get(amendment_id)
    if not amendment:
        raise HTTPException(status_code=404, detail="Amendment not found")
    # Validate org via the parent lease
    lease = db["leases"].get(amendment.get("lease_id", ""), {})
    if lease:
        require_same_org(lease, org_id)

    risk_score = amendment["risk_score"]
    risk_level = amendment["risk_level"]

    n_flags = random.randint(2, 4)
    flagged = random.sample(FLAGGED_CLAUSES_POOL, min(n_flags, len(FLAGGED_CLAUSES_POOL)))

    if risk_level == "high":
        emit_task(
            title=f"High-risk amendment flagged (score {risk_score}/100) — review {n_flags} flagged clauses and advise on counter-language.",
            source="amendments",
            action_type="amendment_risk_review",
            assignee_role="legal",
            priority="high",
            lease_id=amendment.get("lease_id"),
            watched_by_role="re_director",
            due_days=7,
        )

    return {
        "riskScore": risk_score,
        "riskLevel": risk_level,
        "flaggedClauses": flagged,
    }

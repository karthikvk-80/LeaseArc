from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from app.mock_db import db
from app.services.org_isolation import current_org_id, scoped, require_same_org
from typing import Optional
import uuid
from datetime import datetime, date

router = APIRouter()


class DraftLetterRequest(BaseModel):
    tone: str = "formal_first_notice"


class LetterUpdateRequest(BaseModel):
    content: str


class StatusUpdateRequest(BaseModel):
    status: str
    resolvedAmount: Optional[float] = None
    notes: Optional[str] = None


TONE_LABELS = {
    "formal_first_notice": "Formal First Notice",
    "follow_up_reminder": "Follow-Up Reminder",
    "escalation_notice": "Escalation Notice",
}

TONE_OPENINGS = {
    "formal_first_notice": "We are writing to formally dispute certain charges",
    "follow_up_reminder": "Further to our previous correspondence dated [DATE], we write to follow up on the outstanding dispute",
    "escalation_notice": "Despite our previous notices, this matter remains unresolved. We are escalating this dispute and reserve all rights",
}


@router.get("/negotiations")
def list_negotiations(
    status: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20),
    org_id: str = Depends(current_org_id),
):
    disputes = scoped(db["disputes"].values(), org_id)

    if status:
        disputes = [d for d in disputes if d["status"] == status]
    if type:
        disputes = [d for d in disputes if d["dispute_type"] == type]
    if search:
        s = search.lower()
        disputes = [d for d in disputes if s in d.get("location_name", "").lower()]

    disputes.sort(key=lambda x: x.get("last_activity", ""), reverse=True)
    total = len(disputes)
    start = (page - 1) * pageSize

    result = []
    for d in disputes[start: start + pageSize]:
        result.append({
            "disputeId": d["dispute_id"],
            "leaseId": d["lease_id"],
            "locationName": d.get("location_name", "Unknown"),
            "type": d["dispute_type"],
            "status": d["status"],
            "claimedAmount": d["claimed_amount"],
            "recoveredAmount": d["recovered_amount"],
            "lastActivity": d["last_activity"],
        })

    return {"negotiations": result, "disputes": result, "total": total}


FLAGGED_CLAUSES = [
    {"clause_name": "HVAC Exclusion Clause", "risk": "high", "explanation": "HVAC operating costs are expressly excluded from CAM per Clause 12.3."},
    {"clause_name": "Capital Expenditure Cap", "risk": "high", "explanation": "Capital expenditure is excluded per Clause 15.2; only routine maintenance is recoverable."},
    {"clause_name": "Management Fee Cap", "risk": "medium", "explanation": "Management fee exceeds the 5% of gross rent cap defined in Clause 8.4."},
    {"clause_name": "Audit Rights Clause", "risk": "medium", "explanation": "Tenant retains audit rights under Clause 21 — landlord must provide supporting invoices within 30 days."},
    {"clause_name": "Gross-Up Provision", "risk": "low", "explanation": "Gross-up calculation methodology may not be compliant with Clause 11.2 for partially occupied buildings."},
]


@router.get("/disputes/{dispute_id}")
def get_dispute_detail(dispute_id: str, org_id: str = Depends(current_org_id)):
    dispute = db["disputes"].get(dispute_id)
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")
    require_same_org(dispute, org_id)
    lease = db["leases"].get(dispute["lease_id"], {})
    dtype = dispute["dispute_type"]
    title = f"CAM Dispute — {dispute.get('location_name', 'Store')}" if dtype == "cam_dispute" else f"Amendment Risk — {dispute.get('location_name', 'Store')}"
    comms = [c for c in db["communications"].values() if c.get("dispute_id") == dispute_id]
    comms.sort(key=lambda x: x.get("sent_at", ""))
    risk_score = 72 if dtype == "cam_dispute" else 45
    return {
        "dispute": {
            "disputeId": dispute_id,
            "leaseId": dispute["lease_id"],
            "locationName": dispute.get("location_name", "Unknown"),
            "title": title,
            "description": f"Dispute relating to charges in the {'CAM reconciliation statement' if dtype == 'cam_dispute' else 'lease amendment'} for {dispute.get('location_name', 'this property')}.",
            "type": dtype.replace("_dispute", "").replace("_risk", ""),
            "status": dispute["status"],
            "disputedAmount": dispute["claimed_amount"],
            "recoveredAmount": dispute["recovered_amount"],
            "raisedAt": dispute["created_at"],
            "landlordName": lease.get("landlord_name", "Property Management"),
        },
        "riskAnalysis": {
            "overallRiskScore": risk_score,
            "highRiskCount": 2,
            "mediumRiskCount": 2,
            "lowRiskCount": 1,
            "marketBenchmarks": ["CAM cap: 8% of base rent", "Avg recovery: 72%", "Market norm: exclude capex", "Audit rights: standard"],
            "flaggedClauses": FLAGGED_CLAUSES,
        },
        "communications": [
            {
                "comm_id": c["comm_id"],
                "direction": c["direction"],
                "type": c["type"],
                "subject": c["subject"],
                "body": c["body"],
                "sent_at": c["sent_at"],
            }
            for c in comms
        ],
    }


@router.post("/disputes/{dispute_id}/draft-letter")
def draft_letter(dispute_id: str, body: DraftLetterRequest, org_id: str = Depends(current_org_id)):
    dispute = db["disputes"].get(dispute_id)
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")
    require_same_org(dispute, org_id)

    lease = db["leases"].get(dispute["lease_id"], {})
    landlord = lease.get("landlord_name", "Property Management")
    store = lease.get("store_name", "the referenced property")
    today = date.today()
    claimed = dispute["claimed_amount"]
    opening = TONE_OPENINGS.get(body.tone, TONE_OPENINGS["formal_first_notice"])

    existing = db["dispute_letters"].get(dispute_id)
    if existing and body.tone == "formal_first_notice":
        return {
            "letterId": existing["letter_id"],
            "content": existing["content"],
            "tone": body.tone,
            "clausesCited": existing.get("clauses_cited", ["Clause 12.3", "Clause 15.2"]),
            "totalDisputed": claimed,
        }

    letter_id = str(uuid.uuid4())
    content = f"""{today.strftime('%B %d, %Y')}

{landlord}
Property Management Division

RE: {TONE_LABELS[body.tone]} — CAM Charge Dispute for {store}
Lease Reference: {dispute['lease_id'][:8].upper()}
Statement Period: 2024

Dear Property Management Team,

{opening} included in the Common Area Maintenance (CAM) reconciliation statement for the above-referenced property.

Following a thorough review of your statement against the executed terms of our lease agreement, we have identified the following discrepancies:

┌─────────────────────────────────┬──────────────┬──────────────┬──────────────┬──────────────────────────┐
│ Expense Category                │ Landlord Amt │ Allowable    │ Variance     │ Lease Reference          │
├─────────────────────────────────┼──────────────┼──────────────┼──────────────┼──────────────────────────┤
│ HVAC Operating Costs            │ $8,400.00    │ $0.00        │ $8,400.00    │ Clause 12.3              │
│ Capital Expenditure Allocation  │ $5,200.00    │ $0.00        │ $5,200.00    │ Clause 15.2              │
│ Management Fee Overage          │ ${claimed - 13600:,.2f}    │ $0.00        │ ${claimed - 13600:,.2f}    │ Clause 8.4               │
└─────────────────────────────────┴──────────────┴──────────────┴──────────────┴──────────────────────────┘

Total Amount Disputed: ${claimed:,.2f}

The above-listed charges are expressly disallowed under the clauses cited. We respectfully request:
1. A credit of ${claimed:,.2f} against future rent obligations; or
2. A direct refund within thirty (30) days of this notice.

{"We look forward to your prompt response." if body.tone != "escalation_notice" else "Please be advised that failure to respond within 14 days will result in escalation to legal proceedings."}

Sincerely,

Sarah Chen
RE Director, RetailCo Global
director@leasearc.com | +1 (312) 555-0100"""

    db["dispute_letters"][dispute_id] = {
        "letter_id": letter_id,
        "dispute_id": dispute_id,
        "org_id": dispute.get("org_id"),
        "tone": body.tone,
        "total_disputed": claimed,
        "clauses_cited": ["Clause 12.3", "Clause 15.2", "Clause 8.4"],
        "content": content,
    }

    from app.services.task_emitter import emit_task
    emit_task(
        title=f"Dispute letter drafted for {dispute.get('location_name', store)} (${claimed:,.0f}) — approve before sending to landlord.",
        source="negotiation",
        action_type="dispute_letter_approval",
        assignee_role="re_director",
        priority="high",
        lease_id=dispute.get("lease_id"),
        dispute_id=dispute_id,
        requires_approval=True,
        approval_requested_from_role="re_director",
        due_days=3,
    )

    return {
        "letterId": letter_id,
        "content": content,
        "tone": body.tone,
        "clausesCited": ["Clause 12.3", "Clause 15.2", "Clause 8.4"],
        "totalDisputed": claimed,
    }


@router.put("/disputes/{dispute_id}/draft-letter/{letter_id}")
def update_letter(dispute_id: str, letter_id: str, body: LetterUpdateRequest, org_id: str = Depends(current_org_id)):
    dispute = db["disputes"].get(dispute_id)
    if dispute:
        require_same_org(dispute, org_id)
    letter = db["dispute_letters"].get(dispute_id)
    if not letter:
        raise HTTPException(status_code=404, detail="Letter not found")
    letter["content"] = body.content
    return {"letterId": letter_id, "content": body.content}


@router.post("/disputes/{dispute_id}/send-letter")
def send_letter(dispute_id: str, org_id: str = Depends(current_org_id)):
    dispute = db["disputes"].get(dispute_id)
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")
    require_same_org(dispute, org_id)

    letter = db["dispute_letters"].get(dispute_id, {})
    comm_id = str(uuid.uuid4())

    db["communications"][comm_id] = {
        "comm_id": comm_id,
        "lease_id": dispute["lease_id"],
        "org_id": dispute.get("org_id"),
        "dispute_id": dispute_id,
        "type": "email",
        "direction": "outbound",
        "subject": f"CAM Dispute Letter — {dispute.get('location_name', 'Store')}",
        "body": letter.get("content", "Dispute letter"),
        "sent_by": "user-001",
        "sent_at": datetime.utcnow().isoformat(),
        "linked_clause": "Clause 12.3",
    }

    dispute["status"] = "responded" if dispute["status"] == "open" else dispute["status"]
    dispute["last_activity"] = datetime.utcnow().isoformat()

    return {"commId": comm_id, "disputeId": dispute_id, "sentAt": datetime.utcnow().isoformat()}


@router.get("/disputes/{dispute_id}/thread")
def get_dispute_thread(dispute_id: str, org_id: str = Depends(current_org_id)):
    dispute = db["disputes"].get(dispute_id)
    if dispute:
        require_same_org(dispute, org_id)
    comms = [c for c in db["communications"].values() if c.get("dispute_id") == dispute_id]
    comms.sort(key=lambda x: x.get("sent_at", ""), reverse=True)

    events = []
    for c in comms:
        user = db["users"].get(c.get("sent_by", "user-001"), {})
        events.append({
            "eventType": "communication",
            "content": c["body"],
            "sender": user.get("name", "RetailCo Team"),
            "role": user.get("role", "re_director"),
            "timestamp": c["sent_at"],
            "commId": c["comm_id"],
        })

    return {"events": events}


@router.patch("/disputes/{dispute_id}/status")
def update_dispute_status(dispute_id: str, body: StatusUpdateRequest, org_id: str = Depends(current_org_id)):
    dispute = db["disputes"].get(dispute_id)
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")
    require_same_org(dispute, org_id)

    dispute["status"] = body.status
    if body.resolvedAmount is not None:
        dispute["recovered_amount"] = body.resolvedAmount
    if body.notes:
        dispute["notes"] = body.notes
    if body.status == "resolved":
        dispute["resolved_at"] = datetime.utcnow().isoformat()
        from app.services.task_emitter import emit_task
        lease = db.get("leases", {}).get(dispute.get("lease_id", ""), {})
        location = lease.get("store_name", dispute.get("location_name", ""))
        resolved_amount = body.resolvedAmount or 0
        emit_task(
            title=f"Dispute resolved — ${resolved_amount:,.0f} recovered, {location}. Verify credit in ERP.",
            source="negotiation",
            action_type="dispute_resolution_verify",
            assignee_role="finance",
            priority="medium",
            lease_id=dispute.get("lease_id"),
            dispute_id=dispute_id,
        )
        for match in db.get("payment_matches", {}).values():
            if match.get("leaseId") == dispute.get("lease_id") and match.get("status") == "disputed":
                match["status"] = "on_time"
        for stmt in db.get("cam_statements", {}).values():
            if stmt.get("lease_id") == dispute.get("lease_id") and stmt.get("status") == "disputed":
                stmt["status"] = "resolved"

    dispute["last_activity"] = datetime.utcnow().isoformat()
    return dispute

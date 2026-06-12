from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from app.mock_db import db
from app.services.org_isolation import current_org_id, require_admin, scoped, require_same_org
from typing import Optional, List
import uuid
from datetime import date, datetime, timedelta

router = APIRouter()


class AlertUpdateRequest(BaseModel):
    alertThresholds: list
    notificationMethod: str = "in_app"


class CommunicationCreate(BaseModel):
    leaseId: str
    disputeId: Optional[str] = None
    type: str
    direction: str
    subject: str
    body: str
    sentBy: Optional[str] = "user-001"
    linkedClause: Optional[str] = None


@router.get("/critical-dates")
def get_critical_dates(
    portfolio_id: Optional[str] = Query(None),
    date_type: Optional[str] = Query(None),
    org_id: str = Depends(current_org_id),
):
    today = date.today()
    dates = []
    org_leases = {l["lease_id"]: l for l in scoped(db["leases"].values(), org_id)}

    for lease in org_leases.values():
        if portfolio_id and lease.get("portfolio_id") != portfolio_id:
            continue

        exp = lease.get("expiry_date")
        if exp:
            try:
                d = date.fromisoformat(exp[:10])
                days = (d - today).days
                if -30 <= days <= 365:
                    dates.append({
                        "dateId": f"exp-{lease['lease_id'][:8]}",
                        "leaseId": lease["lease_id"],
                        "locationName": lease.get("store_name", "Unknown"),
                        "dateType": "Lease Expiry",
                        "dueDate": exp,
                        "daysRemaining": days,
                        "alertThresholds": [180, 90, 60, 30],
                    })
            except Exception:
                pass

    # Add renewal deadlines / break clauses / rent reviews from attributes
    for lease_id, attrs in db["attributes"].items():
        if lease_id not in org_leases:
            continue
        lease = org_leases[lease_id]
        if portfolio_id and lease.get("portfolio_id") != portfolio_id:
            continue

        # Build a quick key→value map for this lease's attrs
        attr_kv: dict = {}
        for a in attrs:
            k = a.get("attribute_key")
            if k:
                attr_kv[k] = a.get("user_edited_value") or a.get("extracted_value")

        def _add_date(date_id: str, date_type: str, raw_val: str):
            """Try to parse raw_val as a date and append to dates if in window."""
            if not raw_val or str(raw_val).strip().lower() in ("n/a", "none", ""):
                return
            from datetime import datetime as _dt
            _FMTS = ("%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d", "%d %B %Y", "%d %b %Y")
            parsed = None
            for fmt in _FMTS:
                try:
                    parsed = _dt.strptime(raw_val.strip(), fmt).date()
                    break
                except ValueError:
                    pass
            if parsed is None and len(raw_val) >= 10:
                try:
                    parsed = date.fromisoformat(raw_val[:10])
                except Exception:
                    pass
            if parsed is None:
                return
            days = (parsed - today).days
            if -30 <= days <= 365:
                dates.append({
                    "dateId": date_id,
                    "leaseId": lease_id,
                    "locationName": lease.get("store_name", "Unknown"),
                    "dateType": date_type,
                    "dueDate": parsed.isoformat(),
                    "daysRemaining": days,
                    "alertThresholds": [180, 90, 60, 30],
                })

        # Legacy flat keys (seed data)
        for key, date_type in [
            ("renewal_deadline",  "Renewal Deadline"),
            ("break_clause_date", "Break Clause"),
            ("rent_review_date",  "Rent Review"),
        ]:
            if attr_kv.get(key):
                _add_date(f"{key[:3]}-{lease_id[:8]}", date_type, attr_kv[key])

        # Grouped Options keys  (xts extraction: options_N_option_type, etc.)
        for i in range(20):
            opt_type = attr_kv.get(f"options_{i}_option_type")
            if opt_type is None:
                break
            opt_type_lc = opt_type.lower()

            if opt_type_lc == "renewal":
                deadline = attr_kv.get(f"options_{i}_option_latest_notice_deadline")
                if deadline:
                    _add_date(
                        f"rnd-{lease_id[:8]}-{i}",
                        "Renewal Deadline",
                        deadline,
                    )

            if opt_type_lc == "termination":
                eff_date = attr_kv.get(f"options_{i}_option_effective_date")
                if eff_date:
                    _add_date(
                        f"brk-{lease_id[:8]}-{i}",
                        "Break Clause",
                        eff_date,
                    )

        # Grouped Expenses keys  — slot N>0 start_date = next rent escalation
        for i in range(1, 20):
            rent_type = attr_kv.get(f"expenses_{i}_rent_type")
            start_dt  = attr_kv.get(f"expenses_{i}_start_date")
            if rent_type is None and start_dt is None:
                break
            if start_dt:
                _add_date(
                    f"rev-{lease_id[:8]}-{i}",
                    "Rent Review",
                    start_dt,
                )

    if date_type:
        dates = [d for d in dates if d["dateType"] == date_type]

    dates.sort(key=lambda x: x["daysRemaining"])
    return {"dates": dates}


@router.patch("/critical-dates/{date_id}/alerts")
def update_date_alerts(date_id: str, body: AlertUpdateRequest, org_id: str = Depends(current_org_id)):
    return {"dateId": date_id, "alertThresholds": body.alertThresholds, "notificationMethod": body.notificationMethod}


@router.get("/rent-escalations")
def get_rent_escalations(portfolio_id: Optional[str] = Query(None), org_id: str = Depends(current_org_id)):
    today = date.today()
    escalations = []
    seen_ids: set = set()  # deduplicate (lease_id + slot) pairs
    org_lease_ids = {l["lease_id"] for l in scoped(db["leases"].values(), org_id)}

    for lease_id, attrs in db["attributes"].items():
        if lease_id not in org_lease_ids:
            continue
        lease = db["leases"].get(lease_id, {})
        if portfolio_id and lease.get("portfolio_id") != portfolio_id:
            continue

        attr_kv: dict = {}
        for a in attrs:
            k = a.get("attribute_key")
            if k:
                attr_kv[k] = a.get("user_edited_value") or a.get("extracted_value")

        # ── Legacy flat keys (seed data) ──────────────────────────────────
        rent_review_val = attr_kv.get("rent_review_date")
        if rent_review_val:
            try:
                review_date = date.fromisoformat(rent_review_val[:10])
                days_until = (review_date - today).days
                current_rent = lease.get("monthly_rent", 0)
                rate_raw = attr_kv.get("escalation_rate_pct")
                rate = float(str(rate_raw).rstrip("%") or 3) / 100 if rate_raw else 0.03
                new_rent = round(current_rent * (1 + rate), 2)
                esc_type_val = attr_kv.get("escalation_type") or "Fixed"
                key = f"{lease_id}-legacy"
                if key not in seen_ids:
                    seen_ids.add(key)
                    escalations.append({
                        "leaseId": lease_id,
                        "locationName": lease.get("store_name", "Unknown"),
                        "currentRent": current_rent,
                        "newRent": new_rent,
                        "effectiveDate": rent_review_val,
                        "type": esc_type_val,
                        "percentageChange": round(rate * 100, 2),
                        "daysUntilEffective": days_until,
                        "currency": lease.get("currency", "USD"),
                    })
            except Exception:
                pass

        # ── Grouped Expenses keys (xts extraction) ─────────────────────────
        # Slot 0 = current rent; slot N>0 with a future start_date = escalation step
        import re as _re
        from datetime import datetime as _dt

        _FMTS = ("%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d", "%d %B %Y", "%d %b %Y")

        def _parse_date_local(val: str):
            for fmt in _FMTS:
                try:
                    return _dt.strptime(val.strip(), fmt).date()
                except ValueError:
                    pass
            try:
                return date.fromisoformat(val[:10])
            except Exception:
                return None

        def _parse_amt(val: str):
            if not val:
                return None
            cleaned = _re.sub(r"[^\d.]", "", val.replace(",", ""))
            try:
                return float(cleaned) if cleaned else None
            except ValueError:
                return None

        prev_amount = None
        for i in range(20):
            rent_type = attr_kv.get(f"expenses_{i}_rent_type")
            start_raw = attr_kv.get(f"expenses_{i}_start_date")
            amount_raw = attr_kv.get(f"expenses_{i}_monthly_amount")
            if rent_type is None and start_raw is None:
                break
            cur_amount = _parse_amt(amount_raw or "")
            if i == 0:
                prev_amount = cur_amount
                continue
            if not start_raw or not cur_amount:
                prev_amount = cur_amount or prev_amount
                continue
            start_d = _parse_date_local(start_raw)
            if not start_d:
                prev_amount = cur_amount
                continue
            days_until = (start_d - today).days
            key = f"{lease_id}-exp-{i}"
            if key not in seen_ids:
                seen_ids.add(key)
                base = prev_amount or lease.get("monthly_rent", 0) or 0
                rate = round((cur_amount - base) / base, 4) if base else 0.0
                esc_rate_raw = attr_kv.get("escalation_rate_pct")
                if esc_rate_raw:
                    try:
                        rate = float(str(esc_rate_raw).rstrip("%")) / 100
                        cur_amount = round(base * (1 + rate), 2)
                    except Exception:
                        pass
                escalations.append({
                    "leaseId": lease_id,
                    "locationName": lease.get("store_name", "Unknown"),
                    "currentRent": base,
                    "newRent": cur_amount,
                    "effectiveDate": start_d.isoformat(),
                    "type": rent_type or "Fixed",
                    "percentageChange": round(rate * 100, 2),
                    "daysUntilEffective": days_until,
                    "currency": attr_kv.get(f"expenses_{i}_currency") or lease.get("currency", "INR"),
                })
            prev_amount = cur_amount

    escalations.sort(key=lambda x: x["daysUntilEffective"])
    return {"escalations": escalations}


@router.get("/communications")
def list_communications(
    lease_id: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    direction: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20),
    org_id: str = Depends(current_org_id),
):
    comms = scoped(db["communications"].values(), org_id)

    if lease_id:
        comms = [c for c in comms if c.get("lease_id") == lease_id]
    if type:
        comms = [c for c in comms if c.get("type") == type]
    if direction:
        comms = [c for c in comms if c.get("direction") == direction]

    comms.sort(key=lambda x: x.get("sent_at", ""), reverse=True)
    total = len(comms)
    start = (page - 1) * pageSize
    return {"communications": comms[start: start + pageSize], "total": total}


@router.post("/communications")
def create_communication(body: CommunicationCreate, org_id: str = Depends(current_org_id)):
    comm_id = str(uuid.uuid4())
    comm = {
        "comm_id": comm_id,
        "lease_id": body.leaseId,
        "org_id": org_id,
        "dispute_id": body.disputeId,
        "type": body.type,
        "direction": body.direction,
        "subject": body.subject,
        "body": body.body,
        "sent_by": body.sentBy or "user-001",
        "sent_at": datetime.utcnow().isoformat(),
        "linked_clause": body.linkedClause,
    }
    db["communications"][comm_id] = comm
    return comm


@router.get("/leases/{lease_id}/communications")
def get_lease_communications(lease_id: str, org_id: str = Depends(current_org_id)):
    lease = db["leases"].get(lease_id)
    if lease:
        require_same_org(lease, org_id)
    comms = [c for c in db["communications"].values() if c.get("lease_id") == lease_id]
    comms.sort(key=lambda x: x.get("sent_at", ""), reverse=True)
    return {"communications": comms}


# ── Tasks ─────────────────────────────────────────────────────────────────────

class TaskCreate(BaseModel):
    title: str
    source: str = "administration"
    sourceRecordId: str = ""
    sourceRecordLabel: str = ""
    actionType: str = "manual"
    priority: str = "medium"
    status: str = "open"
    assignedTo: Optional[str] = None
    assignedToName: Optional[str] = None
    watchedBy: List[str] = []
    dueDate: Optional[str] = None
    isAutoGenerated: bool = False
    requiresApproval: bool = False
    linkedLeaseId: Optional[str] = None
    notes: Optional[str] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    assignedTo: Optional[str] = None
    assignedToName: Optional[str] = None
    watchedBy: Optional[List[str]] = None
    dueDate: Optional[str] = None
    approvalStatus: Optional[str] = None
    approvalNotes: Optional[str] = None
    notes: Optional[str] = None


class ApprovalRequest(BaseModel):
    action: str  # "approve" | "reject"
    notes: Optional[str] = None


@router.get("/tasks")
def list_tasks(
    source: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    assigned_to: Optional[str] = Query(None),
    org_id: str = Depends(current_org_id),
):
    tasks = scoped(db.get("tasks", {}).values(), org_id)
    if source:
        tasks = [t for t in tasks if t.get("source") == source]
    if status:
        tasks = [t for t in tasks if t.get("status") == status]
    if assigned_to:
        tasks = [t for t in tasks if t.get("assignedTo") == assigned_to]
    tasks.sort(key=lambda t: (t.get("dueDate") or "9999", t.get("priority", "low")))
    return {"tasks": tasks}


@router.post("/tasks")
def create_task(body: TaskCreate, org_id: str = Depends(current_org_id)):
    task_id = str(uuid.uuid4())
    task = {
        "taskId": task_id,
        "org_id": org_id,
        "title": body.title,
        "source": body.source,
        "sourceRecordId": body.sourceRecordId,
        "sourceRecordLabel": body.sourceRecordLabel,
        "actionType": body.actionType,
        "priority": body.priority,
        "status": body.status,
        "assignedTo": body.assignedTo,
        "assignedToName": body.assignedToName,
        "watchedBy": body.watchedBy,
        "dueDate": body.dueDate,
        "createdAt": datetime.utcnow().isoformat(),
        "completedAt": None,
        "isAutoGenerated": body.isAutoGenerated,
        "requiresApproval": body.requiresApproval,
        "approvalStatus": None,
        "approvalRequestedFrom": None,
        "approvalNotes": None,
        "linkedLeaseId": body.linkedLeaseId,
        "notes": body.notes or "",
    }
    db["tasks"][task_id] = task
    return task


@router.patch("/tasks/{task_id}")
def update_task(task_id: str, body: TaskUpdate, org_id: str = Depends(current_org_id)):
    task = db["tasks"].get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.get("org_id") and task["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Task not found")
    updates = body.model_dump(exclude_unset=True)
    if updates.get("status") == "done" and not task.get("completedAt"):
        updates["completedAt"] = datetime.utcnow().isoformat()
    task.update(updates)
    return task


@router.post("/tasks/{task_id}/approve")
def approve_task(task_id: str, body: ApprovalRequest, org_id: str = Depends(current_org_id)):
    task = db["tasks"].get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.get("org_id") and task["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Task not found")
    task["approvalStatus"] = "approved" if body.action == "approve" else "rejected"
    task["approvalNotes"] = body.notes
    if body.action == "approve":
        task["status"] = "in_progress"
    return task


@router.post("/tasks/{task_id}/nudge")
def nudge_task(task_id: str, org_id: str = Depends(current_org_id)):
    task = db["tasks"].get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"taskId": task_id, "nudgeSent": True}


# ── Obligations ────────────────────────────────────────────────────────────────

class ObligationUpdate(BaseModel):
    status: Optional[str] = None
    lastActionDate: Optional[str] = None
    notes: Optional[str] = None


class PercentRentSubmit(BaseModel):
    salesVolume: float
    rentRate: float
    minimumRent: float
    period: str


@router.get("/obligations")
def list_obligations(status: Optional[str] = Query(None), org_id: str = Depends(current_org_id)):
    obligations = scoped(db.get("obligations", {}).values(), org_id)
    if status:
        obligations = [o for o in obligations if o.get("status") == status]
    obligations.sort(key=lambda o: o.get("dueDate", ""))
    return {"obligations": obligations}


@router.patch("/obligations/{obligation_id}")
def update_obligation(obligation_id: str, body: ObligationUpdate, org_id: str = Depends(current_org_id)):
    obl = db["obligations"].get(obligation_id)
    if not obl:
        raise HTTPException(status_code=404, detail="Obligation not found")
    lease = db["leases"].get(obl.get("leaseId") or obl.get("lease_id", ""), {})
    if lease:
        require_same_org(lease, org_id)
    updates = body.model_dump(exclude_unset=True)
    obl.update(updates)
    return obl


@router.post("/obligations/{obligation_id}/percent-rent")
def submit_percent_rent(obligation_id: str, body: PercentRentSubmit, org_id: str = Depends(current_org_id)):
    obl = db["obligations"].get(obligation_id)
    if not obl:
        raise HTTPException(status_code=404, detail="Obligation not found")
    lease = db["leases"].get(obl.get("leaseId") or obl.get("lease_id", ""), {})
    if lease:
        require_same_org(lease, org_id)

    calculated_rent = body.salesVolume * (body.rentRate / 100)
    exceeds_minimum = calculated_rent > body.minimumRent
    obl["lastActionDate"] = datetime.utcnow().date().isoformat()
    obl["percentRentData"] = {
        "salesVolume": body.salesVolume,
        "rentRate": body.rentRate,
        "minimumRent": body.minimumRent,
        "calculatedRent": round(calculated_rent, 2),
        "period": body.period,
    }

    # Workflow 12 — % Rent threshold exceeded, finance needs to log the payment
    if exceeds_minimum:
        from app.services.task_emitter import emit_task
        overage = round(calculated_rent - body.minimumRent, 2)
        emit_task(
            title=f"% Rent threshold exceeded — ${overage:,.0f} over minimum at {obl.get('location', obligation_id[:8])}. Log payment.",
            source="administration",
            action_type="percent_rent_payment",
            assignee_role="finance",
            priority="high",
            lease_id=obl.get("leaseId"),
            due_days=7,
        )

    return {
        "obligationId": obligation_id,
        "calculatedRent": round(calculated_rent, 2),
        "exceedsMinimum": exceeds_minimum,
        "period": body.period,
    }


# ── Renewals ────────────────────────────────────────────────────────────────────

class RenewalUpdate(BaseModel):
    stage: Optional[str] = None
    decision: Optional[str] = None
    decisionDate: Optional[str] = None
    notes: Optional[str] = None


@router.get("/renewals")
def list_renewals(org_id: str = Depends(current_org_id)):
    renewals = scoped(db.get("renewals", {}).values(), org_id)
    renewals.sort(key=lambda r: r.get("monthsRemaining", 99))
    return {"renewals": renewals}


@router.get("/renewals/{renewal_id}")
def get_renewal(renewal_id: str, org_id: str = Depends(current_org_id)):
    renewal = db["renewals"].get(renewal_id)
    if not renewal:
        raise HTTPException(status_code=404, detail="Renewal not found")
    lease_for_check = db["leases"].get(renewal.get("leaseId", ""), {})
    if lease_for_check:
        require_same_org(lease_for_check, org_id)

    # Attach lease details for richer display on the detail page
    lease = db["leases"].get(renewal.get("leaseId", ""), {})
    return {
        **renewal,
        "lease": {
            "leaseId":          lease.get("lease_id"),
            "storeName":        lease.get("store_name"),
            "storeCode":        lease.get("store_code"),
            "landlordName":     lease.get("landlord_name"),
            "commencementDate": lease.get("commencement_date"),
            "expiryDate":       lease.get("expiry_date"),
            "monthlyRent":      lease.get("monthly_rent"),
            "currency":         lease.get("currency"),
            "leaseType":        lease.get("lease_type"),
            "city":             lease.get("city"),
            "country":          lease.get("country"),
        } if lease else None,
    }


@router.patch("/renewals/{renewal_id}")
def update_renewal(renewal_id: str, body: RenewalUpdate, org_id: str = Depends(current_org_id)):
    renewal = db["renewals"].get(renewal_id)
    if not renewal:
        raise HTTPException(status_code=404, detail="Renewal not found")
    lease = db["leases"].get(renewal.get("leaseId", ""), {})
    if lease:
        require_same_org(lease, org_id)
    updates = body.model_dump(exclude_unset=True)
    renewal.update(updates)
    return renewal


# ── Payments ────────────────────────────────────────────────────────────────────

class PaymentCreate(BaseModel):
    leaseId: str
    locationName: str
    storeCode: str
    period: str
    expectedAmount: float
    actualAmount: Optional[float] = None
    paymentDate: Optional[str] = None
    status: str = "pending"
    currency: str = "USD"


@router.get("/payments")
def list_payments(period: Optional[str] = Query(None), org_id: str = Depends(current_org_id)):
    payments = scoped(db.get("payments", {}).values(), org_id)
    if period:
        payments = [p for p in payments if p.get("period") == period]
    return {"payments": payments}


@router.post("/payments")
def log_payment(body: PaymentCreate, org_id: str = Depends(current_org_id)):
    payment_id = str(uuid.uuid4())
    variance = None
    if body.actualAmount is not None:
        variance = round(body.actualAmount - body.expectedAmount, 2)
    payment = {
        "paymentId": payment_id,
        "org_id": org_id,
        "leaseId": body.leaseId,
        "locationName": body.locationName,
        "storeCode": body.storeCode,
        "period": body.period,
        "expectedAmount": body.expectedAmount,
        "actualAmount": body.actualAmount,
        "paymentDate": body.paymentDate,
        "variance": variance,
        "status": body.status,
        "currency": body.currency,
    }
    db["payments"][payment_id] = payment
    return payment


# ── Admin Summary ──────────────────────────────────────────────────────────────

@router.get("/administration/summary")
def get_admin_summary(org_id: str = Depends(current_org_id)):
    today = date.today()
    tasks = scoped(db.get("tasks", {}).values(), org_id)
    open_tasks = len([t for t in tasks if t.get("status") not in ("done",)])
    overdue_tasks = len([t for t in tasks if t.get("status") == "overdue"])

    # Critical dates ≤ 30 days from now
    critical_30 = 0
    for lease in scoped(db["leases"].values(), org_id):
        exp = lease.get("expiry_date")
        if exp:
            try:
                d = date.fromisoformat(exp[:10])
                days = (d - today).days
                if 0 <= days <= 30:
                    critical_30 += 1
            except Exception:
                pass

    renewals_window = len(scoped(db.get("renewals", {}).values(), org_id))
    open_disputes = len([d for d in scoped(db.get("disputes", {}).values(), org_id) if d.get("status") not in ("resolved", "closed")])

    return {
        "openTasks": open_tasks,
        "overdueTasks": overdue_tasks,
        "criticalDates30Days": critical_30,
        "renewalsInWindow": renewals_window,
        "openDisputes": open_disputes,
    }


# ── Month-end Checklist ────────────────────────────────────────────────────────

@router.get("/administration/month-end-checklist")
def get_checklist(org_id: str = Depends(current_org_id)):
    items = list(db.get("month_end_checklist", {}).values())
    return {"items": items}


@router.post("/administration/month-end-checklist/{item_id}/complete")
def complete_checklist_item(item_id: str, org_id: str = Depends(current_org_id)):
    item = db["month_end_checklist"].get(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Checklist item not found")
    item["isDone"] = not item["isDone"]
    return item


# ── Workflow Rules ─────────────────────────────────────────────────────────────

class WorkflowRuleUpdate(BaseModel):
    requiresApproval: Optional[bool] = None
    approvalRole: Optional[str] = None


@router.get("/settings/workflow-rules")
def get_workflow_rules(user=Depends(require_admin)):
    rules = list(db.get("workflow_rules", {}).values())
    return {"rules": rules}


@router.patch("/settings/workflow-rules/{rule_id}")
def update_workflow_rule(rule_id: str, body: WorkflowRuleUpdate, user=Depends(require_admin)):
    rule = db["workflow_rules"].get(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    if not rule.get("isConfigurable"):
        raise HTTPException(status_code=403, detail="This rule cannot be modified")
    updates = body.model_dump(exclude_unset=True)
    rule.update(updates)
    return rule


# ── Notice Status ──────────────────────────────────────────────────────────────

class NoticeStatusUpdate(BaseModel):
    noticeStatus: str


@router.patch("/communications/{comm_id}/notice-status")
def update_notice_status(comm_id: str, body: NoticeStatusUpdate, org_id: str = Depends(current_org_id)):
    comm = db["communications"].get(comm_id)
    if not comm:
        raise HTTPException(status_code=404, detail="Communication not found")
    require_same_org(comm, org_id)
    comm["noticeStatus"] = body.noticeStatus
    return comm


# ── Alert Config ──────────────────────────────────────────────────────────────

DEFAULT_ALERT_CONFIG = {
    "thresholds": {
        "expiry": [30, 60, 90, 180],
        "rent_review": [30, 60],
        "renewal": [90, 180],
        "break_clause": [60, 90],
        "insurance_cert": [30, 60],
        "cam_statement": [30],
    },
}


@router.get("/administration/alert-config")
def get_alert_config(org_id: str = Depends(current_org_id)):
    return db.get("org_settings", {}).get(org_id, {}).get("alert_config", {**DEFAULT_ALERT_CONFIG, "orgId": org_id})


@router.patch("/administration/alert-config")
def update_alert_config(body: dict, org_id: str = Depends(current_org_id)):
    if "org_settings" not in db:
        db["org_settings"] = {}
    if org_id not in db["org_settings"]:
        db["org_settings"][org_id] = {}
    existing = db["org_settings"][org_id].get("alert_config", DEFAULT_ALERT_CONFIG)
    updated = {**existing, **body, "orgId": org_id, "updatedAt": datetime.utcnow().isoformat()}
    db["org_settings"][org_id]["alert_config"] = updated
    return updated


# ── Nightly Jobs ──────────────────────────────────────────────────────────────

@router.post("/administration/nightly-jobs/run")
def run_nightly_jobs(org_id: str = Depends(current_org_id)):
    from app.services.task_emitter import emit_task
    today = date.today()
    created = []

    # Workflow 6 — leases entering the 18-month (540-day) renewal decision window
    for lease in scoped(db["leases"].values(), org_id):
        exp = lease.get("expiry_date")
        if not exp:
            continue
        try:
            d = date.fromisoformat(exp[:10])
            days = (d - today).days
            if 530 <= days <= 550:  # ±10-day window avoids duplicate tasks on consecutive runs
                task = emit_task(
                    title=f"{lease.get('store_name', lease['lease_id'])} enters 18-month renewal window — initiate renewal strategy.",
                    source="administration",
                    action_type="renewal_window_alert",
                    assignee_role="re_director",
                    priority="high",
                    lease_id=lease["lease_id"],
                    due_days=30,
                )
                created.append(task["taskId"])
        except Exception:
            pass

    # Workflow 9 — insurance certificates unacknowledged for 14+ days
    for obl in scoped(db.get("obligations", {}).values(), org_id):
        if obl.get("obligationType", "") != "Insurance Certificate":
            continue
        last_action = obl.get("lastActionDate")
        if not last_action:
            continue
        try:
            d = date.fromisoformat(last_action[:10])
            days_since = (today - d).days
            if days_since >= 14 and obl.get("status") != "compliant":
                task = emit_task(
                    title=f"Insurance certificate unacknowledged 14+ days at {obl.get('location', 'location')} — escalate to landlord.",
                    source="administration",
                    action_type="insurance_cert_escalation",
                    assignee_role="lease_admin",
                    priority="medium",
                    lease_id=obl.get("leaseId"),
                    due_days=7,
                )
                created.append(task["taskId"])
        except Exception:
            pass

    # Workflow 13 — month-end checklist items overdue by 3+ days
    for item in db.get("month_end_checklist", {}).values():
        if item.get("isDone"):
            continue
        due = item.get("dueDate") or item.get("due_date")
        if not due:
            continue
        try:
            d = date.fromisoformat(due[:10])
            overdue_days = (today - d).days
            if overdue_days >= 3:
                task = emit_task(
                    title=f"Month-end item '{item.get('title', 'checklist item')}' is {overdue_days} days overdue — complete immediately.",
                    source="month_end",
                    action_type="month_end_overdue",
                    assignee_role="finance",
                    priority="medium",
                    due_days=1,
                )
                created.append(task["taskId"])
        except Exception:
            pass

    return {"tasksCreated": len(created), "taskIds": created, "ranAt": datetime.utcnow().isoformat()}

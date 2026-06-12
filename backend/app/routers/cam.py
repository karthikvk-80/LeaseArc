from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from app.mock_db import db
from app.services.org_isolation import current_org_id, scoped, require_same_org
from typing import Optional
import uuid
import random
from datetime import date, datetime

router = APIRouter()


@router.post("/statements/upload")
async def upload_cam_statement(
    file: UploadFile = File(...),
    lease_id: str = Form(...),
    year: int = Form(...),
    org_id: str = Depends(current_org_id),
):
    lease = db["leases"].get(lease_id)
    if not lease:
        raise HTTPException(status_code=404, detail="Lease not found")
    require_same_org(lease, org_id)

    statement_id = str(uuid.uuid4())
    db["cam_statements"][statement_id] = {
        "statement_id": statement_id,
        "lease_id": lease_id,
        "org_id": lease.get("org_id"),
        "statement_year": year,
        "landlord_total": 0,
        "audited_total": 0,
        "variance_amount": 0,
        "percent_overcharged": 0,
        "status": "uploaded",
        "pdf_url": f"/mock/cam/{statement_id}.pdf",
    }
    # Workflow 3 — CAM statement uploaded, audit not yet run
    from app.services.task_emitter import emit_task
    location = lease.get("store_name", lease_id)
    emit_task(
        title=f"CAM audit not run — {location} statement {year}",
        source="cam",
        action_type="cam_audit_pending",
        assignee_role="finance",
        priority="high",
        lease_id=lease_id,
        watched_by_role="re_director",
        due_days=3,
    )
    return {"statementId": statement_id, "leaseId": lease_id, "year": year, "status": "uploaded"}


@router.get("/statements")
def list_cam_statements(lease_id: Optional[str] = Query(None), org_id: str = Depends(current_org_id)):
    if lease_id:
        stmts = [s for s in db["cam_statements"].values() if s["lease_id"] == lease_id and s.get("org_id") == org_id]
    else:
        stmts = [s for s in db["cam_statements"].values() if s.get("org_id") == org_id]
    stmts.sort(key=lambda x: x["variance_amount"], reverse=True)
    result = []
    for s in stmts:
        lease = db["leases"].get(s["lease_id"], {})
        result.append({
            "statementId": s["statement_id"],
            "leaseId": s["lease_id"],
            "year": s["statement_year"],
            "locationName": lease.get("store_name", "Unknown"),
            "country": lease.get("country", ""),
            "landlordName": lease.get("landlord_name", ""),
            "landlordTotal": s["landlord_total"],
            "auditedTotal": s["audited_total"],
            "varianceAmount": s["variance_amount"],
            "percentOvercharged": s["percent_overcharged"],
            "status": s["status"],
        })
    return {"statements": result}


@router.get("/statements/{statement_id}/extracted")
def get_extracted(statement_id: str, org_id: str = Depends(current_org_id)):
    stmt = db["cam_statements"].get(statement_id, {})
    if stmt.get("org_id") and stmt["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Statement not found")
    items = db["cam_line_items"].get(statement_id)
    if not items:
        # Generate mock items for new uploads
        from app.seed.seed_cam import CAM_CATEGORIES, FLAG_REASONS, CLAUSE_REFS
        statement = db["cam_statements"].get(statement_id, {})
        org_id = db["leases"].get(statement.get("lease_id"), {}).get("org_id")
        items = []
        for cat in random.sample(CAM_CATEGORIES, 10):
            li_id = str(uuid.uuid4())
            amount = round(random.uniform(500, 30000), 2)
            items.append({
                "line_item_id": li_id,
                "statement_id": statement_id,
                "org_id": org_id,
                "expense_category": cat,
                "landlord_amount": amount,
                "confidence": random.randint(70, 99),
            })
        db["cam_line_items"][statement_id] = items

    return {
        "lineItems": [
            {
                "lineItemId": li["line_item_id"],
                "expenseCategory": li["expense_category"],
                "landlordAmount": li["landlord_amount"],
                "confidence": li.get("confidence", 85),
            }
            for li in items
        ]
    }


@router.post("/statements/{statement_id}/audit")
def run_audit(statement_id: str, org_id: str = Depends(current_org_id)):
    statement = db["cam_statements"].get(statement_id)
    if statement and statement.get("org_id") and statement["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Statement not found")
    items = db["cam_line_items"].get(statement_id, [])

    if not items:
        raise HTTPException(status_code=404, detail="No line items found for this statement")

    from app.seed.seed_cam import FLAG_REASONS, CLAUSE_REFS, CATEGORY_FLAG_REASONS, CATEGORY_GL_ACCOUNTS, _make_evidence_sources, _make_invoice_refs, _make_conflict

    audit_items = []
    landlord_total = 0.0
    allowable_total = 0.0
    stmt_year = statement.get("statement_year", 2024) if statement else 2024

    for li in items:
        cat = li.get("expense_category", "Common Area Maintenance")
        landlord = li.get("landlord_amount", 0)
        is_flagged = li.get("is_excluded", random.random() < 0.3)

        if is_flagged:
            allowable = 0.0
            flag_reason = li.get("flag_reason") or CATEGORY_FLAG_REASONS.get(cat, random.choice(FLAG_REASONS))
            status = "flagged"
            conflict_type = li.get("conflict_type") or _make_conflict(cat, landlord, stmt_year)[0]
            conflict_detail = li.get("conflict_detail") or _make_conflict(cat, landlord, stmt_year)[1]
        else:
            allowable = round(landlord * random.uniform(0.85, 1.0), 2)
            flag_reason = None
            variance = landlord - allowable
            status = "allowed" if variance < 100 else random.choice(["allowed", "under_review"])
            conflict_type, conflict_detail = None, None

        variance = round(landlord - allowable, 2)
        gl_code, gl_name = CATEGORY_GL_ACCOUNTS.get(cat, ("4000", "Operating Expenses"))
        # Preserve existing evidence if present (seeded items already have it)
        evidence = li.get("evidence_sources") or _make_evidence_sources(cat, is_flagged, flag_reason, stmt_year)
        clause_ref = li.get("lease_clause_ref") or (CATEGORY_FLAG_REASONS.get(cat, "Clause 12.3").split("—")[0].split("per ")[-1].strip() if is_flagged else random.choice(CLAUSE_REFS))
        audit_items.append({
            **li,
            "allowable_amount": allowable,
            "variance": variance,
            "is_excluded": is_flagged,
            "flag_reason": flag_reason,
            "status": status,
            "lease_clause_ref": clause_ref,
            "evidence_sources": evidence,
            "gl_account": li.get("gl_account") or f"{gl_code} — {gl_name}",
            "invoice_refs": li.get("invoice_refs") or (_make_invoice_refs(cat, stmt_year, random.randint(1, 2)) if is_flagged else []),
            "conflict_type": conflict_type,
            "conflict_detail": conflict_detail,
        })

        landlord_total += landlord
        allowable_total += allowable

    variance_amount = round(landlord_total - allowable_total, 2)
    pct = round(variance_amount / landlord_total * 100, 1) if landlord_total > 0 else 0

    db["cam_line_items"][statement_id] = audit_items
    if statement:
        statement["landlord_total"] = round(landlord_total, 2)
        statement["audited_total"] = round(allowable_total, 2)
        statement["variance_amount"] = variance_amount
        statement["percent_overcharged"] = pct
        statement["status"] = "audited"

    # Workflow 4 — CAM audit complete, variance found
    if variance_amount > 0 and statement:
        from app.services.task_emitter import emit_task
        lease = db.get("leases", {}).get(statement.get("lease_id", ""), {})
        location = lease.get("store_name", statement.get("lease_id", ""))
        emit_task(
            title=f"CAM variance found — ${variance_amount:,.0f} overcharged, {location}",
            source="cam",
            action_type="cam_variance_review",
            assignee_role="re_director",
            priority="high",
            lease_id=statement.get("lease_id"),
            watched_by_role="finance",
        )

    audit_id = str(uuid.uuid4())
    return {
        "auditId": audit_id,
        "statementId": statement_id,
        "summary": {
            "landlordTotal": round(landlord_total, 2),
            "allowableTotal": round(allowable_total, 2),
            "varianceAmount": variance_amount,
            "percentOvercharged": pct,
        },
        "lineItems": audit_items,
    }


@router.get("/statements/{statement_id}/audit-results")
def get_audit_results(statement_id: str, org_id: str = Depends(current_org_id)):
    statement = db["cam_statements"].get(statement_id)
    items = db["cam_line_items"].get(statement_id, [])

    if not statement:
        raise HTTPException(status_code=404, detail="Statement not found")
    if statement.get("org_id") and statement["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Statement not found")

    formatted = []
    for li in items:
        formatted.append({
            "lineItemId": li.get("line_item_id", str(uuid.uuid4())),
            "expenseCategory": li.get("expense_category"),
            "landlordAmount": li.get("landlord_amount", 0),
            "allowableAmount": li.get("allowable_amount", li.get("landlord_amount", 0)),
            "variance": li.get("variance", 0),
            "isExcluded": li.get("is_excluded", False),
            "flagReason": li.get("flag_reason"),
            "status": li.get("status", "allowed"),
            "confidence": li.get("confidence", 85),
            "leaseClauseRef": li.get("lease_clause_ref", "Clause 12.3"),
            # Evidence fields
            "evidenceSources": li.get("evidence_sources", []),
            "glAccount": li.get("gl_account"),
            "invoiceRefs": li.get("invoice_refs", []),
            "conflictType": li.get("conflict_type"),
            "conflictDetail": li.get("conflict_detail"),
        })

    lease = db["leases"].get(statement.get("lease_id", ""), {})
    return {
        "statementId": statement_id,
        "year": statement.get("statement_year"),
        "locationName": lease.get("store_name", "Unknown"),
        "landlordName": lease.get("landlord_name", "Landlord"),
        "country": lease.get("country", ""),
        "summary": {
            "landlordTotal": statement.get("landlord_total", 0),
            "allowableTotal": statement.get("audited_total", 0),
            "varianceAmount": statement.get("variance_amount", 0),
            "percentOvercharged": statement.get("percent_overcharged", 0),
        },
        "lineItems": formatted,
        "proRata": {
            "gla": statement.get("pro_rata_gla"),
            "totalGla": statement.get("pro_rata_total_gla"),
            "allowablePct": statement.get("pro_rata_allowable"),
            "chargedPct": statement.get("pro_rata_charged"),
        } if statement.get("pro_rata_gla") else None,
    }


@router.get("/payment-dashboard")
def get_payment_dashboard(org_id: str = Depends(current_org_id)):
    lease_stmts: dict = {}
    for s in db["cam_statements"].values():
        if s["statement_year"] == 2024 and s.get("org_id") == org_id:
            lease_stmts[s["lease_id"]] = s

    leases = scoped(db["leases"].values(), org_id)
    locations = []
    billed_total = 0.0
    recovered_total = 0.0

    for lease in leases:
        lid = lease["lease_id"]
        stmt = lease_stmts.get(lid)
        if not stmt:
            continue
        billed = stmt["landlord_total"]
        audited = stmt["audited_total"]
        variance = round(billed - audited, 2)
        pct = stmt["percent_overcharged"]

        dispute_active = any(
            d.get("lease_id") == lid and d["status"] != "resolved"
            for d in db["disputes"].values()
        )

        if dispute_active:
            pay_status = "In dispute"
            paid = round(audited, 2)
        elif pct > 15:
            pay_status = "Overbilled"
            paid = round(audited, 2)
        elif pct < 2:
            pay_status = "On time"
            paid = round(billed, 2)
        else:
            pay_status = "Under review"
            paid = round(billed * random.uniform(0.90, 0.98), 2)

        billed_total += billed
        recovered_total += variance

        locations.append({
            "statementId": stmt["statement_id"],
            "leaseId": lid,
            "locationName": lease.get("store_name", "Unknown"),
            "country": lease.get("country", ""),
            "year": 2024,
            "billed": round(billed, 2),
            "paid": round(paid, 2),
            "variance": round(billed - paid, 2),
            "percentOvercharged": pct,
            "paymentStatus": pay_status.lower().replace(" ", "_"),
        })

    locations.sort(key=lambda x: x["variance"], reverse=True)

    return {
        "metrics": {
            "totalBilled": round(billed_total, 2),
            "totalRecovered": round(recovered_total, 2),
            "outstanding": round(billed_total - recovered_total, 2),
        },
        "payments": locations,
    }


@router.get("/accrual-forecast")
def get_accrual_forecast(org_id: str = Depends(current_org_id)):
    today = date.today()
    active_leases = [l for l in scoped(db["leases"].values(), org_id) if l["status"] in ("active", "expiring")]
    base_cam = sum(l.get("monthly_rent", 0) * 0.25 for l in active_leases)

    forecast = []
    for i in range(6):
        m = today.month + i
        y = today.year
        while m > 12:
            m -= 12
            y += 1
        accrual = round(base_cam * random.uniform(0.95, 1.05), 2)
        actual = round(accrual * random.uniform(0.90, 1.08), 2) if i < 3 else None
        entry = {"month": date(y, m, 1).strftime("%b %Y"), "accrual": accrual}
        if actual is not None:
            entry["actual"] = actual
        forecast.append(entry)

    return {"forecast": forecast}


@router.get("/yoy-trend")
def get_yoy_trend(org_id: str = Depends(current_org_id)):
    by_lease: dict = {}
    for s in db["cam_statements"].values():
        if s.get("org_id") != org_id:
            continue
        lid = s["lease_id"]
        yr = s["statement_year"]
        if lid not in by_lease:
            by_lease[lid] = {}
        by_lease[lid][yr] = s

    locations = []
    for lid, years in by_lease.items():
        if 2023 not in years or 2024 not in years:
            continue
        lease = db["leases"].get(lid, {})
        y1 = years[2023]["landlord_total"]
        y2 = years[2024]["landlord_total"]
        locations.append({
            "locationId": lease.get("location_id", lid),
            "locationName": lease.get("store_name", "Unknown"),
            "year1": round(y1, 2),
            "year2": round(y2, 2),
            "variance": round(y2 - y1, 2),
        })

    locations.sort(key=lambda x: abs(x["variance"]), reverse=True)
    return {"locations": locations[:15]}


@router.get("/cfo-summary")
def get_cfo_summary(org_id: str = Depends(current_org_id)):
    statements = [s for s in db["cam_statements"].values() if s.get("org_id") == org_id]
    total_exposure = sum(s.get("landlord_total", 0) for s in statements)
    disputes = [d for d in db["disputes"].values() if d.get("org_id") == org_id]
    disputes_raised = len(disputes)
    amount_recovered = sum(d.get("recovered_amount", 0) for d in disputes)

    leases = scoped(db["leases"].values(), org_id)
    top_locations = sorted(
        [{"leaseId": l["lease_id"], "locationName": l.get("store_name", "Unknown"), "camCost": round(l.get("monthly_rent", 0) * 0.25 * 12, 2)} for l in leases],
        key=lambda x: x["camCost"],
        reverse=True
    )[:5]

    # Top landlords by overcharge frequency
    from collections import Counter
    landlord_counts: Counter = Counter()
    for s in statements:
        lease = db["leases"].get(s.get("lease_id", ""), {})
        if lease and s.get("variance_amount", 0) > 0:
            landlord_counts[lease.get("landlord_name", "Unknown")] += 1

    top_landlords = [
        {"landlordName": name, "disputeCount": count}
        for name, count in landlord_counts.most_common(5)
    ]

    return {
        "totalExposure": round(total_exposure, 2),
        "disputesRaised": disputes_raised,
        "amountRecovered": round(amount_recovered, 2),
        "topLocations": top_locations,
        "topLandlords": top_landlords,
    }


# ── Audit Session Endpoints ────────────────────────────────────────────────────

# Plan bullet text keyed by docType, in priority order
_PLAN_BULLETS = [
    {
        "id": "lease_documents",
        "docType": "lease_documents",
        "focusArea": "exclusions",
        "text": (
            "I'll go through the executed lease to pull out exactly which charges are excluded: "
            "management fees, capex, and structural costs. Anything the landlord billed that the "
            "lease says can't be recovered gets flagged straight away."
        ),
    },
    {
        "id": "general_ledger",
        "docType": "general_ledger",
        "focusArea": "exclusions",
        "text": (
            "I'll scan the GL line by line and separate recoverable operating costs from anything "
            "that looks like capital spend or internal overhead. Any account code that shouldn't "
            "be in a CAM bill gets pulled out."
        ),
    },
    {
        "id": "rent_roll",
        "docType": "rent_roll",
        "focusArea": "pro_rata",
        "text": (
            "I'll use the rent roll to recalculate your pro-rata share from first principles: "
            "your GLA against total lettable area. If the landlord's percentage doesn't match, "
            "we'll know exactly where the difference sits."
        ),
    },
    {
        "id": "occupancy_report",
        "docType": "occupancy_report",
        "focusArea": "pro_rata",
        "text": (
            "I'll check the occupancy figures to make sure vacant space isn't inflating your share "
            "of costs. If the lease has a gross-up clause, I'll apply it; if not, vacant periods "
            "should be excluded from the recoverable pool."
        ),
    },
    {
        "id": "invoices",
        "docType": "invoices",
        "focusArea": "invoice_verification",
        "text": (
            "I'll match every line item on the CAM statement back to an actual invoice. Anything "
            "without a supporting document, outside the fiscal year, or outside common area scope "
            "gets flagged for the landlord to explain."
        ),
    },
    {
        "id": "prev_year_recon",
        "docType": "prev_year_recon",
        "focusArea": "yoy_variance",
        "text": (
            "I'll compare this year's charges against last year's reconciliation. Any category "
            "that's jumped more than 15% year-on-year will need a landlord explanation before "
            "we accept it."
        ),
    },
]

_FALLBACK_BULLET = {
    "id": "fallback",
    "focusArea": "general",
    "text": (
        "Without the lease or GL to work from, I'll apply standard market-rate benchmarks. "
        "Management fees above 5% and anything that looks like capital spend will be flagged "
        "as likely unrecoverable."
    ),
}


def _generate_plan(docs: list) -> tuple:
    """Return (plan_bullets, clarifying_question | None)."""
    uploaded_types = {d["docType"] for d in docs if d.get("uploaded") and not d.get("skipped")}

    # Select bullets by priority, cap at 3; skip duplicate pro_rata focus
    bullets = []
    seen_focus = set()
    for b in _PLAN_BULLETS:
        if b["docType"] not in uploaded_types:
            continue
        # Only one pro_rata bullet
        if b["focusArea"] == "pro_rata" and "pro_rata" in seen_focus:
            continue
        bullets.append({"id": b["id"], "focusArea": b["focusArea"], "text": b["text"]})
        seen_focus.add(b["focusArea"])
        if len(bullets) == 3:
            break

    # Fallback if fewer than 2 bullets
    if len(bullets) < 2:
        bullets.append(_FALLBACK_BULLET)

    # Clarifying question logic — first match only
    has_lease = "lease_documents" in uploaded_types
    has_gl = "general_ledger" in uploaded_types
    has_invoices = "invoices" in uploaded_types
    has_rent_roll = "rent_roll" in uploaded_types
    has_occupancy = "occupancy_report" in uploaded_types

    clarifying_question = None
    if not has_lease and not has_gl:
        clarifying_question = {
            "question": (
                "Since I don't have the executed lease, I can't check the actual cap clause. "
                "What does your lease say about CAM expense caps?"
            ),
            "options": [
                "Hard cap at 5% of operating expenses",
                "Soft cap with carve-outs for taxes and insurance",
                "No explicit cap — review all charges on merit",
            ],
        }
    elif has_rent_roll and not has_occupancy:
        clarifying_question = {
            "question": (
                "I have the rent roll but no occupancy data. How should I handle "
                "landlord-vacant units when I'm calculating the recoverable cost pool?"
            ),
            "options": [
                "Gross-up to 95% occupancy (lease gross-up clause applies)",
                "Exclude vacant unit costs from the recoverable pool",
                "Use actual occupancy — no gross-up provision in the lease",
            ],
        }
    elif has_gl and not has_invoices:
        clarifying_question = {
            "question": (
                "I can see the GL entries but there's no invoice pack to match against. "
                "What should I do with charges that don't have a supporting invoice?"
            ),
            "options": [
                "Flag all charges lacking invoice references",
                "Allow charges under $1,000 without invoice support",
                "Request invoices only for flagged or high-value categories",
            ],
        }

    return bullets, clarifying_question


@router.post("/audit-sessions")
def create_audit_session(body: dict, org_id: str = Depends(current_org_id)):
    lease_id = body.get("leaseId")
    fiscal_year = body.get("fiscalYear", datetime.utcnow().year - 1)

    lease = db["leases"].get(lease_id)
    if not lease:
        raise HTTPException(status_code=404, detail="Lease not found")
    require_same_org(lease, org_id)

    session_id = str(uuid.uuid4())
    db["cam_audit_sessions"][session_id] = {
        "session_id": session_id,
        "lease_id": lease_id,
        "org_id": db["leases"][lease_id].get("org_id"),
        "fiscal_year": fiscal_year,
        "docs": [],
        "plan_bullets": [],
        "clarifying_question": None,
        "clarifying_answer": None,
        "plan_approved": False,
        "statement_id": None,
        "status": "draft",
        "created_at": datetime.utcnow().isoformat(),
    }
    return {"sessionId": session_id}


@router.post("/audit-sessions/{session_id}/generate-plan")
def generate_audit_plan(session_id: str, body: dict, org_id: str = Depends(current_org_id)):
    session = db["cam_audit_sessions"].get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("org_id") and session["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Session not found")

    docs = body.get("docs", [])
    session["docs"] = docs

    bullets, clarifying_question = _generate_plan(docs)
    session["plan_bullets"] = bullets
    session["clarifying_question"] = clarifying_question
    session["status"] = "plan_ready"

    return {
        "planBullets": bullets,
        "clarifyingQuestion": clarifying_question,
    }


@router.post("/audit-sessions/{session_id}/approve-and-run")
def approve_and_run(session_id: str, body: dict, org_id: str = Depends(current_org_id)):
    session = db["cam_audit_sessions"].get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("org_id") and session["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Session not found")

    lease_id = session["lease_id"]
    fiscal_year = session["fiscal_year"]
    lease = db["leases"].get(lease_id)
    if not lease:
        raise HTTPException(status_code=404, detail="Lease not found")

    # Store approved plan + answer
    session["plan_bullets"] = body.get("planBullets", session["plan_bullets"])
    session["clarifying_answer"] = body.get("clarifyingAnswer")
    session["plan_approved"] = True

    # Create a new cam_statement
    statement_id = str(uuid.uuid4())
    db["cam_statements"][statement_id] = {
        "statement_id": statement_id,
        "lease_id": lease_id,
        "org_id": lease.get("org_id"),
        "statement_year": fiscal_year,
        "landlord_total": 0,
        "audited_total": 0,
        "variance_amount": 0,
        "percent_overcharged": 0,
        "status": "uploaded",
        "pdf_url": f"/mock/cam/{statement_id}.pdf",
    }

    # Generate mock line items
    from app.seed.seed_cam import CAM_CATEGORIES, FLAG_REASONS, CLAUSE_REFS, CATEGORY_FLAG_REASONS, CATEGORY_GL_ACCOUNTS, _make_evidence_sources, _make_invoice_refs, _make_conflict

    n_items = random.randint(9, 13)
    categories_used = random.sample(CAM_CATEGORIES, min(n_items, len(CAM_CATEGORIES)))
    if n_items > len(categories_used):
        categories_used += random.choices(CAM_CATEGORIES, k=n_items - len(categories_used))

    items = []
    for cat in categories_used[:n_items]:
        li_id = str(uuid.uuid4())
        amount = round(random.uniform(1000, 35000), 2)
        items.append({
            "line_item_id": li_id,
            "statement_id": statement_id,
            "org_id": lease.get("org_id"),
            "expense_category": cat,
            "landlord_amount": amount,
            "confidence": random.randint(75, 99),
        })
    db["cam_line_items"][statement_id] = items

    # Run audit (same logic as run_audit endpoint)
    audit_items = []
    landlord_total = 0.0
    allowable_total = 0.0

    for li in items:
        cat = li.get("expense_category", "Common Area Maintenance")
        landlord = li.get("landlord_amount", 0)
        is_flagged = random.random() < 0.35
        if is_flagged:
            allowable = 0.0
            flag_reason = CATEGORY_FLAG_REASONS.get(cat, random.choice(FLAG_REASONS))
            status = "flagged"
            conflict_type, conflict_detail = _make_conflict(cat, landlord, fiscal_year)
        else:
            allowable = round(landlord * random.uniform(0.85, 1.0), 2)
            flag_reason = None
            variance = landlord - allowable
            status = "allowed" if variance < 100 else random.choice(["allowed", "under_review"])
            conflict_type, conflict_detail = None, None

        variance = round(landlord - allowable, 2)
        gl_code, gl_name = CATEGORY_GL_ACCOUNTS.get(cat, ("4000", "Operating Expenses"))
        clause_ref = CATEGORY_FLAG_REASONS.get(cat, "Clause 12.3").split("—")[0].split("per ")[-1].strip() if is_flagged else random.choice(CLAUSE_REFS)
        audit_items.append({
            **li,
            "allowable_amount": allowable,
            "variance": variance,
            "is_excluded": is_flagged,
            "flag_reason": flag_reason,
            "status": status,
            "lease_clause_ref": clause_ref,
            "evidence_sources": _make_evidence_sources(cat, is_flagged, flag_reason, fiscal_year),
            "gl_account": f"{gl_code} — {gl_name}",
            "invoice_refs": _make_invoice_refs(cat, fiscal_year, random.randint(1, 3)) if is_flagged else [],
            "conflict_type": conflict_type,
            "conflict_detail": conflict_detail,
        })
        landlord_total += landlord
        allowable_total += allowable

    variance_amount = round(landlord_total - allowable_total, 2)
    pct = round(variance_amount / landlord_total * 100, 1) if landlord_total > 0 else 0

    db["cam_line_items"][statement_id] = audit_items
    stmt = db["cam_statements"][statement_id]
    stmt["landlord_total"] = round(landlord_total, 2)
    stmt["audited_total"] = round(allowable_total, 2)
    stmt["variance_amount"] = variance_amount
    stmt["percent_overcharged"] = pct
    stmt["status"] = "audited"

    # Pro-rata data
    gla = random.randint(1200, 4000)
    total_gla = random.randint(8000, 18000)
    stmt["pro_rata_gla"] = gla
    stmt["pro_rata_total_gla"] = total_gla
    stmt["pro_rata_allowable"] = round(gla / total_gla * 100, 1)
    stmt["pro_rata_charged"] = round(gla / total_gla * 100 + random.uniform(-1.0, 3.5), 1)

    audit_id = str(uuid.uuid4())
    session["statement_id"] = statement_id
    session["status"] = "complete"

    return {"statementId": statement_id, "auditId": audit_id}

from fastapi import APIRouter, HTTPException, Request, Depends
from app.mock_db import db
from app.services.task_emitter import emit_task
from app.services.org_isolation import current_org_id, require_admin, scoped, require_same_org
import uuid
import json as _json
from datetime import datetime, date, timedelta
from math import floor

router = APIRouter()

# ── Country → Jurisdiction mapping ────────────────────────────────────────────

COUNTRY_TO_JURISDICTION = {
    "USA": "US", "Canada": "CA", "Mexico": "MX",
    "UK": "GB", "Germany": "DE", "France": "FR",
    "Netherlands": "NL", "Sweden": "SE", "UAE": "AE",
    "Australia": "AU", "Singapore": "SG", "Japan": "JP",
    "Hong Kong": "HK", "South Korea": "KR", "India": "IN",
    "Brazil": "BR", "Argentina": "AR",
}

JURISDICTION_DEFAULT_IBR = {
    "US": 4.2, "GB": 5.1, "AU": 5.5, "IN": 8.5,
    "SG": 3.8, "AE": 4.0, "CA": 5.0, "DE": 3.9,
    "FR": 3.9, "JP": 1.2, "BR": 11.5, "MX": 9.5,
    "KR": 4.1, "HK": 5.5, "NL": 3.9, "SE": 4.0,
}

GL_DEFAULTS = {
    "rou_asset": "1520",
    "accum_amort_rou": "1521",
    "lease_liability_current": "2310",
    "lease_liability_noncurrent": "2320",
    "interest_expense": "6120",
    "operating_lease_expense": "6110",
    "amortization_expense": "6130",
    "accounts_payable": "2010",
}


# ── Computation helpers ────────────────────────────────────────────────────────

def _get_ibr(country: str, org_id: str) -> float:
    jurisdiction = COUNTRY_TO_JURISDICTION.get(country, "US")
    ibrs = [v for v in db.get("ibrs", {}).values()
            if v.get("orgId") == org_id and v.get("jurisdiction") == jurisdiction]
    if ibrs:
        latest = sorted(ibrs, key=lambda x: x.get("effectiveDate", ""), reverse=True)[0]
        return float(latest["rate"])
    return JURISDICTION_DEFAULT_IBR.get(jurisdiction, 5.0)


def _classify_lease(lease: dict, standard: str) -> str:
    term_years = int(lease.get("lease_term_years", 5))
    # Under IFRS 16, short-term (≤12 mo) and low-value are exempt
    if standard == "ifrs16" and term_years * 12 <= 12:
        return "short_term"
    # Finance if term ≥ 7 years (simplified 75%/90% classification test for mock)
    if term_years >= 7:
        return "finance"
    return "operating"


def _build_payment_stream(monthly_rent: float, term_months: int, escalation_pct: float) -> list:
    payments = []
    for m in range(term_months):
        year = m // 12
        payments.append(monthly_rent * ((1.0 + escalation_pct / 100.0) ** year))
    return payments


def _compute_pv(payments: list, monthly_rate: float) -> float:
    return sum(p / (1.0 + monthly_rate) ** (i + 1) for i, p in enumerate(payments))


def _get_escalation_pct(lease_id: str) -> float:
    attrs = db.get("attributes", {}).get(lease_id, [])
    for a in attrs:
        if a.get("attribute_key") == "escalation_rate_pct":
            try:
                return float(a.get("extracted_value", 3.0))
            except (ValueError, TypeError):
                pass
    return 3.0


def _compute_schedule(lease: dict, ibr: float, standard: str, lease_class: str) -> list:
    monthly_rent = float(lease.get("monthly_rent", 0))
    term_months = int(lease.get("lease_term_years", 5)) * 12
    esc_pct = _get_escalation_pct(lease.get("lease_id", ""))
    monthly_rate = ibr / 100.0 / 12.0

    payments = _build_payment_stream(monthly_rent, term_months, esc_pct)
    initial_liability = _compute_pv(payments, monthly_rate)
    initial_rou = initial_liability  # simplified (no initial direct costs in seed)

    straight_line_expense = sum(payments) / term_months if term_months > 0 else 0.0

    liability = initial_liability
    rou = initial_rou

    # Compute commencement period
    try:
        comm_date = date.fromisoformat(lease.get("commencement_date", "2020-01-01"))
    except Exception:
        comm_date = date(2020, 1, 1)

    schedule = []
    for i, payment in enumerate(payments):
        period_date = comm_date + timedelta(days=i * 30)
        period = f"{period_date.year}-{period_date.month:02d}"

        interest = liability * monthly_rate
        principal = payment - interest
        if principal < 0:
            principal = 0.0

        # ROU amortization — straight-line over remaining life
        remaining = term_months - i
        rou_amort = rou / remaining if remaining > 0 else 0.0

        closing_liability = max(0.0, liability - principal)
        closing_rou = max(0.0, rou - rou_amort)

        if standard == "ifrs16" or lease_class == "finance":
            lease_expense = rou_amort
            interest_expense = interest
            amortization_expense = rou_amort
        else:
            # ASC 842 operating: single straight-line expense
            lease_expense = straight_line_expense
            interest_expense = interest
            amortization_expense = rou_amort

        schedule.append({
            "period": period,
            "opening_rou": round(rou, 2),
            "amortization": round(rou_amort, 2),
            "closing_rou": round(closing_rou, 2),
            "opening_liability": round(liability, 2),
            "payment": round(payment, 2),
            "interest": round(interest, 2),
            "principal": round(principal, 2),
            "closing_liability": round(closing_liability, 2),
            "lease_expense": round(lease_expense, 2),
            "interest_expense": round(interest_expense, 2),
            "amortization_expense": round(amortization_expense, 2),
        })

        liability = closing_liability
        rou = closing_rou

    return schedule, initial_liability, initial_rou


def _get_current_position(lease: dict, ibr: float, standard: str, lease_class: str):
    schedule, initial_liability, initial_rou = _compute_schedule(lease, ibr, standard, lease_class)
    today = date.today()
    try:
        comm_date = date.fromisoformat(lease.get("commencement_date", "2020-01-01"))
    except Exception:
        comm_date = date(2020, 1, 1)

    months_elapsed = (today.year - comm_date.year) * 12 + (today.month - comm_date.month)
    months_elapsed = max(0, min(months_elapsed, len(schedule) - 1))

    if schedule:
        current = schedule[months_elapsed]
        return (
            initial_liability, initial_rou,
            current["closing_liability"], current["closing_rou"],
            len(schedule) - months_elapsed
        )
    return initial_liability, initial_rou, 0.0, 0.0, 0


def _build_compliance_lease(lease: dict, standard: str, org_id: str) -> dict:
    ibr = _get_ibr(lease.get("country", "USA"), org_id)
    lease_class = _classify_lease(lease, standard)
    init_liab, init_rou, curr_liab, curr_rou, remaining = _get_current_position(
        lease, ibr, standard, lease_class
    )
    return {
        "lease_id": lease.get("lease_id"),
        "name": lease.get("store_name"),
        "city": lease.get("city"),
        "country": lease.get("country"),
        "currency": lease.get("currency", "USD"),
        "commencement_date": lease.get("commencement_date"),
        "expiry_date": lease.get("expiry_date"),
        "lease_term_years": lease.get("lease_term_years"),
        "monthly_rent": lease.get("monthly_rent"),
        "escalation_rate_pct": _get_escalation_pct(lease.get("lease_id", "")),
        "ibr": ibr,
        "lease_class": lease_class,
        "rou_asset_initial": round(init_rou, 2),
        "lease_liability_initial": round(init_liab, 2),
        "rou_asset_current": round(curr_rou, 2),
        "lease_liability_current": round(curr_liab, 2),
        "remaining_months": remaining,
        "status": lease.get("status"),
        "portfolio_id": lease.get("portfolio_id"),
    }


# ── Settings ──────────────────────────────────────────────────────────────────

@router.get("/compliance/settings")
def get_compliance_settings(user=Depends(require_admin)):
    org_id = str(user.org_id)
    return db.get("compliance_settings", {}).get(org_id, {
        "orgId": org_id,
        "standard": "ASC 842",
        "reportingCurrency": "USD",
        "jurisdiction": "US",
    })


@router.post("/compliance/settings")
def save_compliance_settings(body: dict, user=Depends(require_admin)):
    org_id = str(user.org_id)
    existing = db.get("compliance_settings", {}).get(org_id, {})
    updated = {
        **existing,
        **body,
        "orgId": org_id,
        "updatedAt": datetime.utcnow().isoformat(),
    }
    db["compliance_settings"][org_id] = updated
    return updated


# ── IBR ───────────────────────────────────────────────────────────────────────

@router.get("/compliance/ibr")
def get_ibrs(org_id: str = Depends(current_org_id)):
    ibrs = [v for v in db.get("ibrs", {}).values() if v.get("orgId") == org_id]
    return {"ibrs": sorted(ibrs, key=lambda x: x.get("effectiveDate", ""), reverse=True)}


@router.post("/compliance/ibr")
def add_ibr(body: dict, org_id: str = Depends(current_org_id)):
    ibr_id = str(uuid.uuid4())
    is_first = len([v for v in db.get("ibrs", {}).values() if v.get("orgId") == org_id]) == 0
    ibr = {
        "ibrId": ibr_id,
        "orgId": org_id,
        "jurisdiction": body.get("jurisdiction", "US"),
        "rate": body.get("rate", 0.0),
        "effectiveDate": body.get("effectiveDate", datetime.utcnow().strftime("%Y-%m-%d")),
        "addedBy": "Sarah Chen",
        "addedAt": datetime.utcnow().isoformat(),
    }
    db["ibrs"][ibr_id] = ibr

    if is_first:
        emit_task(
            title="Compliance setup complete — IBR configured, calculations will run when module goes live",
            source="compliance",
            action_type="ibr_configured",
            assignee_role="re_director",
            priority="low",
        )
    return ibr


@router.post("/compliance/ibr/suggest")
async def suggest_ibr(request: Request, body: dict, org_id: str = Depends(current_org_id)):
    jurisdiction = body.get("jurisdiction", "US")
    lease_term_years = float(body.get("lease_term_years", 5))
    asset_type = body.get("asset_type", "retail")
    credit_profile = body.get("credit_profile", "investment_grade")
    currency = body.get("currency", "USD")
    effective_date = body.get("effective_date", datetime.utcnow().strftime("%Y-%m-%d"))

    api_key = body.get("apiKey") or request.headers.get("x-anthropic-key")

    if api_key:
        import httpx
        prompt = (
            f"You are a CFO-level lease accounting advisor. A lessee needs to determine the Incremental Borrowing Rate (IBR) "
            f"under ASC 842 / IFRS 16 for:\n"
            f"- Jurisdiction: {jurisdiction}\n"
            f"- Lease term: {lease_term_years} years\n"
            f"- Asset type: {asset_type}\n"
            f"- Credit profile: {credit_profile}\n"
            f"- Currency: {currency}\n"
            f"- Effective date: {effective_date}\n\n"
            f"Return ONLY a valid JSON object (no markdown, no explanation outside JSON) with these exact keys:\n"
            f"suggested_ibr (number, percentage like 4.75), range_low (number), range_high (number), "
            f"reasoning (string, 2-3 sentences), factors (array of objects with keys 'factor' and 'value' strings).\n"
            f"The factors array must have exactly 4 items: benchmark rate, credit spread, asset type premium, collateralization adjustment."
        )
        payload = {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 600,
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                text = "".join(block.get("text", "") for block in data.get("content", []))
                clean = text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
                return _json.loads(clean)
        except Exception:
            pass

    # Fallback formula-based suggestion
    base_rates = {
        "US": 4.1, "GB": 4.8, "AU": 5.2, "IN": 8.0, "SG": 3.5,
        "AE": 3.8, "CA": 4.7, "DE": 3.7, "FR": 3.7, "JP": 1.0,
    }
    base = base_rates.get(jurisdiction, 4.5)
    term_adj = min(lease_term_years * 0.05, 0.5)
    credit_adj = {"investment_grade": 0.5, "sub_investment_grade": 1.5, "speculative": 2.5}.get(credit_profile, 0.5)
    asset_adj = {"retail": 0.2, "office": 0.1, "industrial": 0.05, "ground": -0.1}.get(asset_type, 0.15)
    suggested = round(base + term_adj + credit_adj + asset_adj - 0.1, 2)

    return {
        "suggested_ibr": suggested,
        "range_low": round(suggested - 0.5, 2),
        "range_high": round(suggested + 0.5, 2),
        "reasoning": (
            f"Based on the {jurisdiction} market as of {effective_date}, a {lease_term_years}-year secured "
            f"{asset_type} lease for an {credit_profile.replace('_', ' ')} lessee suggests an IBR of approximately "
            f"{suggested}%. This reflects current benchmark rates plus appropriate credit and asset-type adjustments."
        ),
        "factors": [
            {"factor": f"Benchmark rate ({jurisdiction} {int(lease_term_years)}Y swap)", "value": f"{base:.2f}%"},
            {"factor": f"Credit spread ({credit_profile.replace('_', ' ')})", "value": f"+{credit_adj:.2f}%"},
            {"factor": f"Asset type premium ({asset_type})", "value": f"+{asset_adj:.2f}%"},
            {"factor": "Collateralization adjustment", "value": "-0.10%"},
        ],
    }


# ── Compliance Leases ─────────────────────────────────────────────────────────

@router.get("/compliance/leases")
def get_compliance_leases(standard: str = "asc842", org_id: str = Depends(current_org_id)):
    leases = scoped(db.get("leases", {}).values(), org_id)
    overrides = db.get("compliance_lease_overrides", {})
    result = []
    for lease in leases:
        row = _build_compliance_lease(lease, standard, org_id)
        # Apply manual override if present
        lease_id = lease.get("lease_id")
        if lease_id in overrides:
            row["lease_class"] = overrides[lease_id].get("override_class", row["lease_class"])
            row["class_override"] = True
            row["override_reason"] = overrides[lease_id].get("reason", "")
        result.append(row)
    # Sort by lease_liability_current descending
    result.sort(key=lambda x: x.get("lease_liability_current", 0), reverse=True)
    return {"leases": result}


@router.get("/compliance/leases/{lease_id}/schedule")
def get_lease_schedule(lease_id: str, standard: str = "asc842", org_id: str = Depends(current_org_id)):
    lease = db.get("leases", {}).get(lease_id)
    if not lease:
        raise HTTPException(status_code=404, detail="Lease not found")
    require_same_org(lease, org_id)
    ibr = _get_ibr(lease.get("country", "USA"), org_id)
    lease_class = _classify_lease(lease, standard)
    schedule, initial_liability, initial_rou = _compute_schedule(lease, ibr, standard, lease_class)
    return {
        "lease_id": lease_id,
        "name": lease.get("store_name"),
        "standard": standard,
        "lease_class": lease_class,
        "ibr": ibr,
        "initial_rou": round(initial_rou, 2),
        "initial_liability": round(initial_liability, 2),
        "term_months": len(schedule),
        "schedule": schedule,
    }


@router.patch("/compliance/leases/{lease_id}/classify")
def override_lease_class(lease_id: str, body: dict, org_id: str = Depends(current_org_id)):
    lease = db.get("leases", {}).get(lease_id)
    if not lease:
        raise HTTPException(status_code=404, detail="Lease not found")
    require_same_org(lease, org_id)
    db["compliance_lease_overrides"][lease_id] = {
        "lease_id": lease_id,
        "override_class": body.get("lease_class", "operating"),
        "reason": body.get("reason", ""),
        "overridden_by": "Sarah Chen",
        "overridden_at": datetime.utcnow().isoformat(),
    }
    return {"success": True}


# ── Summary ───────────────────────────────────────────────────────────────────

@router.get("/compliance/summary")
def get_compliance_summary(standard: str = "asc842", org_id: str = Depends(current_org_id)):
    settings = db.get("compliance_settings", {}).get(org_id, {})
    leases = scoped(db.get("leases", {}).values(), org_id)
    today = date.today()

    operating_count = finance_count = short_term_count = 0
    total_rou = total_liability = 0.0
    ibr_weighted_sum = ibr_weight_total = 0.0
    term_weighted_sum = term_weight_total = 0.0
    expiring_12mo = 0

    for lease in leases:
        ibr = _get_ibr(lease.get("country", "USA"), org_id)
        lc = _classify_lease(lease, standard)

        if lc == "operating":
            operating_count += 1
        elif lc == "finance":
            finance_count += 1
        else:
            short_term_count += 1

        # Use simplified PV without full schedule computation for speed
        monthly_rent = float(lease.get("monthly_rent", 0))
        term_months = int(lease.get("lease_term_years", 5)) * 12
        monthly_rate = ibr / 100.0 / 12.0
        esc_pct = _get_escalation_pct(lease.get("lease_id", ""))
        payments = _build_payment_stream(monthly_rent, term_months, esc_pct)
        init_liab = _compute_pv(payments, monthly_rate)

        # Current position: months elapsed
        try:
            comm_date = date.fromisoformat(lease.get("commencement_date", "2020-01-01"))
        except Exception:
            comm_date = date(2020, 1, 1)
        months_elapsed = max(0, (today.year - comm_date.year) * 12 + (today.month - comm_date.month))
        elapsed = min(months_elapsed, term_months)

        # Approximate remaining liability (proportional)
        remaining_months = max(0, term_months - elapsed)
        if term_months > 0:
            curr_liab = init_liab * (remaining_months / term_months)
        else:
            curr_liab = 0.0

        total_rou += curr_liab  # ROU ≈ liability for approximation
        total_liability += curr_liab

        ibr_weighted_sum += ibr * curr_liab
        ibr_weight_total += curr_liab

        years_remaining = remaining_months / 12.0
        term_weighted_sum += years_remaining * curr_liab
        term_weight_total += curr_liab

        try:
            exp_date = date.fromisoformat(lease.get("expiry_date", "2030-01-01"))
            if 0 < (exp_date - today).days <= 365:
                expiring_12mo += 1
        except Exception:
            pass

    weighted_avg_ibr = round(ibr_weighted_sum / ibr_weight_total, 2) if ibr_weight_total > 0 else 0.0
    weighted_avg_term = round(term_weighted_sum / term_weight_total, 1) if term_weight_total > 0 else 0.0

    ibrs_configured = list(set(
        COUNTRY_TO_JURISDICTION.get(lease.get("country", ""), "US")
        for lease in leases
    ))
    ibrs_in_db = set(v.get("jurisdiction") for v in db.get("ibrs", {}).values() if v.get("orgId") == org_id)
    ibrs_missing = [j for j in ibrs_configured if j not in ibrs_in_db]

    return {
        "total_leases": len(leases),
        "classified": {
            "operating": operating_count,
            "finance": finance_count,
            "short_term": short_term_count,
        },
        "total_rou_asset": round(total_rou, 2),
        "total_lease_liability": round(total_liability, 2),
        "weighted_avg_ibr": weighted_avg_ibr,
        "weighted_avg_remaining_term_years": weighted_avg_term,
        "leases_expiring_12mo": expiring_12mo,
        "standard": settings.get("standard", "ASC 842"),
        "reporting_currency": settings.get("reportingCurrency", "USD"),
        "ibrs_missing_jurisdictions": ibrs_missing,
        "periods_open": len([v for v in db.get("compliance_periods", {}).values() if v.get("status") == "open"]),
        "journal_pending": len([
            v for v in db.get("compliance_journal_status", {}).values()
            if v.get("status") == "pending"
        ]),
    }


# ── Journal Entries ───────────────────────────────────────────────────────────

@router.get("/compliance/journal-entries")
def get_journal_entries(period: str = "2026-05", standard: str = "asc842", org_id: str = Depends(current_org_id)):
    gl = db.get("compliance_gl_mapping", {}).get(org_id, GL_DEFAULTS)
    leases = scoped(db.get("leases", {}).values(), org_id)
    journal_status = db.get("compliance_journal_status", {})
    entries = []

    try:
        period_date = date.fromisoformat(f"{period}-01")
    except Exception:
        period_date = date.today().replace(day=1)

    period_end = (period_date.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

    total_debit = total_credit = 0.0

    for lease in leases:
        ibr = _get_ibr(lease.get("country", "USA"), org_id)
        lease_class = _classify_lease(lease, standard)

        try:
            comm_date = date.fromisoformat(lease.get("commencement_date", "2020-01-01"))
        except Exception:
            comm_date = date(2020, 1, 1)

        months_elapsed = (period_date.year - comm_date.year) * 12 + (period_date.month - comm_date.month)
        if months_elapsed < 0:
            continue  # Lease not yet commenced

        term_months = int(lease.get("lease_term_years", 5)) * 12
        if months_elapsed >= term_months:
            continue  # Lease expired

        monthly_rent = float(lease.get("monthly_rent", 0))
        esc_pct = _get_escalation_pct(lease.get("lease_id", ""))
        payments = _build_payment_stream(monthly_rent, term_months, esc_pct)
        monthly_rate = ibr / 100.0 / 12.0
        init_liab = _compute_pv(payments, monthly_rate)

        # Approximate current liability
        remaining = max(0, term_months - months_elapsed)
        curr_liab = init_liab * (remaining / term_months) if term_months > 0 else 0.0

        payment = payments[months_elapsed] if months_elapsed < len(payments) else 0.0
        interest = curr_liab * monthly_rate
        principal = max(0.0, payment - interest)
        rou_amort = init_liab / term_months if term_months > 0 else 0.0

        entry_key = f"{lease.get('lease_id')}_{period}"
        status_record = journal_status.get(entry_key, {})
        status = status_record.get("status", "pending")

        if lease_class == "finance" or standard == "ifrs16":
            lines = [
                {
                    "account": "Interest Expense",
                    "account_code": gl.get("interest_expense", "6120"),
                    "debit": round(interest, 2),
                    "credit": 0.0,
                    "description": f"Finance lease interest — {lease.get('store_name')}",
                },
                {
                    "account": "Amortisation Expense — ROU",
                    "account_code": gl.get("amortization_expense", "6130"),
                    "debit": round(rou_amort, 2),
                    "credit": 0.0,
                    "description": f"ROU asset amortisation — {lease.get('store_name')}",
                },
                {
                    "account": "Lease Liability — Current",
                    "account_code": gl.get("lease_liability_current", "2310"),
                    "debit": round(principal, 2),
                    "credit": 0.0,
                    "description": "Principal reduction",
                },
                {
                    "account": "Accumulated Amortisation — ROU",
                    "account_code": gl.get("accum_amort_rou", "1521"),
                    "debit": 0.0,
                    "credit": round(rou_amort, 2),
                    "description": "Contra ROU asset",
                },
                {
                    "account": "Accounts Payable",
                    "account_code": gl.get("accounts_payable", "2010"),
                    "debit": 0.0,
                    "credit": round(payment, 2),
                    "description": "Lease payment due",
                },
            ]
        else:
            # ASC 842 operating lease
            # Balanced entry: DR OpLeaseExp + DR LeaseLiab(principal) = CR AP(payment) + CR ROU(SL - interest)
            # ROU credit = straight_line - interest  (plugs to achieve straight-line P&L; can be negative = accretes)
            straight_line = sum(payments) / term_months if term_months > 0 else 0.0
            rou_cr = round(straight_line - interest, 2)  # positive = credit, negative = debit (early high-IBR periods)
            lines = [
                {
                    "account": "Operating Lease Expense",
                    "account_code": gl.get("operating_lease_expense", "6110"),
                    "debit": round(straight_line, 2),
                    "credit": 0.0,
                    "description": f"Straight-line operating lease cost — {lease.get('store_name')}",
                },
                {
                    "account": "ROU Asset",
                    "account_code": gl.get("rou_asset", "1520"),
                    "debit": max(0.0, -rou_cr),   # debit if rou_cr < 0 (ROU asset accretes)
                    "credit": max(0.0, rou_cr),    # credit if rou_cr > 0 (normal amortisation)
                    "description": "ROU asset amortisation (SL − interest)",
                },
                {
                    "account": "Lease Liability — Current",
                    "account_code": gl.get("lease_liability_current", "2310"),
                    "debit": round(principal, 2),
                    "credit": 0.0,
                    "description": "Principal reduction (payment − interest)",
                },
                {
                    "account": "Accounts Payable",
                    "account_code": gl.get("accounts_payable", "2010"),
                    "debit": 0.0,
                    "credit": round(payment, 2),
                    "description": "Lease payment due",
                },
            ]

        entry_debit = sum(l["debit"] for l in lines)
        entry_credit = sum(l["credit"] for l in lines)
        total_debit += entry_debit
        total_credit += entry_credit

        entries.append({
            "entry_id": entry_key,
            "lease_id": lease.get("lease_id"),
            "lease_name": lease.get("store_name"),
            "lease_class": lease_class,
            "entry_type": "monthly_accrual",
            "date": period_end.isoformat(),
            "period": period,
            "lines": lines,
            "status": status,
            "total_debit": round(entry_debit, 2),
            "total_credit": round(entry_credit, 2),
        })

    # Sort: pending first, then by lease name
    entries.sort(key=lambda x: (0 if x["status"] == "pending" else 1, x["lease_name"]))

    return {
        "period": period,
        "standard": standard,
        "entries": entries,
        "total_debit": round(total_debit, 2),
        "total_credit": round(total_credit, 2),
        "count_pending": sum(1 for e in entries if e["status"] == "pending"),
        "count_approved": sum(1 for e in entries if e["status"] == "approved"),
        "count_posted": sum(1 for e in entries if e["status"] == "posted"),
    }


@router.patch("/compliance/journal-entries/{entry_id}")
def update_journal_entry_status(entry_id: str, body: dict, org_id: str = Depends(current_org_id)):
    new_status = body.get("status", "approved")
    db["compliance_journal_status"][entry_id] = {
        "entry_id": entry_id,
        "status": new_status,
        "updated_by": "Sarah Chen",
        "updated_at": datetime.utcnow().isoformat(),
    }
    return {"entry_id": entry_id, "status": new_status}


@router.post("/compliance/journal-entries/approve-all")
def approve_all_journal_entries(body: dict, org_id: str = Depends(current_org_id)):
    period = body.get("period", "2026-05")
    leases = scoped(db.get("leases", {}).values(), org_id)
    count = 0
    for lease in leases:
        entry_key = f"{lease.get('lease_id')}_{period}"
        existing = db["compliance_journal_status"].get(entry_key, {})
        if existing.get("status", "pending") == "pending":
            db["compliance_journal_status"][entry_key] = {
                "entry_id": entry_key,
                "status": "approved",
                "updated_by": "Sarah Chen",
                "updated_at": datetime.utcnow().isoformat(),
            }
            count += 1
    return {"approved": count, "period": period}


# ── GL Mapping ────────────────────────────────────────────────────────────────

@router.get("/compliance/gl-mapping")
def get_gl_mapping(org_id: str = Depends(current_org_id)):
    return db.get("compliance_gl_mapping", {}).get(org_id, GL_DEFAULTS)


@router.post("/compliance/gl-mapping")
def save_gl_mapping(body: dict, org_id: str = Depends(current_org_id)):
    existing = db.get("compliance_gl_mapping", {}).get(org_id, GL_DEFAULTS.copy())
    updated = {**existing, **body}
    db["compliance_gl_mapping"][org_id] = updated
    return updated


# ── Disclosures ───────────────────────────────────────────────────────────────

@router.get("/compliance/disclosures")
def get_disclosures(period: str = "2026-Q1", standard: str = "asc842", org_id: str = Depends(current_org_id)):
    leases = scoped(db.get("leases", {}).values(), org_id)
    today = date.today()

    maturity = {"year_1": 0.0, "year_2": 0.0, "year_3": 0.0, "year_4": 0.0, "year_5": 0.0, "thereafter": 0.0}
    maturity_op = {k: 0.0 for k in maturity}
    maturity_fin = {k: 0.0 for k in maturity}

    op_expense = fin_amort = fin_interest = short_term_exp = 0.0
    rou_operating = rou_finance = 0.0
    liab_current = liab_noncurrent = 0.0
    ibr_op_sum = ibr_op_w = ibr_fin_sum = ibr_fin_w = 0.0
    term_op_sum = term_op_w = term_fin_sum = term_fin_w = 0.0
    cash_operating = cash_financing = 0.0

    for lease in leases:
        ibr = _get_ibr(lease.get("country", "USA"), org_id)
        lc = _classify_lease(lease, standard)
        monthly_rent = float(lease.get("monthly_rent", 0))
        term_months = int(lease.get("lease_term_years", 5)) * 12
        esc_pct = _get_escalation_pct(lease.get("lease_id", ""))

        try:
            comm_date = date.fromisoformat(lease.get("commencement_date", "2020-01-01"))
            exp_date = date.fromisoformat(lease.get("expiry_date", "2030-01-01"))
        except Exception:
            comm_date = date(2020, 1, 1)
            exp_date = date(2030, 1, 1)

        months_elapsed = max(0, (today.year - comm_date.year) * 12 + (today.month - comm_date.month))
        remaining_months = max(0, term_months - months_elapsed)

        if remaining_months == 0:
            continue

        payments = _build_payment_stream(monthly_rent, term_months, esc_pct)
        monthly_rate = ibr / 100.0 / 12.0
        future_payments = payments[months_elapsed:months_elapsed + remaining_months]

        init_liab = _compute_pv(payments, monthly_rate)
        curr_liab = _compute_pv(future_payments, monthly_rate)
        total_undiscounted_future = sum(future_payments)
        curr_rou = init_liab * (remaining_months / term_months)

        # Maturity buckets
        for month_idx, payment in enumerate(future_payments):
            yr = month_idx // 12 + 1
            key = f"year_{yr}" if yr <= 5 else "thereafter"
            maturity[key] += payment
            if lc == "operating":
                maturity_op[key] += payment
            elif lc == "finance":
                maturity_fin[key] += payment

        # Expense
        if lc == "short_term":
            short_term_exp += monthly_rent
        elif lc == "operating":
            straight_line = sum(payments) / term_months if term_months > 0 else 0.0
            op_expense += straight_line
            rou_operating += curr_rou
            liab_current += min(curr_liab, monthly_rent * 12)
            liab_noncurrent += max(0.0, curr_liab - monthly_rent * 12)
            cash_operating += monthly_rent
            ibr_op_sum += ibr * curr_liab
            ibr_op_w += curr_liab
            term_op_sum += (remaining_months / 12.0) * curr_liab
            term_op_w += curr_liab
        elif lc == "finance":
            fin_amort += init_liab / term_months if term_months > 0 else 0.0
            fin_interest += curr_liab * monthly_rate
            rou_finance += curr_rou
            liab_current += min(curr_liab, monthly_rent * 12)
            liab_noncurrent += max(0.0, curr_liab - monthly_rent * 12)
            cash_financing += monthly_rent
            ibr_fin_sum += ibr * curr_liab
            ibr_fin_w += curr_liab
            term_fin_sum += (remaining_months / 12.0) * curr_liab
            term_fin_w += curr_liab

    total_undiscounted = sum(maturity.values())
    total_pv = rou_operating + rou_finance
    discount = max(0.0, total_undiscounted - total_pv)

    return {
        "period": period,
        "standard": standard,
        "maturity_analysis": {
            **maturity,
            "total_undiscounted": round(total_undiscounted, 2),
            "discount": round(discount, 2),
            "present_value": round(total_pv, 2),
            "by_class": {
                "operating": {k: round(v, 2) for k, v in maturity_op.items()},
                "finance": {k: round(v, 2) for k, v in maturity_fin.items()},
            },
        },
        "expense_summary": {
            "operating_lease_expense": round(op_expense, 2),
            "finance_lease_amortization": round(fin_amort, 2),
            "finance_lease_interest": round(fin_interest, 2),
            "short_term_expense": round(short_term_exp, 2),
            "variable_lease_expense": 0.0,
            "total": round(op_expense + fin_amort + fin_interest + short_term_exp, 2),
        },
        "balance_sheet": {
            "rou_asset_operating": round(rou_operating, 2),
            "rou_asset_finance": round(rou_finance, 2),
            "rou_asset_total": round(rou_operating + rou_finance, 2),
            "lease_liability_current": round(liab_current, 2),
            "lease_liability_noncurrent": round(liab_noncurrent, 2),
            "lease_liability_total": round(liab_current + liab_noncurrent, 2),
        },
        "weighted_averages": {
            "remaining_term_operating": round(term_op_sum / term_op_w / 12.0, 1) if term_op_w > 0 else 0.0,
            "remaining_term_finance": round(term_fin_sum / term_fin_w / 12.0, 1) if term_fin_w > 0 else 0.0,
            "ibr_operating": round(ibr_op_sum / ibr_op_w, 2) if ibr_op_w > 0 else 0.0,
            "ibr_finance": round(ibr_fin_sum / ibr_fin_w, 2) if ibr_fin_w > 0 else 0.0,
        },
        "cash_flow": {
            "operating_cash_outflow": round(cash_operating, 2),
            "financing_cash_outflow": round(cash_financing, 2),
        },
    }


# ── IBR Sensitivity ───────────────────────────────────────────────────────────

@router.get("/compliance/sensitivity")
def get_ibr_sensitivity(lease_id: str = None, base_ibr: float = None, org_id: str = Depends(current_org_id)):
    if lease_id:
        lease = db.get("leases", {}).get(lease_id)
        if not lease:
            raise HTTPException(status_code=404, detail="Lease not found")
        require_same_org(lease, org_id)
        if base_ibr is None:
            base_ibr = _get_ibr(lease.get("country", "USA"), org_id)
        monthly_rent = float(lease.get("monthly_rent", 0))
        term_months = int(lease.get("lease_term_years", 5)) * 12
        esc_pct = _get_escalation_pct(lease_id)
        payments = _build_payment_stream(monthly_rent, term_months, esc_pct)

        def _pv_at_ibr(ibr_val):
            r = ibr_val / 100.0 / 12.0
            return _compute_pv(payments, r)

        scenarios = []
        for delta in [-1.0, -0.5, -0.25, 0.0, 0.25, 0.5, 1.0]:
            ibr_val = round(base_ibr + delta, 2)
            if ibr_val <= 0:
                continue
            pv = _pv_at_ibr(ibr_val)
            annual_expense = sum(payments) / (term_months / 12) if term_months > 0 else 0.0
            scenarios.append({
                "ibr": ibr_val,
                "delta": delta,
                "lease_liability": round(pv, 2),
                "rou_asset": round(pv, 2),
                "annual_expense": round(annual_expense, 2),
            })

        return {"lease_id": lease_id, "lease_name": lease.get("store_name"), "scenarios": scenarios}

    # Portfolio-wide sensitivity
    leases = scoped(db.get("leases", {}).values(), org_id)
    deltas = [-1.0, -0.5, -0.25, 0.0, 0.25, 0.5, 1.0]
    totals = {d: 0.0 for d in deltas}

    for lease in leases:
        ibr = _get_ibr(lease.get("country", "USA"), org_id)
        monthly_rent = float(lease.get("monthly_rent", 0))
        term_months = int(lease.get("lease_term_years", 5)) * 12
        esc_pct = _get_escalation_pct(lease.get("lease_id", ""))
        payments = _build_payment_stream(monthly_rent, term_months, esc_pct)
        for delta in deltas:
            ibr_val = max(0.1, ibr + delta)
            r = ibr_val / 100.0 / 12.0
            totals[delta] += _compute_pv(payments, r)

    base_total = totals[0.0]
    return {
        "portfolio": True,
        "scenarios": [
            {
                "delta": d,
                "total_lease_liability": round(totals[d], 2),
                "change_from_base": round(totals[d] - base_total, 2),
                "pct_change": round((totals[d] - base_total) / base_total * 100, 2) if base_total > 0 else 0.0,
            }
            for d in deltas
        ],
    }


# ── Notify waitlist ───────────────────────────────────────────────────────────

@router.post("/compliance/notify")
def join_waitlist(body: dict, org_id: str = Depends(current_org_id)):
    wl_id = str(uuid.uuid4())
    entry = {
        "waitlistId": wl_id,
        "email": body.get("email", ""),
        "outputType": body.get("outputType", ""),
        "createdAt": datetime.utcnow().isoformat(),
    }
    db["compliance_waitlist"][wl_id] = entry
    return {"success": True, "waitlistId": wl_id}


# ── Periods ───────────────────────────────────────────────────────────────────

@router.get("/compliance/periods")
def get_periods(org_id: str = Depends(current_org_id)):
    periods = list(db.get("compliance_periods", {}).values())
    return {"periods": sorted(periods, key=lambda x: x["period"], reverse=True)}


@router.patch("/compliance/periods/{period}")
def update_period(period: str, body: dict, org_id: str = Depends(current_org_id)):
    if period not in db.get("compliance_periods", {}):
        db["compliance_periods"][period] = {
            "period": period,
            "isLocked": False,
            "lockedBy": None,
            "lockedAt": None,
            "status": "open",
        }
    entry = db["compliance_periods"][period]
    is_locked = body.get("isLocked", entry["isLocked"])
    entry["isLocked"] = is_locked
    entry["status"] = "locked" if is_locked else "open"
    entry["lockedBy"] = "Sarah Chen" if is_locked else None
    entry["lockedAt"] = datetime.utcnow().isoformat() if is_locked else None
    return entry

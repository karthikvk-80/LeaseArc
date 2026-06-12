"""
administration_transformer.py

After extraction completes, reads the attrs + lease_record and populates:
  db["obligations"], db["renewals"], db["payments"], db["communications"]

Called from leases.py::_finalize_extraction (automatic) and ::save_lease (on user save).
Idempotent: removes the lease's existing extracted entries before re-inserting.
Does NOT touch mock/seed data for other leases.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

from app.mock_db import db


# ── helpers ──────────────────────────────────────────────────────────────────

_SKIP_VALUES = frozenset({"", "n/a", "lease is silent.", "not found", "none"})

_DATE_FMTS = (
    "%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d",
    "%d %B %Y", "%d %b %Y", "%B %d, %Y",
    "%d-%m-%Y", "%d.%m.%Y",
)


def _get_val(attr_map: dict, key: str) -> Optional[str]:
    a = attr_map.get(key)
    if not a:
        return None
    val = a.get("user_edited_value") or a.get("extracted_value")
    if val is None or str(val).strip().lower() in _SKIP_VALUES:
        return None
    return str(val).strip()


def _parse_date(val: str) -> Optional[str]:
    """Return YYYY-MM-DD or None."""
    if not val:
        return None
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(val.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    if re.match(r"^\d{4}-\d{2}-\d{2}", val):
        return val[:10]
    return None


def _parse_amount(val: str) -> Optional[float]:
    """Strip ₹/Rs./symbols/commas/text → float (handles Indian number format).

    Removes commas first (handles 96,59,962 → 9659962), then finds the first
    numeric sequence that may contain a single decimal point.  This avoids
    treating the period in 'Rs.' as a decimal separator.
    """
    if not val:
        return None
    no_commas = val.replace(",", "")
    # Find the first standalone number (digits, optional single decimal point)
    m = re.search(r"\d[\d.]*\d|\d", no_commas)
    if not m:
        return None
    candidate = m.group()
    # If there are multiple dots (e.g. "1.2.3"), keep only up to the first dot
    parts = candidate.split(".")
    if len(parts) > 2:
        candidate = parts[0] + "." + "".join(parts[1:])
    try:
        return float(candidate)
    except ValueError:
        return None


def _days_remaining(due_iso: str) -> int:
    try:
        return (date.fromisoformat(due_iso) - date.today()).days
    except Exception:
        return 0


def _obligation_status(days: int) -> str:
    if days < 0:
        return "overdue"
    if days <= 60:
        return "due"
    return "compliant"


def _renewal_stage(months: float) -> str:
    if months > 12:
        return "watching"
    if months > 6:
        return "analysis"
    if months > 3:
        return "negotiation"
    return "final"


def _renewal_decision(option_status: Optional[str]) -> str:
    if not option_status:
        return "undecided"
    s = option_status.lower()
    if s == "exercised":
        return "stay"
    if s in ("terminated", "not active"):
        return "relocate"
    return "undecided"


# ── main entry point ──────────────────────────────────────────────────────────

def populate_administration_from_extraction(
    lease_id: str,
    attrs: list,
    lease_record: dict,
    file_name: str,
) -> dict:
    """
    Populate db["obligations"], db["renewals"], db["payments"], db["communications"]
    from the extraction results.  Returns {"warnings": [...]}.
    """
    today = date.today()
    warnings: list[str] = []
    org_id = lease_record.get("org_id")

    # ── attribute lookup map ─────────────────────────────────────────────────
    attr_map: dict = {}
    for a in attrs:
        k = a.get("attribute_key")
        if k:
            attr_map[k] = a

    def gv(key: str) -> Optional[str]:
        return _get_val(attr_map, key)

    # ── lease-level basics ───────────────────────────────────────────────────
    store_name = lease_record.get("store_name") or "Unknown"
    store_code = lease_record.get("store_code") or ""
    currency   = lease_record.get("currency") or gv("currency") or "INR"

    expiry_raw = gv("current_expiration_date") or gv("expiry_date")
    expiry_iso = _parse_date(expiry_raw) if expiry_raw else None
    if not expiry_iso:
        warnings.append("WARN: current_expiration_date — missing; renewal and critical dates unavailable")

    # ── collect Expenses group slots ─────────────────────────────────────────
    expense_slots: list[dict] = []
    for i in range(20):
        rent_type = gv(f"expenses_{i}_rent_type")
        monthly   = gv(f"expenses_{i}_monthly_amount")
        if rent_type is None and monthly is None:
            break
        expense_slots.append({
            "slot":                   i,
            "rent_type":              rent_type,
            "monthly_amount":         monthly,
            "monthly_amount_per_sf":  gv(f"expenses_{i}_monthly_amount_per_sf"),
            "start_date":             gv(f"expenses_{i}_start_date"),
            "end_date":               gv(f"expenses_{i}_end_date"),
            "currency":               gv(f"expenses_{i}_currency") or currency,
            "payment_frequency":      gv(f"expenses_{i}_payment_frequency"),
            "on_day":                 gv(f"expenses_{i}_on_day"),
        })

    # ── collect Options group slots ──────────────────────────────────────────
    option_slots: list[dict] = []
    for i in range(20):
        opt_type = gv(f"options_{i}_option_type")
        if opt_type is None:
            break
        option_slots.append({
            "slot":                        i,
            "option_type":                 opt_type,
            "option_status":               gv(f"options_{i}_option_status"),
            "option_effective_date":       gv(f"options_{i}_option_effective_date"),
            "option_end_date":             gv(f"options_{i}_option_end_date"),
            "option_latest_notice":        gv(f"options_{i}_option_latest_notice_deadline"),
            "options_comments":            gv(f"options_{i}_options_comments"),
        })

    # ── collect Security Deposit group slots (+ flat fallback) ───────────────
    security_deposit_slots: list[dict] = []
    for i in range(10):
        sd_type = gv(f"security_deposit_{i}_security_deposit_type")
        if sd_type is None:
            break
        security_deposit_slots.append({
            "type":     sd_type,
            "amount":   gv(f"security_deposit_{i}_security_deposit_amount"),
            "currency": gv(f"security_deposit_{i}_security_deposit_currency") or currency,
            "comments": gv(f"security_deposit_{i}_security_deposit_comments"),
        })
    if not security_deposit_slots:
        flat_sd = gv("security_deposit_type")
        if flat_sd:
            security_deposit_slots.append({
                "type":     flat_sd,
                "amount":   gv("security_deposit"),
                "currency": gv("security_deposit_currency") or currency,
                "comments": None,
            })

    # warn about key missing fields
    for key, label in [
        ("current_expiration_date", "lease expiry"),
        ("base_rent_monthly",       "monthly rent"),
    ]:
        if gv(key) is None:
            warnings.append(f"WARN: {key} — missing or N/A ({label})")

    # also warn for any attribute present but skipped
    for a in attrs:
        k = a.get("attribute_key")
        if not k:
            continue
        val = a.get("user_edited_value") or a.get("extracted_value")
        if val is None or str(val).strip().lower() in _SKIP_VALUES:
            if a.get("is_key_field"):
                warnings.append(f"WARN: {k} — skipped (null/N/A/Lease is silent)")

    # ====================================================================== #
    # helpers to remove old extracted entries (preserves seeded data)
    # ====================================================================== #
    def _remove_for_lease(table: str):
        for k in list(db[table].keys()):
            if db[table][k].get("leaseId") == lease_id:
                del db[table][k]

    def _remove_comms_for_lease():
        for k in list(db["communications"].keys()):
            c = db["communications"][k]
            if c.get("lease_id") == lease_id and c.get("noticeStatus") == "draft":
                del db["communications"][k]

    # ====================================================================== #
    # 1. obligations
    # ====================================================================== #
    _remove_for_lease("obligations")

    def _add_obligation(obl_type: str, label: str, due_iso: str, notes: str):
        obl_id = str(uuid.uuid4())
        days   = _days_remaining(due_iso)
        db["obligations"][obl_id] = {
            "obligationId":  obl_id,
            "leaseId":       lease_id,
            "org_id":        org_id,
            "locationName":  store_name,
            "storeCode":     store_code,
            "type":          obl_type,
            "label":         label,
            "dueDate":       due_iso,
            "status":        _obligation_status(days),
            "lastActionDate": None,
            "notes":         (notes or "")[:1000],
        }

    insurance = gv("insurance_requirements")
    if insurance:
        _add_obligation(
            "insurance_certificate", "Insurance Certificate",
            (today + timedelta(days=60)).isoformat(),
            insurance,
        )

    pct_rent = gv("percentage_rent_payment")
    if pct_rent:
        _add_obligation(
            "percentage_rent_report", "% Rent Sales Report",
            (today + timedelta(days=30)).isoformat(),
            pct_rent,
        )

    for sd in security_deposit_slots:
        if "letter of credit" in (sd.get("type") or "").lower():
            _add_obligation(
                "letter_of_credit", "Letter of Credit Renewal",
                (today + timedelta(days=90)).isoformat(),
                sd.get("comments") or "Letter of Credit security deposit",
            )

    ti = gv("tenant_improvement_allowance") or gv("allowance_0_allowance_comments")
    if ti:
        ti_deadline_raw = gv("allowance_0_payment_deadline")
        ti_date = _parse_date(ti_deadline_raw) if ti_deadline_raw else None
        _add_obligation(
            "ti_allowance", "TI Allowance Claim",
            ti_date or (today + timedelta(days=60)).isoformat(),
            ti,
        )

    # ====================================================================== #
    # 2. renewal record
    # ====================================================================== #
    _remove_for_lease("renewals")

    if expiry_iso:
        expiry_d        = date.fromisoformat(expiry_iso)
        months_remaining = round((expiry_d - today).days / 30.44, 1)

        # Find first Renewal-type option
        first_renewal = next(
            (o for o in option_slots if (o.get("option_type") or "").lower() == "renewal"),
            None,
        )
        opt_status   = first_renewal.get("option_status")   if first_renewal else None
        opt_comments = first_renewal.get("options_comments") if first_renewal else None

        # Sum currently-active Fixed/Base Rent slots for currentRentMonthly.
        # A slot is active when its start_date <= today <= end_date (or dates absent).
        def _is_active_slot(e: dict) -> bool:
            s = _parse_date(e.get("start_date") or "")
            end = _parse_date(e.get("end_date") or "")
            iso_today = today.isoformat()
            if s and s > iso_today:
                return False
            if end and end < iso_today:
                return False
            return True

        active_fixed = [
            e for e in expense_slots
            if any(kw in (e.get("rent_type") or "").lower() for kw in ("fixed", "base"))
            and _is_active_slot(e)
        ]
        current_rent = sum(
            _parse_amount(e["monthly_amount"]) or 0
            for e in active_fixed
            if e.get("monthly_amount")
        )
        if not current_rent:
            # Fall back to first slot with any amount
            for e in expense_slots:
                amt = _parse_amount(e.get("monthly_amount") or "")
                if amt:
                    current_rent = amt
                    break
        if not current_rent:
            current_rent = lease_record.get("monthly_rent") or 0

        renewal_id = str(uuid.uuid4())
        db["renewals"][renewal_id] = {
            "renewalId":         renewal_id,
            "leaseId":           lease_id,
            "org_id":            org_id,
            "locationName":      store_name,
            "storeCode":         store_code,
            "expiryDate":        expiry_iso,
            "monthsRemaining":   months_remaining,
            "stage":             _renewal_stage(months_remaining),
            "decision":          _renewal_decision(opt_status),
            "decisionDate":      None,
            "notes":             (opt_comments or "")[:500],
            "currentRentMonthly": current_rent,
            "currency":          currency,
        }

    # ====================================================================== #
    # 3. payment schedule  (one record per expense slot)
    # ====================================================================== #
    _remove_for_lease("payments")

    for e in expense_slots:
        monthly = e.get("monthly_amount")
        if not monthly:
            continue
        amount = _parse_amount(monthly)
        if amount is None:
            continue

        start_iso = _parse_date(e["start_date"]) if e.get("start_date") else None
        end_iso   = _parse_date(e["end_date"])   if e.get("end_date")   else None
        period    = start_iso[:7] if start_iso else today.strftime("%Y-%m")

        amount_per_sf = _parse_amount(e.get("monthly_amount_per_sf") or "")

        on_day: Optional[int] = None
        on_day_raw = e.get("on_day") or ""
        m = re.search(r"\b(\d{1,2})\b", on_day_raw)
        if m:
            on_day = int(m.group(1))

        pay_id = str(uuid.uuid4())
        db["payments"][pay_id] = {
            "paymentId":        pay_id,
            "leaseId":          lease_id,
            "org_id":           org_id,
            "locationName":     store_name,
            "storeCode":        store_code,
            "period":           period,
            "rentType":         e.get("rent_type") or "Fixed Rent",
            "expectedAmount":   amount,
            "amountPerSF":      amount_per_sf,
            "scheduleStart":    start_iso,
            "scheduleEnd":      end_iso,
            "currency":         e.get("currency") or currency,
            "paymentFrequency": e.get("payment_frequency") or "Monthly",
            "onDay":            on_day,
            "actualAmount":     None,
            "variance":         None,
            "status":           "pending",
        }

    # ====================================================================== #
    # 4. communication draft  (from notices clause)
    # ====================================================================== #
    notices = gv("notices")
    if notices:
        _remove_comms_for_lease()
        comm_id = str(uuid.uuid4())
        db["communications"][comm_id] = {
            "comm_id":      comm_id,
            "lease_id":     lease_id,
            "org_id":       org_id,
            "type":         "formal_notice",
            "direction":    "outbound",
            "subject":      f"Lease Notice — {store_name}",
            "body":         notices[:2000],
            "sent_by":      "user-001",
            "sent_at":      today.isoformat(),
            "noticeStatus": "draft",
            "linked_clause": None,
        }

    return {"warnings": warnings}

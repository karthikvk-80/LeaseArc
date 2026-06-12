from app.mock_db import db
from datetime import date, timedelta
import uuid


def _get_attr(attrs: list, key: str):
    for a in attrs:
        if a.get("attribute_key") == key:
            val = a.get("user_edited_value") or a.get("extracted_value")
            return val
    return None


def _period_status(start: date, end: date, today: date) -> str:
    if end < today:
        return "past"
    if start <= today <= end:
        return "current"
    return "upcoming"


def seed_rent_schedules():
    if db["rent_schedules"]:
        return  # idempotent

    today = date.today()

    for lease_id, lease in db["leases"].items():
        attrs = db["attributes"].get(lease_id, [])

        # Pull key scalars
        try:
            monthly_rent = float(lease.get("monthly_rent", 0) or 0)
        except (TypeError, ValueError):
            monthly_rent = 0.0

        try:
            escalation_rate = float(_get_attr(attrs, "escalation_rate_pct") or 3.0)
        except (TypeError, ValueError):
            escalation_rate = 3.0

        escalation_type = str(_get_attr(attrs, "escalation_type") or "Fixed")

        try:
            review_freq = int(float(_get_attr(attrs, "rent_review_frequency_years") or 1))
        except (TypeError, ValueError):
            review_freq = 1
        if review_freq < 1:
            review_freq = 1

        # Parse dates
        try:
            commence = date.fromisoformat(str(lease.get("commencement_date", "2020-01-01"))[:10])
        except ValueError:
            commence = date(2020, 1, 1)
        try:
            expiry = date.fromisoformat(str(lease.get("expiry_date", "2030-01-01"))[:10])
        except ValueError:
            expiry = date(2030, 1, 1)

        schedule = []
        period_num = 1
        period_start = commence
        current_monthly = monthly_rent

        while period_start < expiry:
            # Advance end date by review_freq years
            end_year = period_start.year + review_freq
            try:
                period_end = period_start.replace(year=end_year) - timedelta(days=1)
            except ValueError:
                period_end = period_start.replace(year=end_year, day=28) - timedelta(days=1)

            if period_end > expiry:
                period_end = expiry

            change_pct = 0.0 if period_num == 1 else escalation_rate
            annual_rent = round(current_monthly * 12, 2)
            status = _period_status(period_start, period_end, today)

            cam_estimate = round(current_monthly * 0.08, 2)
            property_tax_estimate = round(current_monthly * 0.03, 2)
            insurance_estimate = round(current_monthly * 0.015, 2)
            total_expected = round(current_monthly + cam_estimate + property_tax_estimate + insurance_estimate, 2)

            try:
                payment_due_day = int(float(_get_attr(attrs, "payment_due_day") or 1))
            except (TypeError, ValueError):
                payment_due_day = 1

            erp_cost_centre = _get_attr(attrs, "erp_cost_centre") or ""
            erp_gl_account = _get_attr(attrs, "erp_gl_account") or ""

            schedule.append({
                "schedule_id": str(uuid.uuid4()),
                "lease_id": lease_id,
                "org_id": lease.get("org_id"),
                "period_number": period_num,
                "start_date": period_start.isoformat(),
                "end_date": period_end.isoformat(),
                "monthly_rent": round(current_monthly, 2),
                "annual_rent": annual_rent,
                "change_pct": round(change_pct, 2),
                "review_type": escalation_type,
                "status": status,
                "cam_estimate": cam_estimate,
                "property_tax_estimate": property_tax_estimate,
                "insurance_estimate": insurance_estimate,
                "total_expected": total_expected,
                "payment_due_day": payment_due_day,
                "erp_cost_centre": erp_cost_centre,
                "erp_gl_account": erp_gl_account,
            })

            # Advance for next period
            period_start = period_end + timedelta(days=1)
            current_monthly = round(current_monthly * (1 + escalation_rate / 100), 2)
            period_num += 1

        db["rent_schedules"][lease_id] = schedule

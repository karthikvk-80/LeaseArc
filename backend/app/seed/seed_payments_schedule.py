from app.mock_db import db
from datetime import date, timedelta
import uuid
import random

random.seed(42)

MONTHS_TO_SEED = 6  # 3 prior + current + 2 forward


def _months_around_today(n_prior: int = 3, n_forward: int = 2):
    today = date.today()
    periods = []
    for offset in range(-n_prior, n_forward + 1):
        month = today.month + offset
        year = today.year
        while month <= 0:
            month += 12
            year -= 1
        while month > 12:
            month -= 12
            year += 1
        periods.append(f"{year}-{month:02d}")
    return periods


def seed_payment_schedules():
    if db["payment_schedules"]:
        return

    # Payment schedules stay Xtract-only for now — same as CAM/disputes.
    xtract_org_id = db.get("org_ids", {}).get("Xtract.io")
    leases = [l for l in db["leases"].values() if l.get("org_id") == xtract_org_id][:20]
    periods = _months_around_today(3, 2)

    for lease in leases:
        lease_id = lease["lease_id"]
        monthly_rent = float(lease.get("monthly_rent", 5000) or 5000)
        cam = round(monthly_rent * 0.08, 2)
        tax = round(monthly_rent * 0.03, 2)
        ins = round(monthly_rent * 0.015, 2)
        total = round(monthly_rent + cam + tax + ins, 2)
        currency = lease.get("currency", "USD")

        for period in periods:
            schedule_id = str(uuid.uuid4())
            lines = [
                {
                    "line_id": str(uuid.uuid4()),
                    "schedule_id": schedule_id,
                    "line_item_type": "base_rent",
                    "expected_amount": monthly_rent,
                    "calculation_basis": f"Base rent per lease terms",
                    "escalation_applied": False,
                },
                {
                    "line_id": str(uuid.uuid4()),
                    "schedule_id": schedule_id,
                    "line_item_type": "cam",
                    "expected_amount": cam,
                    "calculation_basis": f"8% of base rent (${monthly_rent:,.0f})",
                    "escalation_applied": False,
                },
                {
                    "line_id": str(uuid.uuid4()),
                    "schedule_id": schedule_id,
                    "line_item_type": "property_tax",
                    "expected_amount": tax,
                    "calculation_basis": f"3% of base rent (${monthly_rent:,.0f})",
                    "escalation_applied": False,
                },
                {
                    "line_id": str(uuid.uuid4()),
                    "schedule_id": schedule_id,
                    "line_item_type": "insurance",
                    "expected_amount": ins,
                    "calculation_basis": f"1.5% of base rent (${monthly_rent:,.0f})",
                    "escalation_applied": False,
                },
            ]
            for line in lines:
                line["org_id"] = xtract_org_id
            db["payment_schedule_lines"][schedule_id] = lines
            db["payment_schedules"][schedule_id] = {
                "schedule_id": schedule_id,
                "lease_id": lease_id,
                "org_id": xtract_org_id,
                "location_name": lease.get("store_name", ""),
                "store_code": lease.get("store_code", ""),
                "period": period,
                "total_expected": total,
                "currency": currency,
                "status": "published",
                "generated_at": f"{period}-01T00:00:00",
                "lines": lines,
            }


def seed_payment_actuals():
    if db["payment_actuals"]:
        return

    today = date.today()
    current_period = today.strftime("%Y-%m")
    prior_periods = [p for p in _months_around_today(3, 0) if p != current_period]

    schedules_by_lease_period: dict = {}
    for s in db["payment_schedules"].values():
        key = (s["lease_id"], s["period"])
        schedules_by_lease_period[key] = s

    statuses_pool = (["on_time"] * 18) + (["overbilled"] * 2) + (["underbilled"] * 1) + (["late"] * 1)

    for (lease_id, period), schedule in schedules_by_lease_period.items():
        if period not in prior_periods and period != current_period:
            continue
        if period == current_period and random.random() < 0.15:
            continue  # 15% pending for current month

        status = random.choice(statuses_pool)
        expected = schedule["total_expected"]

        if status == "overbilled":
            actual = round(expected * random.uniform(1.03, 1.12), 2)
        elif status == "underbilled":
            actual = round(expected * random.uniform(0.88, 0.97), 2)
        elif status == "late":
            actual = expected
        else:
            actual = expected

        year, month = [int(x) for x in period.split("-")]
        pay_day = random.randint(1, 5) if status != "late" else random.randint(8, 15)
        try:
            payment_date = date(year, month, pay_day).isoformat()
        except ValueError:
            payment_date = date(year, month, 1).isoformat()

        actual_id = str(uuid.uuid4())
        db["payment_actuals"][actual_id] = {
            "actual_id": actual_id,
            "lease_id": lease_id,
            "org_id": schedule.get("org_id"),
            "location_name": schedule["location_name"],
            "store_code": schedule["store_code"],
            "period": period,
            "line_item_type": "total",
            "actual_amount": actual,
            "payment_date": payment_date,
            "source": "csv",
            "reference": f"TXN-{random.randint(100000, 999999)}",
            "imported_by": "finance@leasearc.com",
            "imported_at": f"{period}-06T09:00:00",
            "schedule_id": [sid for sid, s in db["payment_schedules"].items() if s["lease_id"] == lease_id and s["period"] == period][0] if any(True for s in db["payment_schedules"].values() if s["lease_id"] == lease_id and s["period"] == period) else "",
            "expected_amount": expected,
            "status": status,
            "currency": schedule["currency"],
        }


def seed_payment_matches():
    if db["payment_matches"]:
        return

    for actual_id, actual in db["payment_actuals"].items():
        variance = round(actual["actual_amount"] - actual["expected_amount"], 2)
        variance_pct = round((variance / actual["expected_amount"]) * 100, 2) if actual["expected_amount"] else 0

        status = actual["status"]
        match_id = str(uuid.uuid4())
        db["payment_matches"][match_id] = {
            "match_id": match_id,
            "lease_id": actual["lease_id"],
            "org_id": actual.get("org_id"),
            "location_name": actual["location_name"],
            "store_code": actual["store_code"],
            "period": actual["period"],
            "line_item_type": actual["line_item_type"],
            "expected_amount": actual["expected_amount"],
            "actual_amount": actual["actual_amount"],
            "variance": variance,
            "variance_pct": variance_pct,
            "status": status,
            "match_confidence": "exact",
            "payment_date": actual["payment_date"],
            "requires_confirmation": False,
            "is_confirmed": True,
            "currency": actual.get("currency", "USD"),
            "actual_id": actual_id,
        }


def seed_csv_profiles():
    if db["csv_profiles"]:
        return

    db["csv_profiles"]["p1"] = {
        "profile_id": "p1",
        "profile_name": "SAP Payment Export",
        "source_system": "SAP S/4HANA",
        "column_mapping": {
            "BUKRS": "lease_id",
            "KOSTL": "location_code",
            "BUDAT": "payment_date",
            "WRBTR": "actual_amount",
            "GJAHR": "period",
            "BELNR": "reference",
        },
        "created_at": "2026-01-15T10:00:00",
    }
    db["csv_profiles"]["p2"] = {
        "profile_id": "p2",
        "profile_name": "QuickBooks AR Report",
        "source_system": "QuickBooks Online",
        "column_mapping": {
            "Transaction Date": "payment_date",
            "Amount": "actual_amount",
            "Memo": "reference",
            "Name": "location_code",
        },
        "created_at": "2026-02-03T14:30:00",
    }


def seed_import_history():
    if db["import_history"]:
        return

    history = [
        {
            "import_id": "imp-001",
            "period": "2026-02",
            "rows_processed": 48,
            "matched": 46,
            "variances": 2,
            "unmatched": 0,
            "errors": 0,
            "match_rate": 96,
            "processing_time_ms": 312,
            "imported_at": "2026-02-06T09:14:22",
            "imported_by": "finance@leasearc.com",
            "status": "complete",
        },
        {
            "import_id": "imp-002",
            "period": "2026-03",
            "rows_processed": 50,
            "matched": 49,
            "variances": 1,
            "unmatched": 0,
            "errors": 0,
            "match_rate": 98,
            "processing_time_ms": 287,
            "imported_at": "2026-03-05T10:02:11",
            "imported_by": "finance@leasearc.com",
            "status": "complete",
        },
        {
            "import_id": "imp-003",
            "period": "2026-04",
            "rows_processed": 47,
            "matched": 44,
            "variances": 5,
            "unmatched": 2,
            "errors": 1,
            "match_rate": 94,
            "processing_time_ms": 341,
            "imported_at": "2026-04-07T08:45:33",
            "imported_by": "finance@leasearc.com",
            "status": "complete",
        },
    ]
    for h in history:
        db["import_history"][h["import_id"]] = h


def seed_all():
    seed_payment_schedules()
    seed_payment_actuals()
    seed_payment_matches()
    seed_csv_profiles()
    seed_import_history()

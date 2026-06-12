from fastapi import APIRouter, Depends
from app.mock_db import db
from app.services.org_isolation import current_org_id, scoped
from datetime import date, timedelta
import random

router = APIRouter()


@router.get("/summary")
def get_summary(org_id: str = Depends(current_org_id)):
    leases = scoped(db["leases"].values(), org_id)
    active = [l for l in leases if l["status"] in ("active", "expiring")]
    total_monthly_rent = sum(l.get("monthly_rent", 0) for l in active)

    today = date.today()
    critical_count = 0
    for l in leases:
        exp = l.get("expiry_date")
        if exp:
            try:
                d = date.fromisoformat(exp[:10])
                diff = (d - today).days
                if 0 <= diff <= 90:
                    critical_count += 1
            except Exception:
                pass

    open_disputes = len([
        d for d in db["disputes"].values()
        if d["status"] in ("open", "responded", "escalated")
        and d.get("org_id") == org_id
    ])

    return {
        "totalLeases": len(leases),
        "monthlyRent": round(total_monthly_rent, 2),
        "criticalDatesCount": critical_count,
        "openDisputes": open_disputes,
        "trends": {
            "leasesChange": "+3.2%",
            "rentChange": "+1.8%",
            "criticalDatesChange": "-2",
            "disputesChange": "+1",
        },
    }


@router.get("/critical-dates")
def get_critical_dates(org_id: str = Depends(current_org_id)):
    today = date.today()
    dates = []

    for lease in scoped(db["leases"].values(), org_id):
        exp = lease.get("expiry_date")
        if not exp:
            continue
        try:
            d = date.fromisoformat(exp[:10])
            days_remaining = (d - today).days
            if 0 <= days_remaining <= 90:
                loc = db["locations"].get(lease.get("location_id"), {})
                dates.append({
                    "dateId": f"cd-exp-{lease['lease_id'][:8]}",
                    "locationName": lease.get("store_name", loc.get("store_name", "Unknown")),
                    "leaseId": lease["lease_id"],
                    "dateType": "Lease Expiry",
                    "dueDate": exp,
                    "daysRemaining": days_remaining,
                })
        except Exception:
            pass

    # Also pull renewal deadlines from attributes (only for this org's leases)
    org_lease_ids = {l["lease_id"] for l in scoped(db["leases"].values(), org_id)}
    for lease_id, attrs in db["attributes"].items():
        if lease_id not in org_lease_ids:
            continue
        for attr in attrs:
            if attr["attribute_key"] == "renewal_deadline" and attr.get("extracted_value"):
                try:
                    d = date.fromisoformat(attr["extracted_value"][:10])
                    days_remaining = (d - today).days
                    if 0 <= days_remaining <= 90:
                        lease = db["leases"].get(lease_id, {})
                        dates.append({
                            "dateId": f"cd-ren-{lease_id[:8]}",
                            "locationName": lease.get("store_name", "Unknown"),
                            "leaseId": lease_id,
                            "dateType": "Renewal Deadline",
                            "dueDate": attr["extracted_value"],
                            "daysRemaining": days_remaining,
                        })
                except Exception:
                    pass

    dates.sort(key=lambda x: x["daysRemaining"])
    return {"dates": dates[:25]}


@router.get("/rent-chart")
def get_rent_chart(org_id: str = Depends(current_org_id)):
    today = date.today()
    active_leases = [l for l in scoped(db["leases"].values(), org_id) if l["status"] in ("active", "expiring")]
    base_rent = sum(l.get("monthly_rent", 0) for l in active_leases)

    months = []
    for offset in range(-3, 3):
        if offset < 0:
            m = today.month + offset
            y = today.year
            while m <= 0:
                m += 12
                y -= 1
        else:
            m = today.month + offset
            y = today.year
            while m > 12:
                m -= 12
                y += 1

        month_date = date(y, m, 1)
        months.append({
            "month": month_date.strftime("%b"),
            "year": y,
            "totalRent": round(base_rent * random.uniform(0.94, 1.06), 2),
            "leaseCount": len(active_leases),
        })

    return {"months": months}


@router.get("/negotiation-summary")
def get_negotiation_summary(org_id: str = Depends(current_org_id)):
    disputes = [d for d in db["disputes"].values() if d.get("org_id") == org_id]
    open_count = len([d for d in disputes if d["status"] == "open"])
    responded = len([d for d in disputes if d["status"] == "responded"])
    resolved = len([d for d in disputes if d["status"] == "resolved"])
    return {
        "open": open_count,
        "sentThisMonth": responded + 1,
        "resolvedThisMonth": resolved,
    }


@router.get("/expiry-chart")
def get_expiry_chart(org_id: str = Depends(current_org_id)):
    today = date.today()
    months = []

    for i in range(12):
        m = today.month + i
        y = today.year
        while m > 12:
            m -= 12
            y += 1
        month_start = date(y, m, 1)
        if m == 12:
            month_end = date(y + 1, 1, 1) - timedelta(days=1)
        else:
            month_end = date(y, m + 1, 1) - timedelta(days=1)

        count = 0
        for l in scoped(db["leases"].values(), org_id):
            exp = l.get("expiry_date")
            if exp:
                try:
                    d = date.fromisoformat(exp[:10])
                    if month_start <= d <= month_end:
                        count += 1
                except Exception:
                    pass

        months.append({
            "month": month_start.strftime("%b"),
            "year": y,
            "expiryCount": count,
        })

    return {"months": months}

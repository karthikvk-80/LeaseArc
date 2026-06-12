from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from app.mock_db import db
from app.services.org_isolation import current_org_id, require_same_org
from datetime import date
import uuid
import io
import csv as csv_module

router = APIRouter()


# ── Pydantic models ──────────────────────────────────────────────────────────

class PaymentStatusUpdate(BaseModel):
    status: str
    notes: Optional[str] = None


class MatchAction(BaseModel):
    notes: Optional[str] = None


class DisputeFromMatch(BaseModel):
    notes: Optional[str] = None


class CSVProfileCreate(BaseModel):
    profile_name: str
    source_system: str
    column_mapping: dict


# ── Helpers ──────────────────────────────────────────────────────────────────

def _period_schedules(period: str, org_id: str | None = None):
    return [s for s in db["payment_schedules"].values() if s["period"] == period and (org_id is None or s.get("org_id") == org_id)]


def _period_matches(period: str, org_id: str | None = None):
    return [m for m in db["payment_matches"].values() if m["period"] == period and (org_id is None or m.get("org_id") == org_id)]


def _payment_summary(period: str, org_id: str | None = None) -> dict:
    matches = _period_matches(period, org_id)
    schedules = _period_schedules(period, org_id)
    total_expected = sum(s["total_expected"] for s in schedules)
    total_actual = sum(m["actual_amount"] for m in matches)
    total_variance = round(total_actual - total_expected, 2)
    currency = schedules[0]["currency"] if schedules else "USD"
    return {
        "period": period,
        "totalExpected": round(total_expected, 2),
        "totalActual": round(total_actual, 2),
        "totalVariance": total_variance,
        "onTime": sum(1 for m in matches if m["status"] == "on_time"),
        "overbilled": sum(1 for m in matches if m["status"] == "overbilled"),
        "underbilled": sum(1 for m in matches if m["status"] == "underbilled"),
        "pending": len(schedules) - len(matches),
        "disputed": sum(1 for m in matches if m["status"] == "disputed"),
        "unmatched": 0,
        "currency": currency,
    }


def _generate_csv_content(period: str, fmt: str) -> str:
    schedules = _period_schedules(period)
    output = io.StringIO()

    if fmt == "sap":
        fieldnames = ["BUKRS", "KOSTL", "LIFNR", "WRBTR", "WAERS", "BUDAT", "GJAHR", "MONAT", "BELNR", "SGTXT"]
        writer = csv_module.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for s in schedules:
            y, m = s["period"].split("-")
            writer.writerow({
                "BUKRS": "1000",
                "KOSTL": s["store_code"],
                "LIFNR": s["lease_id"][:8].upper(),
                "WRBTR": f"{s['total_expected']:.2f}",
                "WAERS": s["currency"],
                "BUDAT": f"{s['period']}-01",
                "GJAHR": y,
                "MONAT": m,
                "BELNR": f"RE{uuid.uuid4().hex[:8].upper()}",
                "SGTXT": s["location_name"],
            })

    elif fmt == "oracle":
        fieldnames = ["Ledger", "Period Name", "Segment1", "Segment2", "Amount", "Currency", "Description", "Attribute1"]
        writer = csv_module.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for s in schedules:
            writer.writerow({
                "Ledger": "US_PRIMARY",
                "Period Name": s["period"],
                "Segment1": "6200",
                "Segment2": s["store_code"],
                "Amount": f"{s['total_expected']:.2f}",
                "Currency": s["currency"],
                "Description": f"Rent – {s['location_name']}",
                "Attribute1": s["lease_id"],
            })

    elif fmt == "quickbooks":
        fieldnames = ["Account", "Amount", "Memo", "Name", "Class", "Date"]
        writer = csv_module.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for s in schedules:
            writer.writerow({
                "Account": "Rent Expense",
                "Amount": f"{s['total_expected']:.2f}",
                "Memo": f"Rent {s['period']} – {s['location_name']}",
                "Name": s["location_name"],
                "Class": s["store_code"],
                "Date": f"{s['period']}-01",
            })

    elif fmt == "xero":
        fieldnames = ["ContactName", "InvoiceDate", "InvoiceDueDate", "Description", "Quantity", "UnitAmount", "AccountCode", "TaxType"]
        writer = csv_module.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for s in schedules:
            writer.writerow({
                "ContactName": s["location_name"],
                "InvoiceDate": f"{s['period']}-01",
                "InvoiceDueDate": f"{s['period']}-01",
                "Description": f"Monthly rent – {s['period']}",
                "Quantity": 1,
                "UnitAmount": f"{s['total_expected']:.2f}",
                "AccountCode": "4200",
                "TaxType": "NONE",
            })

    else:  # standard
        fieldnames = ["lease_id", "location_name", "store_code", "period", "line_item_type", "expected_amount", "currency", "effective_date"]
        writer = csv_module.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for s in schedules:
            for line in s.get("lines", []):
                writer.writerow({
                    "lease_id": s["lease_id"],
                    "location_name": s["location_name"],
                    "store_code": s["store_code"],
                    "period": s["period"],
                    "line_item_type": line["line_item_type"],
                    "expected_amount": f"{line['expected_amount']:.2f}",
                    "currency": s["currency"],
                    "effective_date": f"{s['period']}-01",
                })

    return output.getvalue()


# ── Payment schedule endpoints ────────────────────────────────────────────────

@router.get("/payment-schedule/{period}")
def get_payment_schedule(period: str, org_id: str = Depends(current_org_id)):
    schedules = _period_schedules(period, org_id)
    total_expected = sum(s["total_expected"] for s in schedules)
    currency = schedules[0]["currency"] if schedules else "USD"
    return {
        "period": period,
        "schedules": [
            {
                "scheduleId": s["schedule_id"],
                "leaseId": s["lease_id"],
                "locationName": s["location_name"],
                "storeCode": s["store_code"],
                "totalExpected": s["total_expected"],
                "currency": s["currency"],
                "status": s["status"],
                "generatedAt": s["generated_at"],
                "lines": [
                    {
                        "lineId": l["line_id"],
                        "lineItemType": l["line_item_type"],
                        "expectedAmount": l["expected_amount"],
                        "calculationBasis": l["calculation_basis"],
                        "escalationApplied": l["escalation_applied"],
                    }
                    for l in s.get("lines", [])
                ],
            }
            for s in schedules
        ],
        "summary": {
            "totalLeases": len(schedules),
            "totalExpected": round(total_expected, 2),
            "currency": currency,
        },
    }


@router.get("/payment-schedule/{period}/export")
def export_payment_schedule(period: str, format: str = "standard", org_id: str = Depends(current_org_id)):
    fmt = format.lower()
    content = _generate_csv_content(period, fmt)
    ext_map = {"quickbooks": "iif", "xero": "csv", "sap": "csv", "oracle": "csv", "standard": "csv"}
    ext = ext_map.get(fmt, "csv")
    filename = f"leasearc-payment-schedule-{period}-{fmt}.{ext}"
    return StreamingResponse(
        io.BytesIO(content.encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/payment-schedule/generate")
def generate_payment_schedule(period: str, org_id: str = Depends(current_org_id)):
    schedules = _period_schedules(period, org_id)
    total = sum(s["total_expected"] for s in schedules)
    return {
        "period": period,
        "totalLeases": len(schedules),
        "totalExpected": round(total, 2),
        "status": "generated",
    }


@router.get("/payment-schedule/{schedule_id}/lines")
def get_schedule_lines(schedule_id: str, org_id: str = Depends(current_org_id)):
    lines = db["payment_schedule_lines"].get(schedule_id, [])
    return {"lines": lines}


# ── Payment actuals / import ──────────────────────────────────────────────────

@router.post("/payments/import")
async def import_payments(file: UploadFile = File(None), period: str = Form(default=""), org_id: str = Depends(current_org_id)):
    today = date.today()
    if not period:
        period = today.strftime("%Y-%m")

    import_id = f"imp-{str(uuid.uuid4())[:8]}"
    rows = 47
    matched = 44
    variances = 5
    unmatched = 2
    errors = 1

    db["import_history"][import_id] = {
        "import_id": import_id,
        "period": period,
        "rows_processed": rows,
        "matched": matched,
        "variances": variances,
        "unmatched": unmatched,
        "errors": errors,
        "match_rate": round((matched / rows) * 100),
        "processing_time_ms": 341,
        "imported_at": today.isoformat() + "T10:00:00",
        "imported_by": "finance@leasearc.com",
        "status": "complete",
    }

    return {
        "importId": import_id,
        "period": period,
        "rowsProcessed": rows,
        "matched": matched,
        "variances": variances,
        "unmatched": unmatched,
        "errors": errors,
        "processingTimeMs": 341,
        "importedAt": today.isoformat() + "T10:00:00",
    }


@router.get("/payments/import/{import_id}")
def get_import_result(import_id: str, org_id: str = Depends(current_org_id)):
    result = db["import_history"].get(import_id)
    if not result:
        raise HTTPException(status_code=404, detail="Import not found")
    return result


@router.get("/payments/import-history")
def get_import_history(org_id: str = Depends(current_org_id)):
    history = sorted(db["import_history"].values(), key=lambda x: x["imported_at"], reverse=True)
    return {"history": history}


@router.get("/payments/summary/{period}")
def get_payment_summary(period: str, org_id: str = Depends(current_org_id)):
    return _payment_summary(period, org_id)


@router.patch("/payments/{actual_id}/status")
def update_payment_status(actual_id: str, body: PaymentStatusUpdate, org_id: str = Depends(current_org_id)):
    actual = db["payment_actuals"].get(actual_id)
    if not actual:
        raise HTTPException(status_code=404, detail="Payment not found")
    if actual.get("org_id") and actual["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Payment not found")
    actual["status"] = body.status
    return {"ok": True}


# ── CSV profiles ──────────────────────────────────────────────────────────────

@router.get("/payments/csv-profiles")
def list_csv_profiles(org_id: str = Depends(current_org_id)):
    return {"profiles": list(db["csv_profiles"].values())}


@router.post("/payments/csv-profiles")
def create_csv_profile(body: CSVProfileCreate, org_id: str = Depends(current_org_id)):
    profile_id = str(uuid.uuid4())
    profile = {
        "profile_id": profile_id,
        "profile_name": body.profile_name,
        "source_system": body.source_system,
        "column_mapping": body.column_mapping,
        "created_at": date.today().isoformat() + "T00:00:00",
    }
    db["csv_profiles"][profile_id] = profile
    return profile


# ── Payment matches ───────────────────────────────────────────────────────────

@router.get("/payments/matches/{period}")
def get_payment_matches(period: str, org_id: str = Depends(current_org_id)):
    matches = _period_matches(period, org_id)
    return {
        "period": period,
        "matches": [
            {
                "matchId": m["match_id"],
                "leaseId": m["lease_id"],
                "locationName": m["location_name"],
                "storeCode": m["store_code"],
                "period": m["period"],
                "lineItemType": m["line_item_type"],
                "expectedAmount": m["expected_amount"],
                "actualAmount": m["actual_amount"],
                "variance": m["variance"],
                "variancePct": m["variance_pct"],
                "status": m["status"],
                "matchConfidence": m["match_confidence"],
                "paymentDate": m["payment_date"],
                "requiresConfirmation": m["requires_confirmation"],
                "isConfirmed": m["is_confirmed"],
                "currency": m.get("currency", "USD"),
            }
            for m in matches
        ],
    }


@router.patch("/payments/matches/{match_id}/confirm")
def confirm_match(match_id: str, body: MatchAction, org_id: str = Depends(current_org_id)):
    m = db["payment_matches"].get(match_id)
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")
    if m.get("org_id") and m["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Match not found")
    m["is_confirmed"] = True
    m["requires_confirmation"] = False
    return {"ok": True}


@router.patch("/payments/matches/{match_id}/reject")
def reject_match(match_id: str, body: MatchAction, org_id: str = Depends(current_org_id)):
    m = db["payment_matches"].get(match_id)
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")
    if m.get("org_id") and m["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Match not found")
    m["status"] = "unmatched"
    m["is_confirmed"] = False
    return {"ok": True}


@router.post("/payments/matches/{match_id}/dispute")
def dispute_match(match_id: str, body: DisputeFromMatch, org_id: str = Depends(current_org_id)):
    m = db["payment_matches"].get(match_id)
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")
    if m.get("org_id") and m["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Match not found")
    m["status"] = "disputed"
    dispute_id = str(uuid.uuid4())
    return {"ok": True, "disputeId": dispute_id}


# ── v2 stubs ──────────────────────────────────────────────────────────────────

@router.get("/v2/payment-schedule/{period}")
def v2_get_schedule(period: str, org_id: str = Depends(current_org_id)):
    raise HTTPException(status_code=501, detail="Live API sync is coming in v2. Join the waitlist in Settings → Integrations.")


@router.post("/v2/payments/sync")
def v2_sync_payments(org_id: str = Depends(current_org_id)):
    raise HTTPException(status_code=501, detail="Live API sync is coming in v2. Join the waitlist in Settings → Integrations.")

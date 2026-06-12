from fastapi import APIRouter, HTTPException, Request, Depends
from app.mock_db import db
from app.services.task_emitter import emit_task
from app.services.org_isolation import current_org_id, require_admin, scoped, require_same_org
import uuid
import random
from datetime import datetime, timedelta

router = APIRouter()
random.seed(66)

# ── Anomaly Radar ─────────────────────────────────────────────────────────────

@router.get("/intelligence/anomalies")
def get_anomalies(status: str | None = None, priority: str | None = None, org_id: str = Depends(current_org_id)):
    anomalies = scoped(db.get("anomalies", {}).values(), org_id, lease_field="leaseId")
    if status:
        anomalies = [a for a in anomalies if a["status"] == status]
    if priority:
        anomalies = [a for a in anomalies if a["priority"] == priority]
    anomalies.sort(key=lambda x: ({"high": 0, "medium": 1, "low": 2}[x["priority"]], x["detectedAt"]))

    high = sum(1 for a in anomalies if a["priority"] == "high")
    medium = sum(1 for a in anomalies if a["priority"] == "medium")
    low = sum(1 for a in anomalies if a["priority"] == "low")

    last_scan = (datetime.utcnow() - timedelta(hours=6)).isoformat()
    next_scan = datetime.utcnow().replace(hour=2, minute=0, second=0, microsecond=0)
    if next_scan < datetime.utcnow():
        next_scan = next_scan + timedelta(days=1)

    return {
        "anomalies": anomalies,
        "lastScanAt": last_scan,
        "nextScanAt": next_scan.isoformat(),
        "counts": {"high": high, "medium": medium, "low": low, "total": len(anomalies)},
    }


@router.get("/intelligence/anomalies/{anomaly_id}")
def get_anomaly(anomaly_id: str, org_id: str = Depends(current_org_id)):
    a = db.get("anomalies", {}).get(anomaly_id)
    if not a:
        raise HTTPException(404, "Anomaly not found")
    require_same_org(a, org_id)
    return a


@router.post("/intelligence/anomalies/{anomaly_id}/dismiss")
def dismiss_anomaly(anomaly_id: str, body: dict = {}, org_id: str = Depends(current_org_id)):
    a = db.get("anomalies", {}).get(anomaly_id)
    if not a:
        raise HTTPException(404, "Anomaly not found")
    require_same_org(a, org_id)
    a["status"] = "dismissed"
    a["actionedBy"] = "Sarah Chen"
    a["actionedAt"] = datetime.utcnow().isoformat()
    return a


@router.post("/intelligence/anomalies/{anomaly_id}/action")
def action_anomaly(anomaly_id: str, body: dict = {}, org_id: str = Depends(current_org_id)):
    a = db.get("anomalies", {}).get(anomaly_id)
    if not a:
        raise HTTPException(404, "Anomaly not found")
    require_same_org(a, org_id)
    a["status"] = "actioned"
    a["actionedBy"] = "Sarah Chen"
    a["actionedAt"] = datetime.utcnow().isoformat()
    return a


@router.post("/intelligence/scan")
def trigger_scan(org_id: str = Depends(current_org_id)):
    job_id = str(uuid.uuid4())
    # Workflow 7 — surface recurring overbill pattern as a task when scan runs
    anomalies = scoped(db.get("anomalies", {}).values(), org_id)
    for a in anomalies:
        if a.get("anomalyType") == "cam_overbill" and a.get("status") == "open":
            emit_task(
                title=f"Recurring CAM overbill detected — {a.get('landlordName', 'landlord')} ({a.get('locationName', 'location')}). Initiate dispute review.",
                source="intelligence",
                action_type="recurring_overbill_review",
                assignee_role="re_director",
                priority="high",
                watched_by_role="finance",
                due_days=14,
            )
            break  # one task per scan cycle
    return {"jobId": job_id, "message": "Anomaly scan triggered. Results available in ~2 minutes.", "startedAt": datetime.utcnow().isoformat()}


@router.get("/intelligence/digest")
def get_digest(org_id: str = Depends(current_org_id)):
    anomalies = scoped(db.get("anomalies", {}).values(), org_id)
    top5 = sorted(anomalies, key=lambda x: ({"high": 0, "medium": 1, "low": 2}[x["priority"]], x["detectedAt"]))[:5]
    return {
        "weekOf": (datetime.utcnow() - timedelta(days=datetime.utcnow().weekday())).strftime("%Y-%m-%d"),
        "topAnomalies": top5,
        "totalDetected": len(anomalies),
        "totalActioned": sum(1 for a in anomalies if a["status"] == "actioned"),
    }


# ── Ask Portfolio ─────────────────────────────────────────────────────────────

SUGGESTED_QUESTIONS = [
    "Which 5 locations had the highest CAM increase this year?",
    "Show all leases expiring in 6 months with no renewal decision",
    "Which landlords have we disputed with more than twice?",
    "What is total rent exposure in the UK in GBP?",
    "Which leases have a break clause exercisable in the next 12 months?",
    "What is our total CAM overcharge recovered year-to-date?",
]

MOCK_ANSWER_TEMPLATE = {
    "answer": "Based on your portfolio data, I found relevant information across multiple lease records. This is a demonstration response — connect an Anthropic API key in Settings → Intelligence to enable live natural-language queries against your actual lease data.",
    "sources": [],
    "confidence": "medium",
    "attributesUsed": ["monthly_rent", "expiry_date", "landlord_name"],
}


@router.post("/intelligence/query")
async def run_query(request: Request, body: dict, org_id: str = Depends(current_org_id)):
    question = body.get("question", "")
    org_data = body.get("orgData", {})
    api_key = body.get("apiKey")

    if api_key:
        # Live Anthropic API call
        import httpx
        leases = scoped(db.get("leases", {}).values(), org_id)
        context_leases = [
            {
                "leaseId": l.get("lease_id", ""),
                "locationName": l.get("store_name", ""),
                "storeCode": l.get("store_code", ""),
                "country": l.get("country", ""),
                "currency": l.get("currency", "USD"),
                "expiryDate": l.get("expiry_date", ""),
                "monthlyRent": l.get("monthly_rent", 0),
                "status": l.get("status", ""),
                "landlordName": l.get("landlord_name", ""),
            }
            for l in leases[:30]
        ]
        payload = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1000,
            "system": (
                "You are a lease portfolio analyst for a retail tenant. You have access to structured lease data "
                "for the user's organisation only. Answer questions accurately based only on the data provided. "
                "Always cite which leases or records your answer is based on. "
                "Return response as JSON with keys: answer (string), sources (array of {leaseId, locationName, relevantValue}), "
                "confidence ('high'|'medium'|'low'), attributesUsed (array of strings)."
            ),
            "messages": [{
                "role": "user",
                "content": f"Portfolio data: {context_leases}\n\nQuestion: {question}"
            }],
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
                import json as _json
                clean = text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
                result = _json.loads(clean)
        except Exception as e:
            result = {**MOCK_ANSWER_TEMPLATE, "answer": f"API error: {str(e)[:100]}. Showing mock response."}
    else:
        # Return matching mock query if available, else generic template
        queries = list(db.get("intelligence_queries", {}).values())
        queries = [q for q in queries if q.get("orgId") == org_id or q.get("org_id") == org_id]
        match = next((q for q in queries if question.lower()[:30] in q["question"].lower()), None)
        if match:
            result = {
                "answer": match["answer"],
                "sources": match["sources"],
                "confidence": match["confidence"],
                "attributesUsed": match["attributesUsed"],
            }
        else:
            result = {**MOCK_ANSWER_TEMPLATE}

    query_id = str(uuid.uuid4())
    record = {
        "queryId": query_id,
        "orgId": org_id,
        "question": question,
        "answer": result.get("answer", ""),
        "sources": result.get("sources", []),
        "confidence": result.get("confidence", "medium"),
        "attributesUsed": result.get("attributesUsed", []),
        "resultCount": len(result.get("sources", [])),
        "askedAt": datetime.utcnow().isoformat(),
    }
    db["intelligence_queries"][query_id] = record
    return {**record, "queryId": query_id, "suggestedQuestions": SUGGESTED_QUESTIONS}


@router.get("/intelligence/queries")
def get_query_history(org_id: str = Depends(current_org_id)):
    queries = scoped(db.get("intelligence_queries", {}).values(), org_id)
    queries.sort(key=lambda x: x.get("askedAt", ""), reverse=True)
    return {"queries": queries[:20], "suggestedQuestions": SUGGESTED_QUESTIONS}


@router.get("/intelligence/queries/{query_id}")
def get_query(query_id: str, org_id: str = Depends(current_org_id)):
    q = db.get("intelligence_queries", {}).get(query_id)
    if not q:
        raise HTTPException(404, "Query not found")
    require_same_org(q, org_id)
    return q


@router.post("/intelligence/queries/{query_id}/tasks")
def create_tasks_from_query(query_id: str, org_id: str = Depends(current_org_id)):
    q = db.get("intelligence_queries", {}).get(query_id)
    if not q:
        raise HTTPException(404, "Query not found")
    require_same_org(q, org_id)
    created = []
    for source in q.get("sources", []):
        task = emit_task(
            title=f"[Portfolio query] {q['question'][:60]} — {source.get('locationName', '')}",
            source="intelligence",
            action_type="portfolio_query_action",
            assignee_role="re_director",
            priority="medium",
            lease_id=source.get("leaseId"),
        )
        created.append(task["taskId"])
    return {"tasksCreated": len(created), "taskIds": created}


# ── Landlords ─────────────────────────────────────────────────────────────────

@router.get("/intelligence/landlords")
def list_landlords(country: str | None = None, risk_level: str | None = None, org_id: str = Depends(current_org_id)):
    landlords = scoped(db.get("landlords", {}).values(), org_id)
    if country:
        landlords = [ll for ll in landlords if ll.get("country") == country]
    if risk_level:
        landlords = [ll for ll in landlords if ll.get("riskLevel") == risk_level]
    landlords.sort(key=lambda x: x.get("riskScore", 0), reverse=True)
    return {"landlords": landlords, "total": len(landlords)}


@router.get("/intelligence/landlords/{landlord_id}")
def get_landlord(landlord_id: str, org_id: str = Depends(current_org_id)):
    ll = db.get("landlords", {}).get(landlord_id)
    if not ll:
        raise HTTPException(404, "Landlord not found")
    require_same_org(ll, org_id)
    return ll


@router.get("/intelligence/landlords/{landlord_id}/locations")
def get_landlord_locations(landlord_id: str, org_id: str = Depends(current_org_id)):
    leases = [l for l in scoped(db.get("leases", {}).values(), org_id) if l.get("landlord_id") == landlord_id]
    return {"leases": leases}


@router.get("/intelligence/landlords/{landlord_id}/disputes")
def get_landlord_disputes(landlord_id: str, org_id: str = Depends(current_org_id)):
    ll = db.get("landlords", {}).get(landlord_id)
    if not ll:
        raise HTTPException(404, "Landlord not found")
    require_same_org(ll, org_id)
    ll_lease_ids = {l["lease_id"] for l in scoped(db.get("leases", {}).values(), org_id) if l.get("landlord_id") == landlord_id}
    disputes = scoped([d for d in db.get("disputes", {}).values() if d.get("lease_id") in ll_lease_ids], org_id)
    return {"disputes": disputes}


@router.get("/intelligence/landlords/{landlord_id}/cam")
def get_landlord_cam(landlord_id: str, org_id: str = Depends(current_org_id)):
    ll_lease_ids = {l["lease_id"] for l in scoped(db.get("leases", {}).values(), org_id) if l.get("landlord_id") == landlord_id}
    stmts = scoped([s for s in db.get("cam_statements", {}).values() if s.get("lease_id") in ll_lease_ids], org_id)
    return {"camStatements": stmts}


@router.get("/intelligence/landlords/{landlord_id}/comms")
def get_landlord_comms(landlord_id: str, org_id: str = Depends(current_org_id)):
    ll_lease_ids = {l["lease_id"] for l in scoped(db.get("leases", {}).values(), org_id) if l.get("landlord_id") == landlord_id}
    comms = scoped([c for c in db.get("communications", {}).values() if c.get("lease_id") in ll_lease_ids], org_id)
    return {"communications": comms}


@router.get("/intelligence/landlords/{landlord_id}/amendments")
def get_landlord_amendments(landlord_id: str, org_id: str = Depends(current_org_id)):
    ll_lease_ids = {l["lease_id"] for l in scoped(db.get("leases", {}).values(), org_id) if l.get("landlord_id") == landlord_id}
    amends = scoped([a for a in db.get("amendments", {}).values() if a.get("lease_id") in ll_lease_ids], org_id)
    return {"amendments": amends}


@router.post("/intelligence/landlords/{landlord_id}/brief")
def export_brief(landlord_id: str, org_id: str = Depends(current_org_id)):
    raise HTTPException(501, detail="Negotiation brief PDF export is coming in v2. Your data is ready — PDF generation will be available soon.")


# ── Cost Forecast ─────────────────────────────────────────────────────────────

@router.post("/intelligence/forecast/calculate")
def calculate_forecast(body: dict, user=Depends(require_admin)):
    decisions = body.get("leaseDecisions", [])
    ibr_rate = 0.042  # default IBR

    year_results = {}
    for horizon in [1, 3, 5]:
        total_rent = 0.0
        total_cam = 0.0
        retained = 0
        exited = 0
        annual_breakdown = []

        for decision_item in decisions:
            decision = decision_item.get("decision", "renew_current")
            lease_id = decision_item.get("leaseId", "")
            lease = db.get("leases", {}).get(lease_id, {})
            monthly_rent = lease.get("monthly_rent", decision_item.get("currentRent", 0))

            if decision in ("break", "relocate"):
                exited += 1
                continue

            retained += 1
            if decision == "renew_market":
                monthly_rent = decision_item.get("marketRate", monthly_rent * 1.05)

            annual_rent = monthly_rent * 12
            for y in range(1, horizon + 1):
                escalated = annual_rent * (1.03 ** (y - 1))
                cam = escalated * 0.08
                total_rent += escalated
                total_cam += cam
                annual_breakdown.append({"year": y, "rent": round(escalated, 2), "cam": round(cam, 2)})

        # Simplified ROU asset estimate using PV of rent at IBR
        rou = round(total_rent / (1 + ibr_rate) ** (horizon / 2), 2) if total_rent else 0

        year_results[f"year{horizon}"] = {
            "totalRentLiability": round(total_rent, 2),
            "totalCamExposure": round(total_cam, 2),
            "rouAssetEstimate": rou,
            "locationsRetained": retained,
            "locationsExited": exited,
            "annualBreakdown": annual_breakdown[:horizon],
        }

    return {"results": year_results}


@router.post("/intelligence/forecast/save")
def save_scenario(body: dict, user=Depends(require_admin)):
    org_id = str(user.org_id)
    scenario_id = str(uuid.uuid4())
    scenario = {
        "scenarioId": scenario_id,
        "orgId": org_id,
        "name": body.get("name", f"Scenario {datetime.utcnow().strftime('%b %Y')}"),
        "createdBy": "Sarah Chen",
        "createdAt": datetime.utcnow().isoformat(),
        "leaseDecisions": body.get("leaseDecisions", []),
        "results": body.get("results", {}),
    }
    db["forecast_scenarios"][scenario_id] = scenario
    return {"scenarioId": scenario_id}


@router.get("/intelligence/forecast/scenarios")
def list_scenarios(user=Depends(require_admin)):
    org_id = str(user.org_id)
    scenarios = scoped(db.get("forecast_scenarios", {}).values(), org_id)
    scenarios.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
    return {"scenarios": scenarios}


@router.get("/intelligence/forecast/scenarios/{scenario_id}")
def get_scenario(scenario_id: str, user=Depends(require_admin)):
    org_id = str(user.org_id)
    s = db.get("forecast_scenarios", {}).get(scenario_id)
    if not s:
        raise HTTPException(404, "Scenario not found")
    require_same_org(s, org_id)
    return s


@router.delete("/intelligence/forecast/scenarios/{scenario_id}")
def delete_scenario(scenario_id: str, user=Depends(require_admin)):
    org_id = str(user.org_id)
    s = db.get("forecast_scenarios", {}).get(scenario_id)
    if not s:
        raise HTTPException(404, "Scenario not found")
    require_same_org(s, org_id)
    del db["forecast_scenarios"][scenario_id]
    return {"success": True}


# ── Org Settings + FX ────────────────────────────────────────────────────────

@router.get("/fx-rates")
def get_fx_rates(org_id: str = Depends(current_org_id)):
    from app.services.fx_rates import get_all_rates
    return get_all_rates()


@router.get("/organisations/settings")
def get_org_settings(user=Depends(require_admin)):
    org_id = str(user.org_id)
    return db.get("org_settings", {}).get(org_id, {
        "reportingCurrency": "USD",
        "defaultJurisdiction": "US",
        "complianceStandard": None,
    })


@router.patch("/organisations/settings")
def update_org_settings(body: dict, user=Depends(require_admin)):
    org_id = str(user.org_id)
    existing = db.get("org_settings", {}).get(org_id, {})
    updated = {**existing, **body, "orgId": org_id, "updatedAt": datetime.utcnow().isoformat()}
    db["org_settings"][org_id] = updated
    return updated

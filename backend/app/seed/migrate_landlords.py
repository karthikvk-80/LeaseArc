"""
Idempotent migration: promotes unique landlord_name strings in lease records
into proper Landlord entities and back-fills landlord_id on every lease.
"""
from app.mock_db import db
import uuid
import random
from datetime import datetime

random.seed(77)

LANDLORD_RISK_OVERRIDES = {
    "Acme Properties LLC":      {"riskScore": 78, "riskLevel": "high"},
    "Greenfield Realty":        {"riskScore": 34, "riskLevel": "low"},
    "Atlas Commercial":         {"riskScore": 55, "riskLevel": "medium"},
    "Meridian Estates":         {"riskScore": 22, "riskLevel": "low"},
    "Cushman & Wakefield":      {"riskScore": 30, "riskLevel": "low"},
    "CBRE Properties":          {"riskScore": 28, "riskLevel": "low"},
    "JLL Real Estate":          {"riskScore": 35, "riskLevel": "low"},
    "Prologis":                 {"riskScore": 42, "riskLevel": "medium"},
    "Simon Property Group":     {"riskScore": 50, "riskLevel": "medium"},
    "Brookfield Properties":    {"riskScore": 38, "riskLevel": "low"},
    "Unibail-Rodamco":          {"riskScore": 45, "riskLevel": "medium"},
    "Scentre Group":            {"riskScore": 33, "riskLevel": "low"},
    "Link REIT":                {"riskScore": 25, "riskLevel": "low"},
    "GIC Real Estate":          {"riskScore": 20, "riskLevel": "low"},
    "Hines Group":              {"riskScore": 40, "riskLevel": "medium"},
    "Oxford Properties":        {"riskScore": 31, "riskLevel": "low"},
    "Westfield Corp":           {"riskScore": 48, "riskLevel": "medium"},
    "Mall of America Realty":   {"riskScore": 55, "riskLevel": "medium"},
    "Prestige Estates Pvt Ltd": {"riskScore": 62, "riskLevel": "medium"},
}

COUNTRY_MAP = {
    "USA": "US", "UK": "GB", "AUS": "AU", "IND": "IN",
    "CAN": "CA", "GER": "DE", "SGP": "SG", "ARE": "AE",
}


def migrate_landlord_names_to_entities():
    """Idempotent — safe to re-run."""
    if db.get("landlords"):
        # Already migrated — just ensure landlord_id is set
        _backfill_ids()
        return

    seen: dict[tuple[str, str], str] = {}  # (org_id, name) -> landlord_id

    for lease in db.get("leases", {}).values():
        lease_org_id = lease.get("org_id") or ""
        name = lease.get("landlord_name", "Unknown Landlord")
        country_raw = lease.get("country", "US")
        country = COUNTRY_MAP.get(country_raw, country_raw)

        key = (lease_org_id, name)
        if key not in seen:
            ll_id = str(uuid.uuid4())
            overrides = LANDLORD_RISK_OVERRIDES.get(name, {})
            risk_score = overrides.get("riskScore", random.randint(15, 75))
            risk_level = overrides.get("riskLevel",
                "high" if risk_score >= 65 else ("medium" if risk_score >= 40 else "low"))
            db["landlords"][ll_id] = {
                "landlordId": ll_id,
                "orgId": lease_org_id,
                "org_id": lease_org_id,
                "name": name,
                "country": country,
                "contactName": None,
                "contactEmail": None,
                "riskScore": risk_score,
                "riskLevel": risk_level,
                "totalLocations": 0,
                "totalAnnualRent": 0.0,
                "createdAt": datetime.utcnow().isoformat(),
                "updatedAt": datetime.utcnow().isoformat(),
            }
            seen[key] = ll_id

        lease["landlord_id"] = seen[key]

    # Compute derived totals
    for ll_id, ll in db["landlords"].items():
        related = [l for l in db["leases"].values() if l.get("landlord_id") == ll_id]
        ll["totalLocations"] = len(related)
        ll["totalAnnualRent"] = round(
            sum(l.get("monthly_rent", 0) * 12 for l in related), 2
        )


def _backfill_ids():
    # (org_id, name) -> landlord_id — org-aware to avoid cross-tenant assignment
    key_to_id = {
        (ll.get("org_id") or ll.get("orgId") or "", ll["name"]): ll_id
        for ll_id, ll in db["landlords"].items()
    }
    for lease in db.get("leases", {}).values():
        name = lease.get("landlord_name", "Unknown Landlord")
        lease_org_id = lease.get("org_id") or ""
        key = (lease_org_id, name)
        if "landlord_id" not in lease and key in key_to_id:
            lease["landlord_id"] = key_to_id[key]

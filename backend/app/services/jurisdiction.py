"""
Jurisdiction service — maps country codes to applicable accounting standards.
"""
from app.mock_db import db

JURISDICTION_MAP: dict[str, str] = {
    "US": "ASC 842",
    "CA": "IFRS 16",
    "GB": "IFRS 16",
    "AU": "IFRS 16",
    "IN": "Ind AS 116",
    "DE": "IFRS 16",
    "SG": "IFRS 16",
    "AE": "IFRS 16",
    "NZ": "IFRS 16",
    "FR": "IFRS 16",
}


def get_standard_for_lease(lease: dict, org_id: str = "org1") -> str:
    org = db.get("org_settings", {}).get(org_id, {})
    if org.get("complianceStandard"):
        return org["complianceStandard"]
    country = lease.get("country", "US")
    return JURISDICTION_MAP.get(country, "IFRS 16")


def get_all_standards() -> list[str]:
    return ["ASC 842", "IFRS 16", "Ind AS 116"]

"""
FX rate service — uses fixed mock rates. In production: polls Open Exchange Rates daily.
"""
from app.mock_db import db

MOCK_FX_RATES: dict[str, float] = {
    "USD": 1.0,
    "GBP": 0.79,
    "EUR": 0.92,
    "INR": 83.5,
    "CAD": 1.36,
    "AUD": 1.52,
    "SGD": 1.34,
    "AED": 3.67,
}


def get_reporting_currency(org_id: str = "org1") -> str:
    settings = db.get("org_settings", {}).get(org_id, {})
    return settings.get("reportingCurrency", "USD")


def convert_to_reporting_currency(amount: float, from_currency: str, org_id: str = "org1") -> float:
    reporting = get_reporting_currency(org_id)
    if from_currency == reporting:
        return amount
    usd_amount = amount / MOCK_FX_RATES.get(from_currency, 1.0)
    return round(usd_amount * MOCK_FX_RATES.get(reporting, 1.0), 2)


def get_all_rates() -> dict:
    return {
        "rates": MOCK_FX_RATES,
        "baseCurrency": "USD",
        "lastUpdated": "2026-04-29T00:00:00Z",
    }

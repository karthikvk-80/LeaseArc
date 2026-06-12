from faker import Faker
from app.mock_db import db
import uuid
import random

fake = Faker()
Faker.seed(42)
random.seed(99)

CAM_CATEGORIES = [
    "Common Area Maintenance",
    "Real Estate Taxes",
    "Insurance",
    "Management Fee",
    "HVAC",
    "Utilities",
    "Janitorial",
    "Security",
    "Landscaping",
    "Snow Removal",
    "Pest Control",
    "Repairs & Maintenance",
    "Capital Expenditures",
]

# Category → most-likely flag reason (for demo realism)
CATEGORY_FLAG_REASONS = {
    "HVAC": "Excluded per Clause 12.3 — HVAC capital maintenance not chargeable under this lease",
    "Capital Expenditures": "Capital expenditures excluded under Clause 15.2 — non-recoverable improvement costs",
    "Management Fee": "Management fee exceeds contractual cap of 5% per Clause 8.4",
    "Real Estate Taxes": "Real estate tax portion non-recoverable per Clause 9.1 — base year exclusion applies",
    "Insurance": "Insurance premium above allowable market rate benchmark per Clause 11.2",
    "Snow Removal": "Snow removal charged outside agreed seasonal scope per Clause 14.6",
    "Janitorial": "Janitorial services exceed agreed specification per Exhibit C",
    "Pest Control": "Pest control cost includes non-common areas — excluded per Clause 12.7",
    "Repairs & Maintenance": "Repair costs relate to landlord structural obligations excluded per Clause 13.4",
    "Landscaping": "Landscaping scope exceeds tenant's proportionate share per Exhibit B",
    "Utilities": "Utility sub-metering methodology not in compliance with Clause 10.2",
    "Security": "Security upgrade costs are capital in nature — excluded per Clause 15.2",
    "Common Area Maintenance": "Administrative uplift applied to excluded items in breach of Clause 8.1",
}

CLAUSE_REFS = ["Clause 8.1", "Clause 9.3", "Clause 11.2", "Clause 12.3", "Clause 14.6", "Clause 15.2", "Exhibit C", "Schedule 2", "Clause 10.2", "Clause 13.4"]

FLAG_REASONS = list(CATEGORY_FLAG_REASONS.values())

# GL account codes per category — capital categories get capital accounts
CATEGORY_GL_ACCOUNTS = {
    "HVAC": ("5420", "Capital Improvements — HVAC"),
    "Capital Expenditures": ("5410", "Building Improvements"),
    "Management Fee": ("4200", "Management & Admin Fees"),
    "Real Estate Taxes": ("4300", "Property Taxes & Levies"),
    "Insurance": ("4150", "Building Insurance"),
    "Snow Removal": ("4080", "Seasonal Maintenance"),
    "Janitorial": ("4060", "Janitorial & Cleaning"),
    "Pest Control": ("4070", "Pest Control Services"),
    "Repairs & Maintenance": ("4050", "Repairs & Maintenance"),
    "Landscaping": ("4075", "Grounds & Landscaping"),
    "Utilities": ("4100", "Common Area Utilities"),
    "Security": ("5430", "Capital Improvements — Security"),
    "Common Area Maintenance": ("4010", "Common Area Maintenance"),
}

# Invoice description templates per category
INVOICE_DESCRIPTIONS = {
    "HVAC": ["roof-mounted HVAC unit replacement", "central air handler capital upgrade", "chiller plant overhaul"],
    "Capital Expenditures": ["lobby renovation capital works", "elevator modernisation program", "façade improvement project"],
    "Management Fee": ["property management services — full year", "management fee — Q3/Q4 blended", "administration & oversight fee"],
    "Real Estate Taxes": ["municipal property tax levy FY2024", "council rates annual assessment", "land tax — base year adjustment"],
    "Insurance": ["building all-risks insurance premium", "public liability insurance annual", "combined property insurance policy"],
    "Snow Removal": ["snow ploughing — January 2025 (outside scope)", "ice treatment — Feb/Mar 2025 seasonal", "winter maintenance — extended season"],
    "Janitorial": ["enhanced cleaning specification FY2024", "daily cleaning services — full building", "deep clean & sanitisation program"],
    "Pest Control": ["annual pest management program — all areas", "rodent control — warehouse & loading", "termite prevention treatment"],
    "Repairs & Maintenance": ["structural roof repair — landlord obligation", "façade crack remediation works", "car park resurfacing — capital component"],
    "Landscaping": ["annual landscaping — full site scope", "garden maintenance & irrigation FY2024", "tree surgery & grounds programme"],
    "Utilities": ["electricity — common areas FY2024", "water & sewerage — building total", "gas supply — central plant"],
    "Security": ["CCTV system upgrade — capital installation", "access control modernisation project", "security barrier replacement works"],
    "Common Area Maintenance": ["CAM administration fee & overhead uplift", "common area upkeep FY2024", "facility management overhead"],
}

# Conflict type details
CONFLICT_TEMPLATES = {
    "amount_gap": [
        ("Invoice total {inv_amt} vs statement charge {stmt_amt} — unexplained gap {gap}", ),
        ("Supporting invoices sum to {inv_amt}; landlord billed {stmt_amt} — {gap} variance unaccounted", ),
    ],
    "out_of_period": [
        ("Invoice dated {inv_date} falls outside FY {year} reconciliation period", ),
        ("{count} invoices dated after reconciliation close ({inv_date}) — charged to prior year statement", ),
    ],
    "pro_rata_error": [
        ("Charged at {charged_pct}% pro-rata share; Rent Roll confirms allowable share {allow_pct}%", ),
        ("Your GLA is {gla} sq ft of {total_gla} sq ft total ({allow_pct}%) — billed at {charged_pct}%", ),
    ],
}

OUT_OF_PERIOD_MONTHS = ["January 2025", "February 2025", "March 2025", "December 2022"]


def _make_invoice_refs(category: str, year: int, count: int = 2) -> list:
    refs = []
    for _ in range(count):
        num = random.randint(1000, 9999)
        refs.append(f"INV-{year}-{num}")
    return refs


def _make_evidence_sources(category: str, is_flagged: bool, flag_reason: str | None, year: int) -> list:
    sources = []

    if is_flagged and flag_reason:
        # Always include lease clause evidence for flagged items
        clause = flag_reason.split("—")[0].split("per ")[-1].strip() if "per " in flag_reason else "Clause 12.3"
        detail = flag_reason.split("—")[-1].strip() if "—" in flag_reason else flag_reason
        sources.append({
            "type": "lease_clause",
            "ref": clause,
            "detail": detail,
        })

        # GL evidence — always for flagged items
        gl_code, gl_name = CATEGORY_GL_ACCOUNTS.get(category, ("4000", "Operating Expenses"))
        is_capital = gl_code.startswith("5")
        sources.append({
            "type": "general_ledger",
            "ref": f"Account #{gl_code}",
            "detail": f"Posted to {gl_name}" + (" — capital classification confirms non-recoverability" if is_capital else " — operating account"),
        })

        # Invoice evidence — 70% chance for flagged items
        if random.random() < 0.70:
            inv_descs = INVOICE_DESCRIPTIONS.get(category, ["service charge"])
            inv_desc = random.choice(inv_descs)
            inv_ref = f"INV-{year}-{random.randint(1000, 9999)}"
            sources.append({
                "type": "invoice",
                "ref": inv_ref,
                "detail": f'Description: "{inv_desc}"',
            })
    else:
        # Allowed items: GL evidence showing correct classification
        gl_code, gl_name = CATEGORY_GL_ACCOUNTS.get(category, ("4000", "Operating Expenses"))
        sources.append({
            "type": "general_ledger",
            "ref": f"Account #{gl_code}",
            "detail": f"Correctly posted to {gl_name} — operating expense, recoverable under lease",
        })
        # 40% chance of invoice match confirmation
        if random.random() < 0.40:
            inv_ref = f"INV-{year}-{random.randint(1000, 9999)}"
            sources.append({
                "type": "invoice",
                "ref": inv_ref,
                "detail": "Invoice amount matches statement charge — verified",
            })

    return sources


def _make_conflict(category: str, landlord_amount: float, year: int):
    """Return (conflict_type, conflict_detail) or (None, None)."""
    r = random.random()
    if r < 0.15:
        # amount_gap conflict
        gap_pct = random.uniform(0.08, 0.25)
        inv_total = round(landlord_amount * (1 - gap_pct), 2)
        gap = round(landlord_amount - inv_total, 2)
        detail = f"Supporting invoices sum to ${inv_total:,.2f}; landlord billed ${landlord_amount:,.2f} — ${gap:,.2f} variance unaccounted"
        return "amount_gap", detail
    elif r < 0.25:
        # out_of_period conflict
        month = random.choice(OUT_OF_PERIOD_MONTHS)
        count = random.randint(1, 3)
        detail = f"{count} invoice{'s' if count > 1 else ''} dated {month} — outside FY {year} reconciliation period"
        return "out_of_period", detail
    elif r < 0.30:
        # pro_rata_error conflict
        allow_pct = round(random.uniform(14.0, 22.0), 1)
        charged_pct = round(allow_pct + random.uniform(2.0, 6.0), 1)
        gla = random.randint(1200, 3500)
        total_gla = round(gla / (allow_pct / 100))
        detail = f"Your GLA is {gla:,} sq ft of {total_gla:,} sq ft total ({allow_pct}%) — billed at {charged_pct}%"
        return "pro_rata_error", detail
    return None, None


def seed_cam():
    if db["cam_statements"]:
        return  # idempotent

    # CAM data stays Xtract-only for now — WeWork hasn't run any audits yet.
    xtract_org_id = db.get("org_ids", {}).get("Xtract.io")
    lease_ids = [
        lease_id for lease_id, lease in db["leases"].items()
        if lease.get("org_id") == xtract_org_id
    ]

    for idx, lease_id in enumerate(lease_ids):
        # First 10 leases get higher overcharge rates (35-50%) for compelling demo
        high_dispute = idx < 10
        flag_rate = random.uniform(0.40, 0.55) if high_dispute else random.uniform(0.30, 0.45)

        for year in [2023, 2024]:
            statement_id = str(uuid.uuid4())

            n_items = random.randint(9, 13)
            categories_used = random.sample(CAM_CATEGORIES, min(n_items, len(CAM_CATEGORIES)))
            if n_items > len(categories_used):
                categories_used += random.choices(CAM_CATEGORIES, k=n_items - len(categories_used))

            line_items = []
            landlord_total = 0.0
            audited_total = 0.0

            # Pro-rata data for this statement (from "rent roll")
            gla = random.randint(1200, 4000)
            total_gla = random.randint(8000, 18000)
            allowable_pro_rata = round(gla / total_gla * 100, 1)
            charged_pro_rata = round(allowable_pro_rata + random.uniform(-1.0, 3.5), 1)

            for cat in categories_used[:n_items]:
                line_item_id = str(uuid.uuid4())
                # High-value line items for demo impact
                if cat in ("HVAC", "Capital Expenditures", "Management Fee", "Real Estate Taxes"):
                    landlord_amount = round(random.uniform(8000, 55000), 2)
                else:
                    landlord_amount = round(random.uniform(1500, 25000), 2)

                is_flagged = random.random() < flag_rate

                if is_flagged:
                    flag_reason = CATEGORY_FLAG_REASONS.get(cat, CATEGORY_FLAG_REASONS["Common Area Maintenance"])
                    allowable_amount = 0.0
                    variance = landlord_amount
                    status = "flagged"
                    is_excluded = True
                    conflict_type, conflict_detail = _make_conflict(cat, landlord_amount, year)
                else:
                    flag_reason = None
                    allowable_amount = round(landlord_amount * random.uniform(0.88, 1.0), 2)
                    variance = round(landlord_amount - allowable_amount, 2)
                    status = "allowed" if variance < 200 else random.choice(["allowed", "under_review"])
                    is_excluded = False
                    conflict_type, conflict_detail = None, None

                gl_code, gl_name = CATEGORY_GL_ACCOUNTS.get(cat, ("4000", "Operating Expenses"))
                invoice_refs = _make_invoice_refs(cat, year, random.randint(1, 3)) if is_flagged else []
                evidence_sources = _make_evidence_sources(cat, is_flagged, flag_reason, year)

                li = {
                    "line_item_id": line_item_id,
                    "statement_id": statement_id,
                    "lease_id": lease_id,
                    "org_id": xtract_org_id,
                    "expense_category": cat,
                    "landlord_amount": landlord_amount,
                    "allowable_amount": allowable_amount,
                    "variance": round(variance, 2),
                    "is_excluded": is_excluded,
                    "flag_reason": flag_reason,
                    "status": status,
                    "confidence": random.randint(82, 99),
                    "lease_clause_ref": CATEGORY_FLAG_REASONS.get(cat, "Clause 12.3").split("—")[0].split("per ")[-1].strip() if is_flagged else random.choice(CLAUSE_REFS),
                    # Evidence fields
                    "evidence_sources": evidence_sources,
                    "gl_account": f"{gl_code} — {gl_name}",
                    "invoice_refs": invoice_refs,
                    "conflict_type": conflict_type,
                    "conflict_detail": conflict_detail,
                }
                line_items.append(li)
                landlord_total += landlord_amount
                audited_total += allowable_amount

            variance_amount = round(landlord_total - audited_total, 2)

            db["cam_line_items"][statement_id] = line_items

            statement = {
                "statement_id": statement_id,
                "lease_id": lease_id,
                "org_id": xtract_org_id,
                "statement_year": year,
                "landlord_total": round(landlord_total, 2),
                "audited_total": round(audited_total, 2),
                "variance_amount": variance_amount,
                "percent_overcharged": round(variance_amount / landlord_total * 100, 1) if landlord_total > 0 else 0,
                "status": "audited",
                "pdf_url": f"/mock/cam/{statement_id}.pdf",
                # Pro-rata data from "rent roll"
                "pro_rata_gla": gla,
                "pro_rata_total_gla": total_gla,
                "pro_rata_allowable": allowable_pro_rata,
                "pro_rata_charged": charged_pro_rata,
            }
            db["cam_statements"][statement_id] = statement

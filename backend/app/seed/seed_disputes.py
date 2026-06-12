from faker import Faker
from app.mock_db import db
import uuid
import random
from datetime import date, timedelta

fake = Faker()
Faker.seed(42)
random.seed(77)

# ---------- Realistic landlord contact names (one person per company) ----------

LANDLORD_CONTACTS = {
    "Cushman & Wakefield":    "Marcus Webb",
    "CBRE Properties":        "Diane Holloway",
    "JLL Real Estate":        "Richard Tan",
    "Prologis":               "Angela Reyes",
    "Simon Property Group":   "Tom Hennessey",
    "Brookfield Properties":  "Claire Dubois",
    "Unibail-Rodamco":        "Stefan Müller",
    "Scentre Group":          "Fiona Hartley",
    "Link REIT":              "Henry Lam",
    "GIC Real Estate":        "Pradeep Nair",
    "Hines Group":            "Patricia Osei",
    "Oxford Properties":      "Andrew Clarke",
    "Westfield Corp":         "Sandra Kovacs",
    "Mall of America Realty": "Greg Hoffman",
    "Prestige Estates Pvt Ltd": "Vikram Patel",
}
DEFAULT_CONTACT = "Property Manager"

USER_NAMES = {
    "user-001": "Sarah Chen",
    "user-002": "James Wilson",
    "user-003": "Priya Sharma",
    "user-004": "Michael Torres",
}


def _contact(landlord_name: str) -> str:
    return LANDLORD_CONTACTS.get(landlord_name, DEFAULT_CONTACT)


# ---------- Realistic email body templates ----------

OUTBOUND_BODIES = {
    "cam_dispute": [
        "Dear {landlord_contact},\n\nWe are writing to formally dispute charges included in the CAM reconciliation statement for the above-referenced property. Following a detailed review, we have identified administrative fees billed at 18% of total CAM costs, which exceeds the 15% contractual cap set out in Clause 12.3 of our executed lease. Additionally, HVAC capital expenditure charges of approximately $8,400 appear to have been included, despite these being expressly excluded under Schedule 3.\n\nWe respectfully request a credit note for the disputed amount and a revised reconciliation statement within 14 business days.\n\nKind regards,\nLease Management Team",
        "Dear {landlord_contact},\n\nThank you for submitting the CAM reconciliation statement for the period ended 31 December. We have reviewed the statement against the provisions of our lease and identified several discrepancies that require your attention. Management fees have been calculated on gross CAM costs including excluded items, resulting in an overstatement of approximately $3,200. Security costs appear to have been grossed-up beyond the permitted 5% administrative uplift.\n\nWe request a revised statement addressing these items within 10 business days. Please do not hesitate to contact us if you require supporting documentation.\n\nYours sincerely,\nSarah Chen, RE Director",
    ],
    "amendment_risk": [
        "Dear {landlord_contact},\n\nWe have received and reviewed Amendment No. 2 to our lease agreement. While we acknowledge the landlord's right to initiate certain amendments, we have concerns regarding the removal of the co-tenancy protection clause and the modification of the CAM cap provisions. These changes materially alter our risk exposure and we believe the proposed terms are above market for comparable retail locations.\n\nWe propose a meeting to negotiate revised terms before executing the amendment. Our legal counsel has been engaged and will participate in any discussions.\n\nKind regards,\nRetailCo Lease Management",
        "Dear {landlord_contact},\n\nWe are in receipt of the proposed lease amendment and are currently reviewing it with our legal team. We note with concern that the renewal notice period has been shortened from 12 months to 6 months, which significantly reduces our planning horizon. We would like to propose maintaining the original 12-month notice requirement or, at minimum, agreeing on 9 months as a compromise position.\n\nPlease advise on your availability for a call this week to discuss our position.\n\nBest regards,\nJames Wilson, Lease Administrator",
    ],
    "renewal": [
        "Dear {landlord_contact},\n\nI am writing on behalf of RetailCo to formally express our intention to exercise the renewal option available under Section 12 of our lease agreement. The current term is due to expire and we wish to continue our occupation of the premises on terms to be agreed. We value the location highly and are committed to maintaining a strong long-term relationship.\n\nPlease confirm receipt of this notice and advise on your preferred process for agreeing the renewal terms. We are available for a meeting at your convenience.\n\nYours sincerely,\nSarah Chen, RE Director",
        "Dear {landlord_contact},\n\nAs per Clause 12.2 of our executed lease agreement, we hereby provide formal written notice of our intention to renew the lease for a further term of 5 years, commencing from the expiry of the current term. We would appreciate confirmation that this notice has been received and recorded, and we look forward to receiving your proposed renewal terms in due course.\n\nKind regards,\nRetailCo Lease Management",
    ],
    "cam_query": [
        "Dear {landlord_contact},\n\nWe have received the CAM reconciliation statement for the prior year and have a number of queries before we can approve the figures. Could you please provide a breakdown of the utilities allocation methodology, the basis for the management fee calculation, and copies of invoices for maintenance items exceeding $5,000? We also note that the total appears inconsistent with our monthly accruals and would appreciate clarification.\n\nWe aim to resolve these queries within 30 days. Thank you for your cooperation.\n\nKind regards,\nFinance Team",
        "Dear {landlord_contact},\n\nThank you for providing the CAM statement. We are in the process of reviewing the figures and have a few items we would like to clarify. Specifically, we would like to understand the basis for the insurance cost allocation, which appears to have increased by 22% year-on-year without a corresponding explanation. Could you also confirm whether the car park maintenance costs are within the recoverable expenses definition under our lease?\n\nWe look forward to your response.\n\nYours sincerely,\nJames Wilson",
    ],
    "maintenance": [
        "Dear {landlord_contact},\n\nWe wish to draw your attention to ongoing maintenance issues affecting the common areas adjacent to our tenancy. The air conditioning units servicing the main retail corridor have been operating intermittently for the past 3 weeks, causing discomfort to our customers and staff. We have logged this issue with your facilities team on two previous occasions without resolution.\n\nWe respectfully request that urgent remediation works be scheduled within 5 business days. We will escalate to our legal team if the issue remains unresolved.\n\nKind regards,\nRetailCo Facilities",
        "Dear {landlord_contact},\n\nFollowing our previous correspondence, we are pleased to confirm that the maintenance works to the loading dock have been completed satisfactorily. Thank you for arranging the contractors promptly. We request that a record of these works be added to the property maintenance log as this may be relevant to our CAM reconciliation review at year end.\n\nBest regards,\nRetailCo",
    ],
    "payment": [
        "Dear {landlord_contact},\n\nPlease find enclosed our remittance advice for rent and outgoings due for the current month. Payment has been made by EFT to your nominated account and should clear within 2 business days. Please note that we have withheld $1,240 relating to the disputed CAM charge referenced in our letter of last month, pending resolution of that matter.\n\nKindly confirm receipt of this payment.\n\nRegards,\nRetailCo Finance",
        "Dear {landlord_contact},\n\nWe confirm that rent for the current quarter has been transferred to your nominated bank account in full. We note that the direct debit was drawn 3 days earlier than the contractual due date, which is inconsistent with our agreed payment terms. We request that future direct debits be processed on or after the 1st of the month as specified in our lease.\n\nThank you for your attention to this matter.\n\nKind regards,\nRetailCo Finance Team",
    ],
    "compliance": [
        "Dear Tenant,\n\nWe write to bring to your attention a compliance matter relating to your fit-out at the above property. Our recent inspection noted that external signage has been installed that does not conform to the approved signage schedule attached to your lease. We require you to rectify this within 30 days or obtain formal written approval from our design review team.\n\nPlease acknowledge receipt of this notice.\n\nYours faithfully,\nProperty Management",
        "Dear {landlord_contact},\n\nWe acknowledge receipt of your compliance notice dated last week. We have reviewed the matter and wish to clarify that the signage in question was installed pursuant to verbal approval provided by your centre manager during our fit-out period. We are happy to formalise this approval through your design review process and request that you initiate this on our behalf as a matter of urgency.\n\nKind regards,\nRetailCo Compliance Team",
    ],
    "insurance": [
        "Dear {landlord_contact},\n\nPlease find attached our certificate of currency for public liability insurance and plate glass cover for the current policy year, as required under Clause 18 of our lease. The policy is in the name of RetailCo Pty Ltd and covers the insured premises for the full lease term. Should you require any endorsements or additional named insured provisions, please advise and we will liaise with our broker.\n\nKind regards,\nRetailCo Risk Management",
        "Dear Tenant,\n\nThank you for providing your insurance certificate. We have noted that the policy expiry date falls before the lease expiry date. Please ensure that renewal documentation is provided to our office no later than 14 days prior to the current policy expiry. Failure to maintain required insurance coverage is a breach of your lease obligations.\n\nRegards,\nProperty Management Team",
    ],
    "estoppel": [
        "Dear {landlord_contact},\n\nWe are in receipt of the estoppel certificate request in connection with the proposed refinancing of the property. We have reviewed the draft certificate and note one discrepancy: the commencement date listed is incorrect and should read the date stipulated in our executed lease agreement. Subject to this correction, we are prepared to execute the certificate within 5 business days.\n\nKind regards,\nRetailCo Legal",
        "Dear {landlord_contact},\n\nThank you for the estoppel certificate request. We confirm that to the best of our knowledge, the lease is in full force and effect, no defaults exist on either side, and the rent stated in the certificate is correct. We will arrange for the executed certificate to be returned to your office within the requested timeframe.\n\nYours sincerely,\nSarah Chen",
    ],
    "access": [
        "Dear Tenant,\n\nPlease be advised that essential maintenance works to the building services will be carried out over the coming weekend. The works will require temporary interruption to electricity supply between 06:00 and 10:00 on Saturday morning. We apologise for any inconvenience and request that you make appropriate arrangements. Building access will otherwise remain available throughout the works period.\n\nRegards,\nFacilities Management",
        "Dear {landlord_contact},\n\nThank you for advising of the upcoming maintenance works. We note that the proposed interruption period overlaps with our scheduled delivery window. We request that the interruption be rescheduled to commence no earlier than 07:30 to allow for our delivery to be completed. Alternatively, we are happy to accept a credit against outgoings for any loss occasioned by the disruption.\n\nKind regards,\nRetailCo Operations",
    ],
    "signage": [
        "Dear {landlord_contact},\n\nWe are in the process of refreshing our store branding in line with our national relaunch campaign and wish to seek approval for updated external signage. Please find attached revised signage drawings prepared by our approved contractor. The proposed changes involve a new font treatment and updated brand colours but retain the same footprint and illumination method as the existing approved signage.\n\nWe look forward to your design review team's feedback.\n\nKind regards,\nRetailCo Brand Team",
        "Dear Tenant,\n\nWe have reviewed the revised signage drawings submitted for approval. The design is acceptable subject to the following conditions: (1) the illumination intensity must not exceed 800 lumens, (2) installation must be completed outside centre trading hours, and (3) a copy of the contractor's public liability certificate must be provided prior to works commencing. Please confirm acceptance of these conditions.\n\nRegards,\nDesign Review, Property Management",
    ],
    "works": [
        "Dear Tenant,\n\nWe wish to advise that the landlord intends to commence upgrade works to the common areas of the centre, including the main atrium and service corridors, commencing next quarter. The works are expected to run for approximately 12 weeks. Access to your tenancy will not be restricted, however there may be minor disruption during peak works phases. We will provide 14 days' notice before works commence in your immediate vicinity.\n\nYours faithfully,\nProperty Management",
        "Dear {landlord_contact},\n\nWe acknowledge your notice regarding the common area improvement works. We would like to request confirmation that (1) no works will be undertaken during our trading hours without prior written consent, (2) the landlord will bear any costs associated with additional cleaning or security required by our tenancy during the works period, and (3) we will be provided with regular progress updates. Your written confirmation of these points would be appreciated.\n\nKind regards,\nRetailCo Operations Manager",
    ],
}

INBOUND_BODIES = {
    "cam_dispute": [
        "Dear Tenant,\n\nThank you for your correspondence regarding the CAM reconciliation statement. We have reviewed the items you have raised and wish to respond as follows. The administrative fee was calculated on total recoverable expenses in accordance with the definition in Schedule 1 of your lease, which includes management costs. Regarding the HVAC charges, these relate to routine maintenance rather than capital expenditure and are accordingly recoverable.\n\nWe trust this clarifies the position. If you wish to discuss further, please contact our accounts team.\n\nYours faithfully,\nProperty Management",
        "Dear RetailCo,\n\nWe are in receipt of your dispute notice and take your concerns seriously. Our internal audit team has commenced a review of the items you have identified. We anticipate being in a position to respond substantively within 10 business days. In the meantime, we confirm that no interest will be charged on the disputed amount pending resolution.\n\nThank you for bringing this to our attention.\n\nKind regards,\nHead of Property Management",
    ],
    "renewal": [
        "Dear Tenant,\n\nThank you for your renewal notice. We are pleased to confirm that we would welcome the continuation of your tenancy and will prepare proposed renewal terms for your review. Please note that the new rent will be subject to a market review, and we anticipate the revised annual rent will be in the range of a 5-8% increase on current levels.\n\nWe will have formal heads of terms to you within 2 weeks.\n\nKind regards,\nLeasing Team",
        "Dear RetailCo,\n\nWe acknowledge your letter exercising the renewal option. We note that this notice has been served within the permitted notice window as specified in the lease. We will proceed accordingly and our leasing team will contact you shortly to discuss the terms for the renewal period.\n\nYours faithfully,\nProperty Management",
    ],
    "general": [
        "Dear Tenant,\n\nThank you for your correspondence. We have noted the matters raised and will respond in full within 5 business days. In the meantime, please do not hesitate to contact your property manager directly if the matter requires urgent attention.\n\nKind regards,\nProperty Management",
        "Dear RetailCo Lease Management,\n\nThank you for your email. We have passed your query to the relevant department and you can expect a substantive response within 10 business days. We apologise for any inconvenience in the interim.\n\nYours faithfully,\nProperty Management Helpdesk",
    ],
}


def _pick_body(subject: str, direction: str, store_name: str, landlord_name: str) -> str:
    landlord_contact = _contact(landlord_name)
    subj_lower = subject.lower()
    pool = None
    if direction == "outbound":
        if "cam dispute" in subj_lower or "amendment risk" in subj_lower:
            key = "cam_dispute" if "cam" in subj_lower else "amendment_risk"
            pool = OUTBOUND_BODIES.get(key) or OUTBOUND_BODIES["cam_dispute"]
        elif "renewal" in subj_lower:
            pool = OUTBOUND_BODIES["renewal"]
        elif "cam reconciliation" in subj_lower or "cam query" in subj_lower:
            pool = OUTBOUND_BODIES["cam_query"]
        elif "maintenance" in subj_lower:
            pool = OUTBOUND_BODIES["maintenance"]
        elif "payment" in subj_lower:
            pool = OUTBOUND_BODIES["payment"]
        elif "compliance" in subj_lower:
            pool = OUTBOUND_BODIES["compliance"]
        elif "insurance" in subj_lower:
            pool = OUTBOUND_BODIES["insurance"]
        elif "estoppel" in subj_lower:
            pool = OUTBOUND_BODIES["estoppel"]
        elif "access" in subj_lower:
            pool = OUTBOUND_BODIES["access"]
        elif "signage" in subj_lower:
            pool = OUTBOUND_BODIES["signage"]
        elif "improvement" in subj_lower or "works" in subj_lower:
            pool = OUTBOUND_BODIES["works"]
        else:
            pool = OUTBOUND_BODIES["compliance"]
    else:
        if "cam dispute" in subj_lower or "amendment risk" in subj_lower:
            pool = INBOUND_BODIES["cam_dispute"]
        elif "renewal" in subj_lower:
            pool = INBOUND_BODIES["renewal"]
        elif "compliance" in subj_lower or "access" in subj_lower or "works" in subj_lower:
            pool = OUTBOUND_BODIES["access"]
        elif "insurance" in subj_lower:
            pool = OUTBOUND_BODIES["insurance"]
        elif "estoppel" in subj_lower:
            pool = OUTBOUND_BODIES["estoppel"]
        elif "signage" in subj_lower:
            pool = OUTBOUND_BODIES["signage"]
        else:
            pool = INBOUND_BODIES["general"]

    body = random.choice(pool)
    return (
        body
        .replace("{store}", store_name)
        .replace("{landlord}", landlord_name)
        .replace("{landlord_contact}", landlord_contact)
    )


def seed_disputes():
    if db["disputes"]:
        return  # idempotent

    today = date.today()

    # Disputes/communications stay Xtract-only for now — same as CAM.
    xtract_org_id = db.get("org_ids", {}).get("Xtract.io")
    lease_ids = [
        lease_id for lease_id, lease in db["leases"].items()
        if lease.get("org_id") == xtract_org_id
    ]
    statement_ids = list(db["cam_statements"].keys())

    # 3 CAM disputes + 4 amendment disputes
    dispute_configs = [
        ("cam_dispute", statement_ids[0] if statement_ids else None, "open"),
        ("cam_dispute", statement_ids[1] if len(statement_ids) > 1 else None, "responded"),
        ("cam_dispute", statement_ids[2] if len(statement_ids) > 2 else None, "escalated"),
        ("amendment_risk", None, "open"),
        ("amendment_risk", None, "open"),
        ("amendment_risk", None, "responded"),
        ("amendment_risk", None, "resolved"),
    ]

    for i, (dtype, stmt_id, dstatus) in enumerate(dispute_configs):
        dispute_id = str(uuid.uuid4())
        lease_id = lease_ids[i % len(lease_ids)]
        lease = db["leases"].get(lease_id, {})
        store_name = lease.get("store_name", "Store")
        landlord_name = lease.get("landlord_name", "Landlord")
        landlord_contact = _contact(landlord_name)
        claimed = round(random.uniform(5000, 25000), 2)
        recovered = round(claimed * random.uniform(0.4, 0.9), 2) if dstatus == "resolved" else 0.0
        created_at = (today - timedelta(days=random.randint(10, 90))).isoformat()
        last_activity = (today - timedelta(days=random.randint(1, 10))).isoformat()

        dispute = {
            "dispute_id": dispute_id,
            "lease_id": lease_id,
            "org_id": xtract_org_id,
            "statement_id": stmt_id,
            "dispute_type": dtype,
            "status": dstatus,
            "claimed_amount": claimed,
            "recovered_amount": recovered,
            "created_at": created_at,
            "resolved_at": today.isoformat() if dstatus == "resolved" else None,
            "location_name": store_name,
            "last_activity": last_activity,
            "notes": "",
        }
        db["disputes"][dispute_id] = dispute

        # Communications for this dispute
        n_comms = random.randint(2, 4)
        for c in range(n_comms):
            comm_id = str(uuid.uuid4())
            direction = "outbound" if c % 2 == 0 else "inbound"
            comm_type = random.choice(["email", "letter"])
            subject = (f"RE: CAM Dispute — {store_name}" if dtype == "cam_dispute"
                       else f"RE: Amendment Risk — {store_name}")
            db["communications"][comm_id] = {
                "comm_id": comm_id,
                "lease_id": lease_id,
                "org_id": xtract_org_id,
                "dispute_id": dispute_id,
                "type": comm_type,
                "direction": direction,
                "subject": subject,
                "body": _pick_body(subject, direction, store_name, landlord_name),
                "sent_by": "user-001",
                "sent_at": (today - timedelta(days=random.randint(1, 30))).isoformat(),
                "linked_clause": random.choice(["Clause 12.3", "Clause 8.1", None]),
                "from_name": "Sarah Chen" if direction == "outbound" else landlord_contact,
                "to_name": landlord_contact if direction == "outbound" else "Sarah Chen",
            }

    # Communications for ALL remaining leases (not tied to disputes)
    SUBJECT_TEMPLATES = [
        "RE: Lease Renewal Discussion — {store}",
        "RE: CAM Reconciliation Query — {store}",
        "RE: Property Maintenance Request — {store}",
        "RE: Rent Payment Confirmation — {store}",
        "RE: Lease Compliance Notice — {store}",
        "RE: Building Access Schedule — {store}",
        "RE: Annual Insurance Certificate — {store}",
        "RE: Estoppel Certificate Request — {store}",
        "RE: Signage Approval — {store}",
        "RE: Common Area Improvement Works — {store}",
    ]

    dispute_lease_ids = set(d["lease_id"] for d in db["disputes"].values())
    for lease_id in lease_ids:
        if lease_id in dispute_lease_ids:
            continue
        lease = db["leases"].get(lease_id, {})
        store_name = lease.get("store_name", "Store")
        landlord_name = lease.get("landlord_name", "Landlord")
        landlord_contact = _contact(landlord_name)
        n_comms = random.randint(3, 5)
        subjects_used = random.sample(SUBJECT_TEMPLATES, min(n_comms, len(SUBJECT_TEMPLATES)))
        for c, subj_tpl in enumerate(subjects_used):
            comm_id = str(uuid.uuid4())
            direction = "outbound" if c % 2 == 0 else "inbound"
            subject = subj_tpl.replace("{store}", store_name)
            sent_by = random.choice(["user-001", "user-002"])
            db["communications"][comm_id] = {
                "comm_id": comm_id,
                "lease_id": lease_id,
                "org_id": xtract_org_id,
                "dispute_id": None,
                "type": random.choice(["email", "letter"]),
                "direction": direction,
                "subject": subject,
                "body": _pick_body(subject, direction, store_name, landlord_name),
                "sent_by": sent_by,
                "sent_at": (today - timedelta(days=random.randint(1, 180))).isoformat(),
                "linked_clause": random.choice(["Clause 8.1", "Clause 12.3", None, None]),
                "from_name": USER_NAMES.get(sent_by, "Sarah Chen") if direction == "outbound" else landlord_contact,
                "to_name": landlord_contact if direction == "outbound" else USER_NAMES.get(sent_by, "Sarah Chen"),
            }

        # Draft letter (kept for negotiation module)
        letter_id = str(uuid.uuid4())
        db["dispute_letters"][dispute_id] = {
            "letter_id": letter_id,
            "dispute_id": dispute_id,
            "org_id": xtract_org_id,
            "tone": "formal_first_notice",
            "total_disputed": claimed,
            "clauses_cited": ["Clause 12.3", "Clause 15.2"],
            "content": f"""{today.strftime('%B %d, %Y')}

{landlord_name}
Property Management Division

RE: Notice of CAM Charge Dispute — {store_name}
Lease Reference: {lease_id[:8].upper()}
Statement Period: {random.choice([2023, 2024])}

Dear {landlord_contact},

We are writing to formally dispute certain charges included in the Common Area Maintenance (CAM) reconciliation statement for the above-referenced property.

Following a detailed review of your statement against the terms of our executed lease agreement, we have identified the following discrepancies totalling ${claimed:,.2f}:

1. HVAC charges of $8,400 — Excluded per Clause 12.3 of the Lease Agreement
2. Capital expenditure allocation of $5,200 — Non-recoverable per Clause 15.2
3. Management fee overage of ${round(claimed - 13600, 2):,.2f} — Exceeds contractual 5% cap

Total Amount Disputed: ${claimed:,.2f}

We respectfully request a credit of ${claimed:,.2f} against future rent obligations, or a direct refund, within thirty (30) days of this notice.

We value our ongoing relationship and look forward to resolving this matter promptly.

Sincerely,

Sarah Chen
RE Director, RetailCo Global
director@leasearc.com""",
        }

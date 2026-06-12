from app.mock_db import db
import uuid
from datetime import datetime, timedelta
import random

random.seed(55)


def seed_anomalies():
    if db.get("anomalies"):
        return

    xtract_org_id = db.get("org_ids", {}).get("Xtract.io", "org1")
    now = datetime.utcnow()
    anomalies = [
        {
            "anomalyId": str(uuid.uuid4()),
            "orgId": xtract_org_id,
            "leaseId": None,
            "landlordId": None,
            "anomalyType": "cam_spike",
            "priority": "high",
            "title": "CAM spike — Mumbai BKC Flagship (+27%)",
            "detail": "CAM charges increased from ₹2,80,000 to ₹3,56,000 year-on-year for lease IN-001.",
            "comparisonData": {"current": 356000, "prior": 280000, "pct": 27.2, "leaseId": "IN-001", "locationName": "Mumbai BKC Flagship"},
            "detectedAt": (now - timedelta(hours=6)).isoformat(),
            "status": "open",
            "actionedBy": None,
            "actionedAt": None,
            "linkedTaskId": None,
        },
        {
            "anomalyId": str(uuid.uuid4()),
            "orgId": xtract_org_id,
            "leaseId": None,
            "landlordId": None,
            "anomalyType": "recurring_overbill",
            "priority": "high",
            "title": "Recurring overbilling — Prestige Estates (3 months)",
            "detail": "Billed above expected for Jan, Feb, Mar 2026 across 4 locations. Total excess: ₹6,80,000.",
            "comparisonData": {"months": 3, "locations": 4, "totalVariance": 680000, "landlordName": "Prestige Estates"},
            "detectedAt": (now - timedelta(hours=6)).isoformat(),
            "status": "open",
            "actionedBy": None,
            "actionedAt": None,
            "linkedTaskId": None,
        },
        {
            "anomalyId": str(uuid.uuid4()),
            "orgId": xtract_org_id,
            "leaseId": None,
            "landlordId": None,
            "anomalyType": "expiry_no_decision",
            "priority": "high",
            "title": "No renewal decision — Bengaluru Koramangala (16 months)",
            "detail": "Lease expires Nov 2026. No renewal record found in pipeline.",
            "comparisonData": {"expiryDate": "2026-11-01", "monthsRemaining": 16, "locationName": "Bengaluru Koramangala"},
            "detectedAt": (now - timedelta(hours=6)).isoformat(),
            "status": "open",
            "actionedBy": None,
            "actionedAt": None,
            "linkedTaskId": None,
        },
        {
            "anomalyId": str(uuid.uuid4()),
            "orgId": xtract_org_id,
            "leaseId": None,
            "landlordId": None,
            "anomalyType": "amendment_not_actioned",
            "priority": "medium",
            "title": "Amendment risk not reviewed — Delhi Khan Market (18 days)",
            "detail": "2 high-risk clauses flagged 18 days ago. No counter-language logged in negotiation.",
            "comparisonData": {"daysSinceFlagged": 18, "riskCount": 2, "locationName": "Delhi Khan Market"},
            "detectedAt": (now - timedelta(days=18)).isoformat(),
            "status": "open",
            "actionedBy": None,
            "actionedAt": None,
            "linkedTaskId": None,
        },
        {
            "anomalyId": str(uuid.uuid4()),
            "orgId": xtract_org_id,
            "leaseId": None,
            "landlordId": None,
            "anomalyType": "low_extraction_coverage",
            "priority": "low",
            "title": "Low extraction coverage — 6 leases with >20% missing fields",
            "detail": "6 leases saved more than 7 days ago still have over 20% unextracted attributes.",
            "comparisonData": {"affectedLeases": 6, "avgMissingPct": 24},
            "detectedAt": (now - timedelta(days=2)).isoformat(),
            "status": "open",
            "actionedBy": None,
            "actionedAt": None,
            "linkedTaskId": None,
        },
    ]
    for a in anomalies:
        db["anomalies"][a["anomalyId"]] = a


MOCK_QUERIES = [
    {
        "queryId": str(uuid.uuid4()),
        "orgId": "org1",
        "question": "Which 5 locations had the highest CAM increase this year?",
        "answer": "Based on your CAM audit data, the 5 locations with the highest year-on-year CAM increases are: (1) Mumbai BKC Flagship +27.2% (₹75,600 increase), (2) Bengaluru MG Road +18.4% (₹52,000 increase), (3) Delhi Connaught Place +15.1% (₹41,000 increase), (4) Hyderabad Banjara Hills +12.8% (₹34,000 increase), (5) Chennai Anna Nagar +11.3% (₹28,500 increase). These locations account for 68% of your total CAM overcharge exposure.",
        "sources": [
            {"leaseId": "IN-001", "locationName": "Mumbai BKC Flagship", "relevantValue": "+27.2% YoY"},
            {"leaseId": "IN-006", "locationName": "Bengaluru MG Road", "relevantValue": "+18.4% YoY"},
            {"leaseId": "IN-010", "locationName": "Delhi Connaught Place", "relevantValue": "+15.1% YoY"},
        ],
        "confidence": "high",
        "attributesUsed": ["cam_charges_current_year", "cam_charges_prior_year"],
        "resultCount": 5,
        "askedAt": (datetime.utcnow() - timedelta(days=3)).isoformat(),
    },
    {
        "queryId": str(uuid.uuid4()),
        "orgId": "org1",
        "question": "Show all leases expiring in the next 6 months with no renewal decision",
        "answer": "3 leases expire within 6 months and have no renewal decision on record: (1) Mumbai BKC Flagship (IN-001) — expires Aug 2026, ₹18,50,000/month, (2) Bengaluru Whitefield (IN-008) — expires Sep 2026, ₹6,20,000/month, (3) Delhi Khan Market (IN-011) — expires Jul 2026, ₹9,40,000/month. Combined monthly exposure: ₹34,10,000. Recommend prioritising Delhi Khan Market given highest footfall and shortest remaining term.",
        "sources": [
            {"leaseId": "IN-001", "locationName": "Mumbai BKC Flagship", "relevantValue": "Expires Aug 2026"},
            {"leaseId": "IN-008", "locationName": "Bengaluru Whitefield", "relevantValue": "Expires Sep 2026"},
            {"leaseId": "IN-011", "locationName": "Delhi Khan Market", "relevantValue": "Expires Jul 2026"},
        ],
        "confidence": "high",
        "attributesUsed": ["expiry_date", "renewal_decision", "monthly_rent"],
        "resultCount": 3,
        "askedAt": (datetime.utcnow() - timedelta(days=5)).isoformat(),
    },
    {
        "queryId": str(uuid.uuid4()),
        "orgId": "org1",
        "question": "Which landlords have we disputed with more than twice?",
        "answer": "2 landlords have been subject to more than 2 disputes: (1) Prestige Estates — 4 disputes totalling ₹48,20,000 claimed, ₹31,50,000 recovered (65% recovery rate). (2) DLF Limited — 3 disputes totalling ₹22,80,000 claimed, ₹18,90,000 recovered (83% recovery rate). Prestige Estates shows a pattern of recurring CAM overcharges across multiple locations.",
        "sources": [
            {"leaseId": "IN-001", "locationName": "Mumbai BKC Flagship (Prestige)", "relevantValue": "2 disputes"},
            {"leaseId": "IN-002", "locationName": "Mumbai Lower Parel (Prestige)", "relevantValue": "2 disputes"},
        ],
        "confidence": "medium",
        "attributesUsed": ["landlord_name", "dispute_status", "claimed_amount", "recovered_amount"],
        "resultCount": 2,
        "askedAt": (datetime.utcnow() - timedelta(days=8)).isoformat(),
    },
    {
        "queryId": str(uuid.uuid4()),
        "orgId": "org1",
        "question": "What is total rent exposure in Maharashtra in INR?",
        "answer": "Your total Maharashtra rent exposure is ₹5,84,00,000 per year across 5 active leases: Mumbai BKC Flagship (IN-001) ₹2,22,00,000/yr, Mumbai Lower Parel (IN-002) ₹1,44,00,000/yr, Mumbai Andheri West (IN-003) ₹96,00,000/yr, Pune FC Road (IN-004) ₹72,00,000/yr, Pune Koregaon Park (IN-005) ₹50,00,000/yr. Average lease term remaining: 3.2 years. All leases are accounted for under Ind AS 116.",
        "sources": [
            {"leaseId": "IN-001", "locationName": "Mumbai BKC Flagship", "relevantValue": "₹18,50,000/month"},
            {"leaseId": "IN-002", "locationName": "Mumbai Lower Parel", "relevantValue": "₹12,00,000/month"},
        ],
        "confidence": "high",
        "attributesUsed": ["currency", "monthly_rent", "country", "expiry_date"],
        "resultCount": 5,
        "askedAt": (datetime.utcnow() - timedelta(days=12)).isoformat(),
    },
    {
        "queryId": str(uuid.uuid4()),
        "orgId": "org1",
        "question": "Which leases have a break clause exercisable in the next 12 months?",
        "answer": "4 leases have break clauses exercisable in the next 12 months: (1) Bengaluru Indiranagar — break date Aug 2026, penalty ₹3,60,000 if exercised. (2) Pune Koregaon Park — break date Oct 2026, no penalty. (3) Hyderabad Jubilee Hills — break date Dec 2026, penalty ₹2,20,000. (4) Noida Sector 18 — break date Jan 2027, no penalty. Note: Pune and Noida breaks have no financial penalty — these are low-cost exit options if relocation is preferred.",
        "sources": [
            {"leaseId": "IN-007", "locationName": "Bengaluru Indiranagar", "relevantValue": "Break: Aug 2026"},
            {"leaseId": "IN-005", "locationName": "Pune Koregaon Park", "relevantValue": "Break: Oct 2026"},
        ],
        "confidence": "high",
        "attributesUsed": ["break_clause_date", "break_penalty_amount"],
        "resultCount": 4,
        "askedAt": (datetime.utcnow() - timedelta(days=15)).isoformat(),
    },
]


def seed_query_history():
    if db.get("intelligence_queries"):
        return
    xtract_org_id = db.get("org_ids", {}).get("Xtract.io", "org1")
    for q in MOCK_QUERIES:
        db["intelligence_queries"][q["queryId"]] = {**q, "orgId": xtract_org_id}


def seed_all():
    seed_anomalies()
    seed_query_history()

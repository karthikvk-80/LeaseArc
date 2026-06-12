from app.mock_db import db
import uuid
from datetime import datetime, timedelta

_NOW = datetime.utcnow()


def _uid(seed: str | None = None):
    if seed:
        return seed
    return str(uuid.uuid4())


def seed_all():
    if db.get("prospects"):
        return

    xtract_org_id = db.get("org_ids", {}).get("Xtract.io", "org1")

    def landlord_id(name_fragment: str):
        for lid, l in db.get("landlords", {}).items():
            if name_fragment.lower() in l.get("name", "").lower():
                return lid, l["name"]
        return "", name_fragment

    # ── Prospect 1 — Sourcing ──────────────────────────────────────────────────
    p1_id = _uid("prospect_bkc_mumbai")
    db["prospects"][p1_id] = {
        "prospect_id": p1_id,
        "org_id": xtract_org_id,
        "location_name": "Bandra Kurla Complex",
        "address": "Unit 4B, Platina Building, BKC, Bandra East, Mumbai 400051",
        "city": "Mumbai",
        "country": "India",
        "landlord_id": "",
        "landlord_name": "Godrej Properties",
        "broker_name": "CBRE India",
        "stage": "sourcing",
        "loi_action": None,
        "created_by": "Sarah Chen",
        "created_at": (_NOW - timedelta(days=12)).isoformat(),
        "updated_at": (_NOW - timedelta(days=2)).isoformat(),
        "finalized_at": None,
        "notes": "Strong foot traffic. Flagship potential.",
        "size_sqft": 4200,
        "use_type": "Retail",
        "target_open_date": "2026-10-01",
    }

    q1a = _uid()
    db["price_quotes"][q1a] = {
        "quote_id": q1a,
        "prospect_id": p1_id,
        "quoted_by": "Godrej Properties (direct)",
        "landlord_id": "",
        "base_rent_monthly": 320000,
        "currency": "INR",
        "rent_free_months": 3,
        "lease_term_years": 5,
        "fit_out_contribution": 2500000,
        "cam_estimated_monthly": 35000,
        "escalation_pct": 5.0,
        "key_terms": "Standard commercial terms. Option to renew 1×5.",
        "notes": None,
        "source": "manual",
        "raw_email_text": None,
        "extracted_at": None,
        "is_shortlisted": True,
        "is_selected": False,
        "created_at": (_NOW - timedelta(days=10)).isoformat(),
    }
    q1b = _uid()
    db["price_quotes"][q1b] = {
        "quote_id": q1b,
        "prospect_id": p1_id,
        "quoted_by": "CBRE India",
        "landlord_id": None,
        "base_rent_monthly": 355000,
        "currency": "INR",
        "rent_free_months": 6,
        "lease_term_years": 9,
        "fit_out_contribution": 3500000,
        "cam_estimated_monthly": 40000,
        "escalation_pct": 5.0,
        "key_terms": "Demolition clause included. 1×5 year option at market rent.",
        "notes": "Imported from broker email",
        "source": "gmail_imported",
        "raw_email_text": "Subject: BKC Mumbai — Lease Proposal\nBase Rent: INR 3,55,000/month\nTerm: 9 years\nRent-Free: 6 months\nFit-Out: ₹35,00,000",
        "extracted_at": (_NOW - timedelta(days=8)).isoformat(),
        "is_shortlisted": False,
        "is_selected": False,
        "created_at": (_NOW - timedelta(days=8)).isoformat(),
    }

    # ── Prospect 2 — LOI Review ────────────────────────────────────────────────
    p2_id = _uid("prospect_indiranagar_bengaluru")
    db["prospects"][p2_id] = {
        "prospect_id": p2_id,
        "org_id": xtract_org_id,
        "location_name": "Indiranagar 100 Feet Road",
        "address": "No. 12, 100 Feet Road, Indiranagar, Bengaluru 560038",
        "city": "Bengaluru",
        "country": "India",
        "landlord_id": "",
        "landlord_name": "Brigade Group",
        "broker_name": None,
        "stage": "loi_review",
        "loi_action": "create_loi",
        "created_by": "Sarah Chen",
        "created_at": (_NOW - timedelta(days=30)).isoformat(),
        "updated_at": (_NOW - timedelta(days=5)).isoformat(),
        "finalized_at": (_NOW - timedelta(days=7)).isoformat(),
        "notes": "Premium high-street location. Agreed terms on paper, LOI drafted.",
        "size_sqft": 5200,
        "use_type": "Retail",
        "target_open_date": "2026-12-01",
    }

    q2a = _uid()
    db["price_quotes"][q2a] = {
        "quote_id": q2a,
        "prospect_id": p2_id,
        "quoted_by": "Brigade Group",
        "landlord_id": "",
        "base_rent_monthly": 420000,
        "currency": "INR",
        "rent_free_months": 4,
        "lease_term_years": 7,
        "fit_out_contribution": 4500000,
        "cam_estimated_monthly": 48000,
        "escalation_pct": 5.0,
        "key_terms": "Renewal options 1×5 at market. Demolition rights to landlord after year 5 with 12-month notice.",
        "notes": "Finalized and selected",
        "source": "manual",
        "raw_email_text": None,
        "extracted_at": None,
        "is_shortlisted": True,
        "is_selected": True,
        "created_at": (_NOW - timedelta(days=25)).isoformat(),
    }

    loi2_id = _uid()
    loi2_text = """LETTER OF INTENT (NON-BINDING)

May 14, 2026

To: Brigade Group
Re: No. 12, 100 Feet Road, Indiranagar, Bengaluru 560038

1. PREMISES. Approximately 5,200 rentable sq ft at No. 12, 100 Feet Road, Indiranagar, Bengaluru.

2. TERM. Seven (7) years commencing on or about 1 December 2026, with one (1) option of five (5) years.

3. BASE RENT. INR 4,20,000/month for Years 1-2, with annual escalation of 5% per annum.

4. RENT-FREE PERIOD. Four (4) months rent-free from commencement for fit-out.

5. FIT-OUT CONTRIBUTION. INR 45,00,000 toward Tenant fit-out, payable within 60 days of lease execution.

6. PERMITTED USE. Retail sale of apparel and accessories.

7. ASSIGNMENT. Tenant shall have right to assign to any affiliate without Landlord consent.

8. RENT REVIEW. Rent shall be reviewed upward-only at end of Year 3 by reference to the CPI All India Index, with a floor of 5% increase regardless of market conditions.

9. MAKE GOOD. On expiry, Tenant shall restore Premises to base building condition to Landlord's reasonable satisfaction, including removal of all fixtures and fittings installed by Tenant.

10. EXCLUSIVITY. Landlord shall not lease any space within the building to any direct competitor of Tenant.

11. DEMOLITION. Landlord reserves the right to terminate this lease upon 12 months' written notice after Year 5 for redevelopment, paying Tenant a relocation allowance of INR 20,00,000.

12. CONDITIONS PRECEDENT. Subject to Tenant's board approval within 30 days.

13. CONFIDENTIALITY. The parties agree to keep the terms of this LOI confidential.
"""
    db["loi_documents"][loi2_id] = {
        "loi_id": loi2_id,
        "prospect_id": p2_id,
        "source": "ai_drafted",
        "raw_text": loi2_text,
        "file_name": None,
        "ai_analysis_status": "done",
        "ai_summary": "3 clauses flagged. Rent review (Clause 8) is aggressive with upward-only 5% floor. Make-good (Clause 9) is open-ended. Demolition (Clause 11) creates exit risk from Year 5.",
        "overall_risk": "medium",
        "analyzed_at": (_NOW - timedelta(days=4)).isoformat(),
        "created_at": (_NOW - timedelta(days=7)).isoformat(),
        "created_by": "AI (Claude)",
    }

    c2_1 = _uid()
    db["loi_clauses"][c2_1] = {
        "clause_id": c2_1, "loi_id": loi2_id, "prospect_id": p2_id,
        "clause_number": 8, "clause_title": "Rent Review Mechanism",
        "clause_text": "Rent shall be reviewed upward-only at end of Year 3 by MSCI All Retail Index, with a floor of 5% increase regardless of market conditions.",
        "risk_level": "high",
        "ai_reasoning": "Upward-only review with a 5% floor bypasses market conditions entirely. Tenant should negotiate for bilateral market review or cap at CPI.",
        "ai_flagged": True, "human_status": "pending_review", "human_note": None,
        "counter_email_draft": None, "counter_email_approved": False, "counter_email_approved_at": None,
        "created_at": (_NOW - timedelta(days=4)).isoformat(),
    }
    c2_2 = _uid()
    db["loi_clauses"][c2_2] = {
        "clause_id": c2_2, "loi_id": loi2_id, "prospect_id": p2_id,
        "clause_number": 9, "clause_title": "Make-Good Obligations",
        "clause_text": "On expiry, Tenant shall restore Premises to base building condition to Landlord's reasonable satisfaction, including removal of all fixtures and fittings.",
        "risk_level": "medium",
        "ai_reasoning": "'Landlord's reasonable satisfaction' is subjective and could expose Tenant to significant end-of-term costs. Recommend capping make-good at a fixed sum.",
        "ai_flagged": True, "human_status": "pending_review", "human_note": None,
        "counter_email_draft": None, "counter_email_approved": False, "counter_email_approved_at": None,
        "created_at": (_NOW - timedelta(days=4)).isoformat(),
    }
    c2_3 = _uid()
    db["loi_clauses"][c2_3] = {
        "clause_id": c2_3, "loi_id": loi2_id, "prospect_id": p2_id,
        "clause_number": 11, "clause_title": "Demolition / Redevelopment Right",
        "clause_text": "Landlord reserves right to terminate upon 12 months' notice after Year 5, paying Tenant a relocation allowance of INR 20,00,000.",
        "risk_level": "medium",
        "ai_reasoning": "A ₹20L relocation allowance is likely insufficient to cover Tenant's actual relocation costs and business disruption. Recommend increasing or extending notice to 18–24 months.",
        "ai_flagged": True, "human_status": "pending_review", "human_note": None,
        "counter_email_draft": None, "counter_email_approved": False, "counter_email_approved_at": None,
        "created_at": (_NOW - timedelta(days=4)).isoformat(),
    }
    c2_4 = _uid()
    db["loi_clauses"][c2_4] = {
        "clause_id": c2_4, "loi_id": loi2_id, "prospect_id": p2_id,
        "clause_number": 6, "clause_title": "Permitted Use",
        "clause_text": "Retail sale of apparel and accessories. Tenant may use Premises for any lawful retail purpose ancillary thereto.",
        "risk_level": "low",
        "ai_reasoning": "Broadly worded. Minor risk — consider broadening to 'lifestyle retail' to preserve flexibility for future product line changes.",
        "ai_flagged": True, "human_status": "accepted",
        "human_note": "Accepted — broad enough for our current portfolio strategy.",
        "counter_email_draft": None, "counter_email_approved": False, "counter_email_approved_at": None,
        "created_at": (_NOW - timedelta(days=4)).isoformat(),
    }

    # ── Prospect 3 — Negotiating ───────────────────────────────────────────────
    p3_id = _uid("prospect_cyberhub_gurugram")
    db["prospects"][p3_id] = {
        "prospect_id": p3_id,
        "org_id": xtract_org_id,
        "location_name": "Cyber Hub, Gurugram",
        "address": "DLF Cyber Hub, DLF Phase 2, Gurugram, Haryana 122002",
        "city": "Gurugram",
        "country": "India",
        "landlord_id": "",
        "landlord_name": "DLF Limited",
        "broker_name": "JLL India",
        "stage": "negotiating",
        "loi_action": "request_loi",
        "created_by": "Sarah Chen",
        "created_at": (_NOW - timedelta(days=45)).isoformat(),
        "updated_at": (_NOW - timedelta(days=1)).isoformat(),
        "finalized_at": (_NOW - timedelta(days=20)).isoformat(),
        "notes": "High-traffic mixed-use hub. Landlord responsive.",
        "size_sqft": 3800,
        "use_type": "Retail",
        "target_open_date": "2026-09-01",
    }

    q3a = _uid()
    db["price_quotes"][q3a] = {
        "quote_id": q3a,
        "prospect_id": p3_id,
        "quoted_by": "DLF Limited",
        "landlord_id": None,
        "base_rent_monthly": 380000,
        "currency": "INR",
        "rent_free_months": 3,
        "lease_term_years": 5,
        "fit_out_contribution": 3000000,
        "cam_estimated_monthly": 42000,
        "escalation_pct": 5.0,
        "key_terms": "Standard commercial terms. Renewal options 1×5 at fixed 5% increase.",
        "notes": None,
        "source": "manual",
        "raw_email_text": None,
        "extracted_at": None,
        "is_shortlisted": True,
        "is_selected": True,
        "created_at": (_NOW - timedelta(days=40)).isoformat(),
    }

    loi3_id = _uid()
    db["loi_documents"][loi3_id] = {
        "loi_id": loi3_id,
        "prospect_id": p3_id,
        "source": "pasted",
        "raw_text": "LOI received from DLF Limited dated April 28, 2026.\n[Three clauses flagged for negotiation: Rent Review, Assignment, Make-Good]",
        "file_name": "jll_gurugram_loi_v2.pdf",
        "ai_analysis_status": "done",
        "ai_summary": "Three clauses flagged. Rent review is upward-only. Assignment requires landlord consent for all transfers. Make-good scope is broad.",
        "overall_risk": "medium",
        "analyzed_at": (_NOW - timedelta(days=15)).isoformat(),
        "created_at": (_NOW - timedelta(days=18)).isoformat(),
        "created_by": "Sarah Chen",
    }

    counter_draft = """Dear Mr. Sharma,

Thank you for the LOI dated April 28, 2026, for the premises at DLF Cyber Hub, Gurugram.

Regarding Clause 8 (Rent Review), we have carefully reviewed the proposed upward-only mechanism. While we appreciate the Landlord's position, a 5% minimum annual floor does not align with our portfolio management standards, particularly given current market conditions in the Gurugram micro-market.

We propose the following amendment:

"Rent shall be reviewed at the end of Year 3 on an open-market basis. Any increase shall be capped at the lesser of (i) 5% or (ii) the CPI change over the review period, with no minimum floor."

This provides a balanced position that protects both parties from market volatility. We believe this is commercially reasonable and consistent with current Gurugram market practice.

We look forward to your response.

Kind regards,
Sarah Chen
Head of Real Estate"""

    c3_1 = _uid()
    db["loi_clauses"][c3_1] = {
        "clause_id": c3_1, "loi_id": loi3_id, "prospect_id": p3_id,
        "clause_number": 8, "clause_title": "Rent Review Mechanism",
        "clause_text": "Rent shall be reviewed upward-only at end of Year 3, minimum 5% increase, maximum 8% per review cycle.",
        "risk_level": "high",
        "ai_reasoning": "Minimum 5% floor compounds significantly over a 5-year term. Current Gurugram CPI is ~4.5%. Year-5 rent would be ~27% above initial. Consider capping at CPI or bilateral market review.",
        "ai_flagged": True, "human_status": "counter_proposed",
        "human_note": "Proposed bilateral market review capped at CPI.",
        "counter_email_draft": counter_draft,
        "counter_email_approved": True,
        "counter_email_approved_at": (_NOW - timedelta(days=3)).isoformat(),
        "created_at": (_NOW - timedelta(days=14)).isoformat(),
    }
    c3_2 = _uid()
    db["loi_clauses"][c3_2] = {
        "clause_id": c3_2, "loi_id": loi3_id, "prospect_id": p3_id,
        "clause_number": 12, "clause_title": "Assignment and Subletting",
        "clause_text": "Tenant may not assign this lease without Landlord consent, except to wholly-owned subsidiaries subject to change of control carve-out.",
        "risk_level": "medium",
        "ai_reasoning": "Change of control carve-out blocks group M&A events. Recommend excluding intra-group restructures and corporate reorganisations from consent requirement.",
        "ai_flagged": True, "human_status": "agreed",
        "human_note": "Landlord accepted our redline — intra-group restructures now excluded.",
        "counter_email_draft": None, "counter_email_approved": False, "counter_email_approved_at": None,
        "created_at": (_NOW - timedelta(days=14)).isoformat(),
    }
    c3_3 = _uid()
    db["loi_clauses"][c3_3] = {
        "clause_id": c3_3, "loi_id": loi3_id, "prospect_id": p3_id,
        "clause_number": 15, "clause_title": "Make-Good Obligations",
        "clause_text": "On expiry, Tenant shall restore Premises to base building condition to Landlord's reasonable satisfaction.",
        "risk_level": "medium",
        "ai_reasoning": "'Landlord's reasonable satisfaction' is subjective. Recommend agreeing a dilapidations schedule or capping obligations at a fixed sum at lease commencement.",
        "ai_flagged": True, "human_status": "pending_review", "human_note": None,
        "counter_email_draft": None, "counter_email_approved": False, "counter_email_approved_at": None,
        "created_at": (_NOW - timedelta(days=14)).isoformat(),
    }

    t1_id = _uid()
    db["negotiation_threads"][t1_id] = {
        "thread_id": t1_id, "prospect_id": p3_id, "clause_id": c3_1,
        "event_type": "counter_approved", "actor": "Sarah Chen", "actor_name": "Sarah Chen",
        "content": "Counter email approved and queued for Rent Review Mechanism clause.",
        "created_at": (_NOW - timedelta(days=3)).isoformat(),
    }
    t2_id = _uid()
    db["negotiation_threads"][t2_id] = {
        "thread_id": t2_id, "prospect_id": p3_id, "clause_id": c3_2,
        "event_type": "status_changed", "actor": "Sarah Chen", "actor_name": "Sarah Chen",
        "content": "Assignment clause marked as agreed — landlord accepted intra-group restructure carve-out.",
        "created_at": (_NOW - timedelta(days=5)).isoformat(),
    }

    # ── Prospect 4 — LOI Signed ────────────────────────────────────────────────
    p4_id = _uid("prospect_aerocity_delhi")
    db["prospects"][p4_id] = {
        "prospect_id": p4_id,
        "org_id": xtract_org_id,
        "location_name": "Phoenix Marketcity, Chennai",
        "address": "Velachery Main Road, Velachery, Chennai 600042",
        "city": "Chennai",
        "country": "India",
        "landlord_id": "",
        "landlord_name": "Phoenix Mills",
        "broker_name": "Cushman & Wakefield India",
        "stage": "loi_signed",
        "loi_action": "request_loi",
        "created_by": "Sarah Chen",
        "created_at": (_NOW - timedelta(days=90)).isoformat(),
        "updated_at": (_NOW - timedelta(days=10)).isoformat(),
        "finalized_at": (_NOW - timedelta(days=60)).isoformat(),
        "loi_signed_at": (_NOW - timedelta(days=10)).isoformat(),
        "signer_name": "Sarah Chen",
        "notes": "LOI signed. Awaiting formal lease documentation.",
        "size_sqft": 4800,
        "use_type": "Retail",
        "target_open_date": "2026-07-01",
    }

    q4a = _uid()
    db["price_quotes"][q4a] = {
        "quote_id": q4a,
        "prospect_id": p4_id,
        "quoted_by": "Phoenix Mills",
        "landlord_id": "",
        "base_rent_monthly": 360000,
        "currency": "INR",
        "rent_free_months": 3,
        "lease_term_years": 5,
        "fit_out_contribution": 2800000,
        "cam_estimated_monthly": 38000,
        "escalation_pct": 5.0,
        "key_terms": "Agreed terms. All clauses resolved. Renewal 1×5 at market.",
        "notes": "Final agreed rent",
        "source": "manual",
        "raw_email_text": None,
        "extracted_at": None,
        "is_shortlisted": True,
        "is_selected": True,
        "created_at": (_NOW - timedelta(days=75)).isoformat(),
    }

    loi4_id = _uid()
    db["loi_documents"][loi4_id] = {
        "loi_id": loi4_id,
        "prospect_id": p4_id,
        "source": "pasted",
        "raw_text": "LOI executed May 4, 2026. All terms agreed. Formal lease execution in progress.",
        "file_name": "phoenix_chennai_loi_signed.pdf",
        "ai_analysis_status": "done",
        "ai_summary": "All three initially flagged clauses were resolved through negotiation. LOI executed on May 4, 2026.",
        "overall_risk": "low",
        "analyzed_at": (_NOW - timedelta(days=25)).isoformat(),
        "created_at": (_NOW - timedelta(days=35)).isoformat(),
        "created_by": "Sarah Chen",
    }
    for title, number in [("Rent Review", 6), ("Make-Good", 11), ("Assignment", 13)]:
        c_id = _uid()
        db["loi_clauses"][c_id] = {
            "clause_id": c_id, "loi_id": loi4_id, "prospect_id": p4_id,
            "clause_number": number, "clause_title": title,
            "clause_text": f"[{title} — agreed redline]",
            "risk_level": "medium",
            "ai_reasoning": "Originally flagged. Resolved through negotiation.",
            "ai_flagged": True, "human_status": "agreed",
            "human_note": "Agreed and signed.",
            "counter_email_draft": None, "counter_email_approved": True,
            "counter_email_approved_at": (_NOW - timedelta(days=15)).isoformat(),
            "created_at": (_NOW - timedelta(days=25)).isoformat(),
        }

    # ── Prospect 5 — lease_review (Worli, Mumbai) ──────────────────────────────
    p5_id = _uid("prospect_anna_salai_chennai")
    db["prospects"][p5_id] = {
        "prospect_id": p5_id, "org_id": xtract_org_id,
        "location_name": "Worli", "address": "Unit 3A, Piramal Agastya Corporate Park, Worli, Mumbai 400030",
        "city": "Mumbai", "country": "India",
        "landlord_name": "Piramal Realty", "broker_name": "Savills India",
        "stage": "lease_review", "loi_action": "create_loi",
        "created_by": "Sarah Chen",
        "created_at": (_NOW - timedelta(days=90)).isoformat(),
        "updated_at": (_NOW - timedelta(days=3)).isoformat(),
        "finalized_at": (_NOW - timedelta(days=60)).isoformat(),
        "loi_signed_at": (_NOW - timedelta(days=14)).isoformat(),
        "signer_name": "Aswatth Krishna",
        "notes": "Lease agreement received. 2 critical mismatches found — rent and escalation.", "size_sqft": 3200, "use_type": "Retail",
        "target_open_date": "2026-12-01",
    }
    q5a = _uid()
    db["price_quotes"][q5a] = {
        "quote_id": q5a, "prospect_id": p5_id, "quoted_by": "Savills India",
        "base_rent_monthly": 180000, "currency": "INR",
        "rent_free_months": 3, "lease_term_years": 5,
        "fit_out_contribution": 2000000, "cam_estimated_monthly": 25000,
        "escalation_pct": 5.0, "key_terms": "Option to renew 1×5. No sub-let.",
        "source": "manual", "is_shortlisted": True, "is_selected": True,
        "created_at": (_NOW - timedelta(days=80)).isoformat(),
    }
    loi5_id = _uid()
    db.setdefault("loi_documents", {})[loi5_id] = {
        "loi_id": loi5_id, "prospect_id": p5_id, "source": "pasted",
        "raw_text": "LOI for Worli, Mumbai. Base Rent ₹1,80,000/mo. Term 5 yrs. Rent-Free 3 months. FO ₹20L. Escalation 5% p.a.",
        "ai_analysis_status": "done", "overall_risk": "low",
        "analyzed_at": (_NOW - timedelta(days=20)).isoformat(),
        "created_at": (_NOW - timedelta(days=30)).isoformat(), "created_by": "Sarah Chen",
    }
    # Lease doc with critical mismatches
    lease5_id = _uid()
    db.setdefault("lease_docs", {})[p5_id] = {
        "lease_doc_id": lease5_id, "prospect_id": p5_id,
        "file_name": "piramal_worli_lease_draft.pdf",
        "raw_text": None, "ai_analysis_status": "done",
        "overall_status": "critical_mismatch",
        "analyzed_at": (_NOW - timedelta(days=3)).isoformat(),
        "created_at": (_NOW - timedelta(days=3)).isoformat(), "created_by": "Sarah Chen",
    }
    db.setdefault("lease_mismatches", {})
    for field, loi_val, lease_val, status in [
        ("Base Rent",         "₹1,80,000/mo", "₹1,95,000/mo", "critical_mismatch"),
        ("Lease Term",        "5 years",       "5 years",       "match"),
        ("Rent-Free Period",  "3 months",      "2 months",      "minor_mismatch"),
        ("Annual Escalation", "5%",            "7%",            "critical_mismatch"),
        ("Security Deposit",  "Not in LOI",    "6 months rent", "new_clause"),
    ]:
        mid = _uid()
        db["lease_mismatches"][mid] = {
            "mismatch_id": mid, "lease_doc_id": lease5_id, "prospect_id": p5_id,
            "field_name": field, "loi_value": loi_val, "lease_value": lease_val,
            "status": status, "dispute_email_draft": None,
            "dispute_email_approved": False, "resolved": False,
            "created_at": (_NOW - timedelta(days=3)).isoformat(),
        }

    # ── Prospect 6 — due_diligence (Lower Parel, Mumbai) ──────────────────────
    p6_id = _uid("prospect_hinjewadi_pune")
    db["prospects"][p6_id] = {
        "prospect_id": p6_id, "org_id": xtract_org_id,
        "location_name": "Lower Parel", "address": "Unit 2B, Kamala Mills Compound, Lower Parel, Mumbai 400013",
        "city": "Mumbai", "country": "India",
        "landlord_name": "Ramesh Kumar Mehta", "broker_name": "Vestian Global",
        "stage": "due_diligence", "loi_action": "create_loi",
        "created_by": "Sarah Chen",
        "created_at": (_NOW - timedelta(days=120)).isoformat(),
        "updated_at": (_NOW - timedelta(days=5)).isoformat(),
        "finalized_at": (_NOW - timedelta(days=90)).isoformat(),
        "loi_signed_at": (_NOW - timedelta(days=30)).isoformat(),
        "signer_name": "Aswatth Krishna",
        "notes": "Lease terms verified. Running govt portal checks.", "size_sqft": 4500, "use_type": "Retail",
        "target_open_date": "2026-09-01",
    }
    q6a = _uid()
    db["price_quotes"][q6a] = {
        "quote_id": q6a, "prospect_id": p6_id, "quoted_by": "Vestian Global",
        "base_rent_monthly": 210000, "currency": "INR",
        "rent_free_months": 2, "lease_term_years": 5,
        "fit_out_contribution": 1500000, "cam_estimated_monthly": 28000,
        "escalation_pct": 5.0, "source": "manual",
        "is_shortlisted": True, "is_selected": True,
        "created_at": (_NOW - timedelta(days=110)).isoformat(),
    }
    dd_id = _uid()
    db.setdefault("dd_reports", {})[p6_id] = {
        "dd_report_id": dd_id, "prospect_id": p6_id,
        "extracted_landlord_name": "Ramesh Kumar Mehta",
        "extracted_property_address": "Unit 2B, Kamala Mills Compound, Lower Parel, Mumbai 400013",
        "extracted_survey_number": "CTS No. 5/4321, Lower Parel Division",
        "extracted_pan": "AABCR1234N",
        "overall_recommendation": "proceed", "risk_flags": [],
        "human_status": "pending", "human_note": None,
        "reviewed_by": None, "reviewed_at": None,
        "created_at": (_NOW - timedelta(days=5)).isoformat(),
    }
    db.setdefault("portal_checks", {})
    for check_type, label, status, result in [
        ("rera",        "MahaRERA Registration",   "verified", "RERA Reg. No.: P51900067890 — Active"),
        ("registry",    "Property Registry (IGR)",  "verified", "Owner: Ramesh Kumar Mehta — confirmed"),
        ("encumbrance", "Encumbrance Check",         "verified", "No outstanding loans or charges"),
    ]:
        cid = _uid()
        db["portal_checks"][cid] = {
            "check_id": cid, "dd_report_id": dd_id, "prospect_id": p6_id,
            "check_type": check_type, "status": status,
            "result_summary": result, "flagged_reason": None,
            "checked_at": (_NOW - timedelta(days=4)).isoformat(),
        }

    # ── Lower Parel — LOI, clauses, lease doc ─────────────────────────────────
    loi6_id = _uid()
    loi6_text = """LETTER OF INTENT (NON-BINDING)

April 2, 2026

To: Ramesh Kumar Mehta
Re: Unit 2B, Kamala Mills Compound, Lower Parel, Mumbai 400013

1. PREMISES. Approximately 4,500 rentable sq ft at Unit 2B, Kamala Mills Compound, Lower Parel.
2. TERM. Five (5) years commencing 1 September 2026, with one (1) option of five (5) years at market rent.
3. BASE RENT. INR 2,10,000 per month for Years 1–2, with annual escalation of 5% per annum.
4. RENT-FREE PERIOD. Two (2) months rent-free from commencement for fit-out works.
5. FIT-OUT CONTRIBUTION. INR 15,00,000 toward Tenant's fit-out, payable within 45 days of lease execution.
6. PERMITTED USE. Retail sale of lifestyle products and accessories. No competing F&B or entertainment use permitted.
7. RENT REVIEW. Reviewed at end of Year 3 by mutual agreement, any increase capped at 15% above then-prevailing rent.
8. ASSIGNMENT & SUBLETTING. No assignment or sublet without prior written Landlord consent.
9. MAKE-GOOD. On expiry, Tenant shall restore Premises to original condition, fair wear and tear excepted.
10. SECURITY DEPOSIT. Six (6) months' rent, refundable within 60 days of lease expiry.
11. LANDLORD'S ACCESS. Right to inspect with 48 hours' prior written notice during business hours.
12. GOVERNING LAW. Laws of India, jurisdiction of courts in Mumbai.
"""
    db["loi_documents"][loi6_id] = {
        "loi_id": loi6_id, "prospect_id": p6_id, "source": "uploaded",
        "file_name": "LOI_Lower_Parel_May2026.pdf", "raw_text": loi6_text,
        "ai_analysis_status": "done", "overall_risk": "high",
        "ai_summary": "3 high-risk clauses identified — rent review, assignment restrictions, and make-good obligations require negotiation.",
        "analyzed_at": (_NOW - timedelta(days=28)).isoformat(),
        "created_at": (_NOW - timedelta(days=30)).isoformat(), "created_by": "Sarah Chen",
    }

    counter6_draft = """Dear Mr. Mehta,

Thank you for the Letter of Intent dated April 2, 2026, for Unit 2B, Kamala Mills Compound, Lower Parel.

Regarding Clause 7 (Permitted Use), the current restriction to "lifestyle products and accessories" is unduly narrow for our brand's expanding product lines. We request that the permitted use be broadened to "retail sale of lifestyle, fashion, and home décor products."

Additionally, the phrase "no competing F&B or entertainment use" is ambiguous — we propose replacing it with "no food and beverage service or live entertainment events on the Premises."

We believe these amendments are commercially reasonable and in keeping with current Mumbai high-street retail practice.

We look forward to your favourable consideration.

Kind regards,
Aswatth Krishna
Director — Real Estate"""

    c6_1 = _uid()
    db["loi_clauses"][c6_1] = {
        "clause_id": c6_1, "loi_id": loi6_id, "prospect_id": p6_id,
        "clause_number": 3, "clause_title": "Base Rent Review",
        "clause_text": "INR 2,10,000 per month for Years 1–2, with annual escalation of 5% per annum.",
        "risk_level": "high",
        "ai_reasoning": "5% annual escalation compounds significantly over 5 years. Current Mumbai CPI is ~4.2%. Year-5 rent would be ₹2,58,000/mo — 23% above initial. Consider capping escalation at CPI or negotiating a fixed-term flat rate.",
        "ai_flagged": True, "human_status": "agreed",
        "human_note": "Accepted — in line with market standard for Lower Parel.",
        "counter_email_draft": None, "counter_email_approved": False, "counter_email_approved_at": None,
        "created_at": (_NOW - timedelta(days=28)).isoformat(),
    }
    c6_2 = _uid()
    db["loi_clauses"][c6_2] = {
        "clause_id": c6_2, "loi_id": loi6_id, "prospect_id": p6_id,
        "clause_number": 5, "clause_title": "Fit-Out Contribution",
        "clause_text": "INR 15,00,000 toward Tenant's fit-out, payable within 45 days of lease execution.",
        "risk_level": "medium",
        "ai_reasoning": "45-day payment window is tight if fit-out commences at lease execution. Recommend extending to 60 days post-commencement or tying disbursement to a milestone.",
        "ai_flagged": True, "human_status": "agreed",
        "human_note": "Accepted — landlord agreed to 60-day window.",
        "counter_email_draft": None, "counter_email_approved": False, "counter_email_approved_at": None,
        "created_at": (_NOW - timedelta(days=28)).isoformat(),
    }
    c6_3 = _uid()
    db["loi_clauses"][c6_3] = {
        "clause_id": c6_3, "loi_id": loi6_id, "prospect_id": p6_id,
        "clause_number": 7, "clause_title": "Permitted Use Restriction",
        "clause_text": "Retail sale of lifestyle products and accessories. No competing F&B or entertainment use permitted.",
        "risk_level": "high",
        "ai_reasoning": "Restriction to 'lifestyle products and accessories' is too narrow — excludes home décor, wellness, and tech accessories which are core SKUs. The 'no F&B' clause is ambiguous and could prohibit in-store sampling or pop-up activations.",
        "ai_flagged": True, "human_status": "counter_proposed",
        "human_note": "Counter proposed — requested broader 'lifestyle, fashion, and home décor' permitted use.",
        "counter_email_draft": counter6_draft, "counter_email_approved": True,
        "counter_email_approved_at": (_NOW - timedelta(days=20)).isoformat(),
        "created_at": (_NOW - timedelta(days=28)).isoformat(),
    }
    c6_4 = _uid()
    db["loi_clauses"][c6_4] = {
        "clause_id": c6_4, "loi_id": loi6_id, "prospect_id": p6_id,
        "clause_number": 9, "clause_title": "Assignment & Subletting",
        "clause_text": "No assignment or sublet without prior written Landlord consent.",
        "risk_level": "high",
        "ai_reasoning": "Blanket prohibition on assignment blocks intra-group restructures and any future M&A event. Standard practice is to permit assignment to wholly-owned subsidiaries and affiliates without consent.",
        "ai_flagged": True, "human_status": "agreed",
        "human_note": "Landlord accepted carve-out for intra-group transfers.",
        "counter_email_draft": None, "counter_email_approved": False, "counter_email_approved_at": None,
        "created_at": (_NOW - timedelta(days=28)).isoformat(),
    }
    c6_5 = _uid()
    db["loi_clauses"][c6_5] = {
        "clause_id": c6_5, "loi_id": loi6_id, "prospect_id": p6_id,
        "clause_number": 11, "clause_title": "Make-Good Obligations",
        "clause_text": "On expiry, Tenant shall restore Premises to original condition, fair wear and tear excepted.",
        "risk_level": "medium",
        "ai_reasoning": "'Original condition' is undefined — at handover or pre-fit-out? Recommend agreeing a photographic handover schedule to establish the baseline and capping obligations at ₹5,00,000.",
        "ai_flagged": True, "human_status": "agreed",
        "human_note": "Agreed — photographic schedule to be attached at lease execution.",
        "counter_email_draft": None, "counter_email_approved": False, "counter_email_approved_at": None,
        "created_at": (_NOW - timedelta(days=28)).isoformat(),
    }
    c6_6 = _uid()
    db["loi_clauses"][c6_6] = {
        "clause_id": c6_6, "loi_id": loi6_id, "prospect_id": p6_id,
        "clause_number": 13, "clause_title": "Landlord's Access Rights",
        "clause_text": "Right to inspect with 48 hours' prior written notice during business hours.",
        "risk_level": "low",
        "ai_reasoning": "48-hour notice with business-hours restriction is commercially standard and adequately protects Tenant's operations.",
        "ai_flagged": False, "human_status": "dismissed",
        "human_note": "No action needed — standard clause.",
        "counter_email_draft": None, "counter_email_approved": False, "counter_email_approved_at": None,
        "created_at": (_NOW - timedelta(days=28)).isoformat(),
    }

    # Lease doc for Lower Parel
    lease6_id = _uid()
    db.setdefault("lease_docs", {})[p6_id] = {
        "lease_doc_id": lease6_id, "prospect_id": p6_id,
        "file_name": "Lease_Agreement_LowerParel_Draft.pdf",
        "raw_text": None, "ai_analysis_status": "done",
        "overall_status": "critical_mismatch",
        "analyzed_at": (_NOW - timedelta(days=8)).isoformat(),
        "created_at": (_NOW - timedelta(days=8)).isoformat(), "created_by": "Sarah Chen",
    }
    db.setdefault("lease_mismatches", {})
    for field, loi_val, lease_val, status in [
        ("Base Rent",         "₹2,10,000/mo",  "₹2,25,000/mo",  "critical_mismatch"),
        ("Lease Term",        "5 years",        "5 years",        "match"),
        ("Fit-Out Contribution", "₹15,00,000",  "₹12,00,000",    "minor_mismatch"),
        ("Annual Escalation", "5%",             "7%",             "critical_mismatch"),
        ("Lock-in Period",    "Not in LOI",     "3 years (Tenant)", "new_clause"),
    ]:
        mid = _uid()
        db["lease_mismatches"][mid] = {
            "mismatch_id": mid, "lease_doc_id": lease6_id, "prospect_id": p6_id,
            "field_name": field, "loi_value": loi_val, "lease_value": lease_val,
            "status": status, "dispute_email_draft": None,
            "dispute_email_approved": False, "resolved": False,
            "created_at": (_NOW - timedelta(days=8)).isoformat(),
        }

    # ── Prospect 7 — lease_signed (Bengaluru, Indiranagar) ────────────────────
    p7_id = _uid("prospect_sector18_noida")
    db["prospects"][p7_id] = {
        "prospect_id": p7_id, "org_id": xtract_org_id,
        "location_name": "Indiranagar 100ft Road", "address": "No. 47, 100 Feet Road, Indiranagar, Bengaluru 560038",
        "city": "Bengaluru", "country": "India",
        "landlord_name": "Prestige Group", "broker_name": "Knight Frank India",
        "stage": "lease_signed", "loi_action": "create_loi",
        "created_by": "Sarah Chen",
        "created_at": (_NOW - timedelta(days=180)).isoformat(),
        "updated_at": (_NOW - timedelta(days=7)).isoformat(),
        "finalized_at": (_NOW - timedelta(days=150)).isoformat(),
        "loi_signed_at": (_NOW - timedelta(days=60)).isoformat(),
        "signer_name": "Aswatth Krishna",
        "notes": "Lease executed. Lease administration initiated.", "size_sqft": 2800, "use_type": "Retail",
        "target_open_date": "2026-07-01",
    }
    q7a = _uid()
    db["price_quotes"][q7a] = {
        "quote_id": q7a, "prospect_id": p7_id, "quoted_by": "Knight Frank India",
        "base_rent_monthly": 145000, "currency": "INR",
        "rent_free_months": 2, "lease_term_years": 5,
        "fit_out_contribution": 1200000, "cam_estimated_monthly": 18000,
        "escalation_pct": 5.0, "source": "manual",
        "is_shortlisted": True, "is_selected": True,
        "created_at": (_NOW - timedelta(days=170)).isoformat(),
    }

    # ── Prospect 8 — shortlisted (Koramangala, Bengaluru) ────────────────────
    p8_id = _uid("prospect_sg_highway_ahmedabad")
    db["prospects"][p8_id] = {
        "prospect_id": p8_id, "org_id": xtract_org_id,
        "location_name": "Koramangala 5th Block", "address": "No. 12, 5th Block, Koramangala, Bengaluru 560095",
        "city": "Bengaluru", "country": "India",
        "landlord_name": "Sobha Developers", "broker_name": "Square Yards Commercial",
        "stage": "shortlisted", "loi_action": None,
        "created_by": "Sarah Chen",
        "created_at": (_NOW - timedelta(days=15)).isoformat(),
        "updated_at": (_NOW - timedelta(days=2)).isoformat(),
        "finalized_at": None,
        "notes": "High youth footfall. Multiple cafe and fashion brands nearby.", "size_sqft": 2200, "use_type": "Retail",
        "target_open_date": "2027-01-01",
    }
    q8a = _uid()
    db["price_quotes"][q8a] = {
        "quote_id": q8a, "prospect_id": p8_id, "quoted_by": "Square Yards Commercial",
        "base_rent_monthly": 95000, "currency": "INR",
        "rent_free_months": 2, "lease_term_years": 5,
        "fit_out_contribution": 800000, "cam_estimated_monthly": 12000,
        "escalation_pct": 5.0, "source": "manual",
        "is_shortlisted": True, "is_selected": False,
        "created_at": (_NOW - timedelta(days=13)).isoformat(),
    }
    q8b = _uid()
    db["price_quotes"][q8b] = {
        "quote_id": q8b, "prospect_id": p8_id, "quoted_by": "Sobha Developers (direct)",
        "base_rent_monthly": 88000, "currency": "INR",
        "rent_free_months": 1, "lease_term_years": 5,
        "fit_out_contribution": 600000, "cam_estimated_monthly": 10000,
        "escalation_pct": 6.0, "source": "manual",
        "is_shortlisted": False, "is_selected": False,
        "created_at": (_NOW - timedelta(days=11)).isoformat(),
    }

    # ── Prospect 9 — finalized (Connaught Place, Delhi NCR) ──────────────────
    p9_id = _uid("prospect_saltlake_kolkata")
    db["prospects"][p9_id] = {
        "prospect_id": p9_id, "org_id": xtract_org_id,
        "location_name": "Connaught Place", "address": "A-Block, Inner Circle, Connaught Place, New Delhi 110001",
        "city": "Delhi NCR", "country": "India",
        "landlord_name": "NDMC Property", "broker_name": "Cushman & Wakefield India",
        "stage": "finalized", "loi_action": "request_loi",
        "created_by": "Sarah Chen",
        "created_at": (_NOW - timedelta(days=20)).isoformat(),
        "updated_at": (_NOW - timedelta(days=1)).isoformat(),
        "finalized_at": (_NOW - timedelta(days=1)).isoformat(),
        "notes": "Premium CP location. Terms finalised — ready to request LOI.", "size_sqft": 3000, "use_type": "Retail",
        "target_open_date": "2026-11-01",
    }
    q9a = _uid()
    db["price_quotes"][q9a] = {
        "quote_id": q9a, "prospect_id": p9_id, "quoted_by": "Cushman & Wakefield India",
        "base_rent_monthly": 320000, "currency": "INR",
        "rent_free_months": 3, "lease_term_years": 9,
        "fit_out_contribution": 3500000, "cam_estimated_monthly": 35000,
        "escalation_pct": 5.0, "source": "manual",
        "is_shortlisted": True, "is_selected": True,
        "created_at": (_NOW - timedelta(days=18)).isoformat(),
    }

    # ── Location Intel — India prospects ──────────────────────────────────────
    db.setdefault("location_intel", {})

    db["location_intel"][p5_id] = {
        "intel_id": _uid(), "prospect_id": p5_id, "status": "done",
        "footfall_score": 8.2, "competition_score": 7.0, "transit_score": 8.8, "attractions_score": 8.5,
        "overall_score": 8.2,
        "narrative": (
            "Worli is a premium mixed-use precinct in South Mumbai with exceptional connectivity to the BKC and Nariman Point CBD corridors via the Bandra-Worli Sea Link. "
            "Footfall is sustained by a dense residential catchment of HNI and upper-middle-income households within a 2 km radius. "
            "Competitive density is manageable — 4 comparable retailers within 500m — providing room for a differentiated brand entry. "
            "Proximity to Phoenix Mills Lower Parel (1.8 km) and Atria Mall (0.4 km) creates a natural retail cluster that drives cross-shopping traffic."
        ),
        "researched_at": (_NOW - timedelta(days=5)).isoformat(),
        "created_at": (_NOW - timedelta(days=5)).isoformat(),
    }

    db["location_intel"][p6_id] = {
        "intel_id": _uid(), "prospect_id": p6_id, "status": "done",
        "footfall_score": 8.8, "competition_score": 6.5, "transit_score": 9.0, "attractions_score": 8.0,
        "overall_score": 8.3,
        "narrative": (
            "Lower Parel has emerged as Mumbai's most dynamic retail and commercial district following the conversion of mill lands into mixed-use developments. "
            "The area records among the highest weekend footfall in Mumbai, driven by Phoenix Palladium, High Street Phoenix, and the Kamala Mills food & beverage cluster. "
            "Transit access is excellent — Lower Parel station (WR) is 400m away with connecting auto and cab services. "
            "Competition density is elevated in the F&B segment but remains low-to-moderate for fashion and lifestyle retail, presenting a clear market opportunity."
        ),
        "researched_at": (_NOW - timedelta(days=7)).isoformat(),
        "created_at": (_NOW - timedelta(days=7)).isoformat(),
    }

    db["location_intel"][p7_id] = {
        "intel_id": _uid(), "prospect_id": p7_id, "status": "done",
        "footfall_score": 8.5, "competition_score": 7.2, "transit_score": 8.0, "attractions_score": 8.8,
        "overall_score": 8.2,
        "narrative": (
            "Indiranagar 100 Feet Road is Bengaluru's most coveted high-street retail corridor, favoured by premium and aspirational brands. "
            "Footfall remains consistently high throughout the week, driven by the affluent residential neighbourhoods of Indiranagar, CMH Road, and Domlur. "
            "The Purple Line metro (Indiranagar station, 600m) significantly enhances connectivity and has boosted evening trade. "
            "Competitive density is moderate-to-high but the street commands premium positioning — the right brand mix ensures strong throughput."
        ),
        "researched_at": (_NOW - timedelta(days=10)).isoformat(),
        "created_at": (_NOW - timedelta(days=10)).isoformat(),
    }

    db["location_intel"][p8_id] = {
        "intel_id": _uid(), "prospect_id": p8_id, "status": "done",
        "footfall_score": 7.8, "competition_score": 6.8, "transit_score": 7.2, "attractions_score": 7.5,
        "overall_score": 7.4,
        "narrative": (
            "Koramangala 5th Block is a high-energy micro-market dominated by the 18–35 age demographic, making it ideal for youth-oriented retail and F&B concepts. "
            "Weekend pedestrian traffic is robust, sustained by proximity to residential apartments, co-working spaces, and multiple educational institutions. "
            "Transit access is adequate though the area lacks metro connectivity; cab and auto availability is high. "
            "Competition density is moderate — several fast-fashion and café chains are present — but differentiated concepts with strong brand identity perform well here."
        ),
        "researched_at": (_NOW - timedelta(days=2)).isoformat(),
        "created_at": (_NOW - timedelta(days=2)).isoformat(),
    }

    # ── Lease signature for Indiranagar (lease_signed) ────────────────────────
    db.setdefault("lease_signatures", {})
    sig7_id = _uid()
    db["lease_signatures"][sig7_id] = {
        "signature_id": sig7_id, "prospect_id": p7_id,
        "signer_name": "Aswatth Krishna",
        "signer_role": "RE Director",
        "document_reference": "Lease Agreement — Indiranagar 100ft Road, Bengaluru",
        "signed_at": (_NOW - timedelta(days=7)).isoformat(),
        "created_at": (_NOW - timedelta(days=7)).isoformat(),
    }

    # Enrich Lower Parel DD report with summary fields
    if p6_id in db.get("dd_reports", {}):
        db["dd_reports"][p6_id].update({
            "ownership_confirmed": True,
            "rera_status": "Registered",
            "encumbrance_status": "Clear",
            "ai_recommendation_text": (
                "All three portal checks have returned verified status for Kamala Mills Compound, Lower Parel. "
                "Property ownership is confirmed in the name of Ramesh Kumar Mehta, matching the lease documentation. "
                "RERA registration (P51900067890) is active with no compliance issues or builder violations on record. "
                "No encumbrances, liens, or outstanding bank charges are recorded against the property. "
                "The property is clear to proceed to lease execution."
            ),
        })

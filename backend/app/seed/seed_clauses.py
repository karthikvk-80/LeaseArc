from app.mock_db import db
import uuid
import random

random.seed(77)

CLAUSE_TEMPLATES = [
    # ── CAM ──────────────────────────────────────────────────────────────────
    {
        "clause_ref": "Clause 8.1",
        "clause_name": "CAM Administrative Uplift",
        "clause_type": "CAM",
        "full_text": "The Landlord may apply an administrative uplift of up to 15% on all Common Area Maintenance expenses recovered from tenants. This uplift is intended to cover the Landlord's overhead costs associated with managing and administering the common areas. The uplift shall be calculated on the gross CAM costs prior to any exclusions or adjustments.",
        "is_risky": True,
        "risk_level": "high",
        "risk_reason": "A 15% administrative uplift is significantly above market standard (3–5%). This clause allows the Landlord to inflate CAM charges by an additional 15% on top of actual costs, compounding any existing overcharges.",
        "risk_recommendation": "Negotiate the uplift cap down to 5% maximum, or require it to apply only to eligible recoverable expenses after exclusions.",
    },
    {
        "clause_ref": "Clause 8.4",
        "clause_name": "Management Fee Cap",
        "clause_type": "CAM",
        "full_text": "The Tenant's proportionate share of the Landlord's management fee shall not exceed 5% of gross operating costs for the relevant year. The management fee is calculated on total outgoings prior to the application of any exclusions. The Landlord shall provide an itemised breakdown of management fee components upon written request.",
        "is_risky": True,
        "risk_level": "medium",
        "risk_reason": "A 5% management fee cap calculated on gross costs (before exclusions) allows the Landlord to include excluded capital items in the base, inflating the fee. Market standard is 3–4% on eligible net operating costs.",
        "risk_recommendation": "Negotiate the cap to 3% applied only to recoverable operating expenses net of exclusions, with annual audit rights.",
    },
    {
        "clause_ref": "Clause 12.3",
        "clause_name": "HVAC Capital Exclusion",
        "clause_type": "CAM",
        "full_text": "Capital replacement of HVAC plant and equipment shall be excluded from Common Area Maintenance recoveries. The Landlord shall be solely responsible for the cost of replacing or renewing HVAC systems serving the common areas. Routine servicing and maintenance of HVAC systems shall remain a recoverable operating expense.",
        "is_risky": False,
        "risk_level": None,
        "risk_reason": None,
        "risk_recommendation": None,
    },
    {
        "clause_ref": "Clause 15.2",
        "clause_name": "Capital Expenditure Exclusion",
        "clause_type": "CAM",
        "full_text": "Expenditure of a capital nature, including but not limited to structural improvements, building envelope repairs, replacement of major plant or equipment with a useful life exceeding 5 years, shall not be recoverable as a Common Area Maintenance expense. The Landlord bears sole responsibility for all capital expenditure unless otherwise agreed in writing.",
        "is_risky": False,
        "risk_level": None,
        "risk_reason": None,
        "risk_recommendation": None,
    },
    {
        "clause_ref": "Clause 9.1",
        "clause_name": "Real Estate Tax Base Year",
        "clause_type": "CAM",
        "full_text": "The Tenant's liability for real estate taxes and municipal levies shall be limited to the increase in such taxes over and above the base year amount established at the commencement of this Lease. The base year shall be the first full calendar year of the Lease term. The Landlord shall provide copies of all tax assessment notices within 30 days of receipt.",
        "is_risky": True,
        "risk_level": "medium",
        "risk_reason": "The base year is defined as the first calendar year, which may not align with the commencement date. If the lease commences mid-year, the base year amount may be artificially low, increasing the Tenant's tax exposure in subsequent years.",
        "risk_recommendation": "Negotiate the base year to be the 12-month period immediately preceding the lease commencement date, or a mutually agreed fixed base amount.",
    },
    {
        "clause_ref": "Clause 10.2",
        "clause_name": "Utility Sub-Metering",
        "clause_type": "CAM",
        "full_text": "Utility costs for the common areas shall be allocated to tenants based on a sub-metering methodology approved by the Landlord. Where sub-meters are not installed, the Landlord may allocate utility costs based on the Tenant's proportionate floor area. The Landlord shall review and update the sub-metering methodology no less than once every three years.",
        "is_risky": True,
        "risk_level": "high",
        "risk_reason": "The clause gives the Landlord unilateral approval over the sub-metering methodology, with no requirement for Tenant consent or independent verification. Floor area allocation (fallback) may significantly overcharge tenants with low utility usage.",
        "risk_recommendation": "Require Tenant consent for any change to the sub-metering methodology and mandate an independent auditor to verify allocation calculations annually.",
    },
    # ── Financial ────────────────────────────────────────────────────────────
    {
        "clause_ref": "Clause 5.1",
        "clause_name": "Rent Escalation — Fixed Rate",
        "clause_type": "Financial",
        "full_text": "The base rent shall increase annually on each anniversary of the Lease Commencement Date by a fixed rate of 3% per annum. Such increases shall be cumulative and compounding. No adjustment shall be made to reflect any decrease in the Consumer Price Index or market rental rates during the Lease term.",
        "is_risky": True,
        "risk_level": "medium",
        "risk_reason": "A fixed 3% compounding escalation with no CPI downside protection means rent increases regardless of market conditions. Over a 10-year term, this results in a 34% cumulative rent increase even if market rents decline.",
        "risk_recommendation": "Negotiate a CPI-collar (e.g., CPI ±1% cap/floor) or include a market rent review right at 5-year intervals to reset to prevailing market rates.",
    },
    {
        "clause_ref": "Clause 5.3",
        "clause_name": "Late Payment Penalty",
        "clause_type": "Financial",
        "full_text": "In the event that any payment due under this Lease remains unpaid for more than 5 business days after the due date, the Tenant shall pay interest on the outstanding amount at a rate of 5% per annum above the Reserve Bank base rate, compounding daily. Such interest shall accrue from the original due date until the date of payment in full.",
        "is_risky": True,
        "risk_level": "medium",
        "risk_reason": "A 5% above-base compounding daily interest rate is punitive relative to market standard (typically 2–3% above base, simple interest). With current base rates, this could result in an effective penalty rate exceeding 10% per annum.",
        "risk_recommendation": "Negotiate to 2% above the base rate, simple interest, with a 10-business-day grace period before interest accrues.",
    },
    {
        "clause_ref": "Clause 6.2",
        "clause_name": "Security Deposit",
        "clause_type": "Financial",
        "full_text": "The Tenant shall provide a security deposit equivalent to three months' base rent, payable on execution of this Lease. The security deposit may be held by the Landlord in a non-interest-bearing account. The Landlord may apply the security deposit against any outstanding obligation of the Tenant without prior notice.",
        "is_risky": True,
        "risk_level": "high",
        "risk_reason": "A non-interest-bearing deposit held without Tenant consent before draw-down constitutes a significant financial exposure. The Landlord's right to apply deposits without notice removes the Tenant's opportunity to cure any alleged default.",
        "risk_recommendation": "Require the deposit to be held in an interest-bearing trust account, with interest credited to the Tenant. Require 14 days written notice before any draw-down, allowing the Tenant to remedy the alleged default.",
    },
    {
        "clause_ref": "Clause 7.1",
        "clause_name": "Outgoings — Tenant Contribution",
        "clause_type": "Financial",
        "full_text": "The Tenant shall pay its proportionate share of all outgoings associated with the Property, including council rates, water rates, land tax, building insurance, and all other statutory charges. The Tenant's proportionate share shall be calculated by dividing the Tenant's net lettable area by the total net lettable area of the building. Outgoings shall be payable monthly in advance as an estimate, with an annual reconciliation.",
        "is_risky": False,
        "risk_level": None,
        "risk_reason": None,
        "risk_recommendation": None,
    },
    {
        "clause_ref": "Clause 7.4",
        "clause_name": "Insurance Premium Recovery",
        "clause_type": "Financial",
        "full_text": "The Landlord shall maintain building insurance at replacement cost value and recover the full premium from tenants as part of outgoings. The Landlord shall select the insurer and policy terms at its absolute discretion. The Tenant has no right to challenge the quantum of the insurance premium provided it reflects the actual cost of the policy.",
        "is_risky": True,
        "risk_level": "medium",
        "risk_reason": "The Landlord's absolute discretion to select insurer and policy terms without benchmarking creates risk of above-market premiums being passed through. There is no mechanism to challenge the cost, even if materially above market.",
        "risk_recommendation": "Require the Landlord to obtain at least two competitive quotes annually and cap the recoverable premium at the lower of the actual cost or a market benchmark rate per square metre.",
    },
    # ── Operational ──────────────────────────────────────────────────────────
    {
        "clause_ref": "Clause 13.4",
        "clause_name": "Landlord Structural Obligations",
        "clause_type": "Operational",
        "full_text": "The Landlord shall maintain the structural elements of the building, including the roof, external walls, foundations, and primary load-bearing structure, in good repair and condition throughout the Lease term. The cost of structural repairs and maintenance shall be borne solely by the Landlord and shall not form part of any outgoings recovery from the Tenant.",
        "is_risky": False,
        "risk_level": None,
        "risk_reason": None,
        "risk_recommendation": None,
    },
    {
        "clause_ref": "Clause 14.6",
        "clause_name": "Snow Removal — Seasonal Scope",
        "clause_type": "Operational",
        "full_text": "Snow removal and ice treatment services shall be provided by the Landlord during the winter season, defined as 1 November to 31 March of each year. Services provided outside this seasonal window shall be at the Landlord's sole cost and shall not be included in Common Area Maintenance reconciliation statements.",
        "is_risky": False,
        "risk_level": None,
        "risk_reason": None,
        "risk_recommendation": None,
    },
    {
        "clause_ref": "Clause 16.1",
        "clause_name": "Permitted Use",
        "clause_type": "Operational",
        "full_text": "The Tenant shall use the Premises solely for the purpose of retail sale of clothing, accessories, and lifestyle products under the Tenant's trading name as notified to the Landlord at the date of this Lease. Any material change to the Tenant's trading format or brand positioning shall require the Landlord's prior written consent, not to be unreasonably withheld.",
        "is_risky": True,
        "risk_level": "medium",
        "risk_reason": "The permitted use clause is tied to the current trading name, which could restrict future rebranding, brand extensions, or sub-category expansion without Landlord consent. 'Not unreasonably withheld' is subjective and may cause disputes.",
        "risk_recommendation": "Broaden the permitted use to 'retail and related services' with the trading name as guidance only, removing the requirement for Landlord consent on brand evolution.",
    },
    {
        "clause_ref": "Clause 17.2",
        "clause_name": "Signage Rights",
        "clause_type": "Operational",
        "full_text": "The Tenant shall be entitled to install signage on the Premises fascia and directory boards within the centre in accordance with the Landlord's signage specifications, as amended from time to time. The Landlord reserves the right to require the Tenant to update or replace signage to comply with any revised specifications at the Tenant's cost.",
        "is_risky": True,
        "risk_level": "medium",
        "risk_reason": "The Landlord's right to amend signage specifications at any time and require the Tenant to bear update costs creates an open-ended financial obligation. Signage replacement can cost $20,000–$100,000 depending on size and complexity.",
        "risk_recommendation": "Cap the Tenant's signage upgrade obligation to once per 5-year period, with a cost cap agreed upfront. Require the Landlord to give 12 months' notice of any specification change.",
    },
    {
        "clause_ref": "Clause 18.1",
        "clause_name": "Fit-Out Obligations",
        "clause_type": "Operational",
        "full_text": "The Tenant shall complete a full fit-out of the Premises in accordance with the Landlord's Fit-Out Guide within 90 days of the Lease Commencement Date. Failure to complete the fit-out within the specified period shall entitle the Landlord to terminate this Lease on 30 days' written notice. The Tenant shall provide a fit-out programme and design drawings for the Landlord's approval prior to commencing works.",
        "is_risky": False,
        "risk_level": None,
        "risk_reason": None,
        "risk_recommendation": None,
    },
    {
        "clause_ref": "Clause 19.3",
        "clause_name": "Landlord Access Rights",
        "clause_type": "Operational",
        "full_text": "The Landlord reserves the right to enter the Premises at any time with not less than 48 hours' written notice for the purpose of inspection, maintenance, or repair. In emergency situations, the Landlord may enter the Premises without prior notice. The Landlord shall use all reasonable endeavours to minimise disruption to the Tenant's trading operations.",
        "is_risky": False,
        "risk_level": None,
        "risk_reason": None,
        "risk_recommendation": None,
    },
    # ── Restrictive ──────────────────────────────────────────────────────────
    {
        "clause_ref": "Schedule 4 §2",
        "clause_name": "Exclusivity Clause",
        "clause_type": "Restrictive",
        "full_text": "The Landlord grants the Tenant exclusivity in the sale of fast fashion apparel and accessories within the Centre, excluding any anchor tenants or department stores operating at the date of this Lease. The exclusivity right shall apply only to the Tenant's current format and shall not extend to any new concept stores, outlet formats, or digital-first retail.",
        "is_risky": True,
        "risk_level": "high",
        "risk_reason": "The exclusivity carve-outs for anchor tenants and department stores are broad and could allow direct competitors to operate within the Centre under a different format. The exclusion of 'new concept' stores is undefined and may be exploited to circumvent the exclusivity protection.",
        "risk_recommendation": "Remove the anchor tenant carve-out or define it narrowly. Define 'fast fashion' and 'accessories' with specific product category lists. Extend exclusivity to all retail formats that derive more than 30% of sales from the defined categories.",
    },
    {
        "clause_ref": "Schedule 4 §5",
        "clause_name": "Co-Tenancy Clause",
        "clause_type": "Restrictive",
        "full_text": "In the event that the anchor tenant(s) specified in Schedule 1 cease trading from the Centre for a continuous period exceeding 90 days, the Tenant shall be entitled to a rent reduction of 20% for the duration of the anchor vacancy. If the anchor vacancy continues for more than 12 months, the Tenant may terminate this Lease on 60 days' written notice.",
        "is_risky": False,
        "risk_level": None,
        "risk_reason": None,
        "risk_recommendation": None,
    },
    {
        "clause_ref": "Clause 21.1",
        "clause_name": "Radius Restriction",
        "clause_type": "Restrictive",
        "full_text": "During the Lease term and for a period of 12 months following its expiry or earlier termination, the Tenant shall not open or operate a retail store within a 2-kilometre radius of the Centre without the Landlord's prior written consent. Consent shall not be unreasonably withheld where the proposed new store is in a different retail precinct or shopping centre.",
        "is_risky": True,
        "risk_level": "high",
        "risk_reason": "A 2km radius restriction post-expiry for 12 months severely limits the Tenant's ability to relocate or open additional stores in the same trade area. This is above market standard (typically 500m during the term only, no post-expiry restriction). It could materially harm the Tenant's expansion strategy.",
        "risk_recommendation": "Negotiate the radius restriction to 500m, apply it only during the Lease term (remove the post-expiry period entirely), and exclude stores in enclosed shopping centres or airports.",
    },
    {
        "clause_ref": "Clause 22.3",
        "clause_name": "Assignment and Subletting",
        "clause_type": "Restrictive",
        "full_text": "The Tenant shall not assign this Lease or sublet the whole or any part of the Premises without the Landlord's prior written consent. Consent may be withheld at the Landlord's absolute discretion. Any assignee or subtenant must meet minimum financial covenant requirements as determined by the Landlord from time to time.",
        "is_risky": True,
        "risk_level": "high",
        "risk_reason": "Absolute discretion to withhold consent for assignment removes the Tenant's flexibility in a corporate restructure, sale of business, or financial stress scenario. This is a significant departure from market standard, which typically requires consent 'not to be unreasonably withheld'.",
        "risk_recommendation": "Replace 'absolute discretion' with 'not to be unreasonably withheld or delayed'. Define objective financial covenant thresholds (e.g., net worth ≥ Tenant's net worth at lease commencement) and add a deemed consent provision if the Landlord fails to respond within 30 days.",
    },
    {
        "clause_ref": "Clause 23.1",
        "clause_name": "Change of Control",
        "clause_type": "Restrictive",
        "full_text": "Any change in the effective control of the Tenant entity (including merger, acquisition, IPO, or sale of more than 50% of the voting shares) shall be deemed an assignment for the purposes of this Lease and shall require the Landlord's prior written consent. Consent shall not be unreasonably withheld where the acquiring entity has an equal or better credit rating.",
        "is_risky": False,
        "risk_level": None,
        "risk_reason": None,
        "risk_recommendation": None,
    },
    # ── Renewal ──────────────────────────────────────────────────────────────
    {
        "clause_ref": "Clause 25.1",
        "clause_name": "Option to Renew",
        "clause_type": "Renewal",
        "full_text": "The Tenant is granted one option to renew this Lease for a further term of 5 years, exercisable by written notice given to the Landlord not less than 12 months and not more than 18 months prior to the expiry of the current Lease term. The renewal rent shall be determined by market review as at the date of renewal. The option shall be of no force or effect if the Tenant is in default at the time of exercise.",
        "is_risky": True,
        "risk_level": "medium",
        "risk_reason": "The option exercise window (12–18 months prior) is narrow and inflexible. Missing the window — even by one day — results in the option lapsing with no recourse. Additionally, 'any default' voiding the option is overly broad and could be triggered by a minor technical breach.",
        "risk_recommendation": "Widen the exercise window to 6–18 months prior to expiry. Limit the default carve-out to material, unremedied defaults only, with a 30-day cure period after notice before the option is voided.",
    },
    {
        "clause_ref": "Clause 25.4",
        "clause_name": "Holdover Provisions",
        "clause_type": "Renewal",
        "full_text": "If the Tenant remains in occupation of the Premises after the expiry of the Lease term without executing a renewal or extension, the Tenant shall be deemed to hold over on a month-to-month basis at 150% of the last monthly rent payable under this Lease. Either party may terminate the holdover tenancy on 30 days' written notice.",
        "is_risky": True,
        "risk_level": "medium",
        "risk_reason": "A 150% holdover rent penalty creates significant cost exposure if lease renewal negotiations extend beyond the expiry date — a common occurrence. This can amount to an unexpected 50% rent surcharge during otherwise routine lease negotiations.",
        "risk_recommendation": "Negotiate holdover rent to 110–120% of the last rent payable, with a longer termination notice period (60–90 days) to allow time to complete renewal negotiations or vacate.",
    },
    {
        "clause_ref": "Clause 26.1",
        "clause_name": "Break Clause",
        "clause_type": "Renewal",
        "full_text": "The Tenant is granted a break right exercisable on the 5th anniversary of the Lease Commencement Date, upon giving not less than 12 months' prior written notice. The break right is conditional upon: (i) the Tenant not being in default; (ii) the Tenant having paid all rent and outgoings to date; and (iii) the Tenant delivering vacant possession of the Premises in the condition required by this Lease.",
        "is_risky": True,
        "risk_level": "high",
        "risk_reason": "The break clause conditions are unusually onerous. Requiring vacant possession in make-good condition and zero outstanding payments at the date of break — including disputed amounts — effectively allows the Landlord to frustrate the break right through any alleged default or outstanding CAM reconciliation.",
        "risk_recommendation": "Limit break conditions to: (i) material, unremedied defaults only; (ii) all undisputed payments current. Remove the make-good/vacant possession condition from the break trigger, treating it as a separate obligation post-break.",
    },
    {
        "clause_ref": "Clause 27.1",
        "clause_name": "Make-Good Obligations",
        "clause_type": "Renewal",
        "full_text": "Upon expiry or earlier termination of this Lease, the Tenant shall at its own cost reinstate the Premises to their original condition as at the Lease Commencement Date, fair wear and tear excepted. The Landlord may elect to accept a cash payment in lieu of physical make-good works, at an amount to be agreed between the parties.",
        "is_risky": False,
        "risk_level": None,
        "risk_reason": None,
        "risk_recommendation": None,
    },
]


def seed_clauses():
    if db["lease_clauses"]:
        return  # idempotent

    lease_ids = list(db["leases"].keys())

    for idx, lease_id in enumerate(lease_ids):
        lease_org_id = db["leases"][lease_id].get("org_id")
        rng = random.Random(idx * 31 + 7)  # deterministic per lease

        # High-demo leases: ensure ≥4 risky clauses
        high_demo = idx < 10
        risky_templates = [c for c in CLAUSE_TEMPLATES if c["is_risky"]]
        safe_templates = [c for c in CLAUSE_TEMPLATES if not c["is_risky"]]

        n_risky = rng.randint(4, 6) if high_demo else rng.randint(2, 4)
        n_safe = rng.randint(5, 7)

        selected_risky = rng.sample(risky_templates, min(n_risky, len(risky_templates)))
        selected_safe = rng.sample(safe_templates, min(n_safe, len(safe_templates)))
        selected = selected_risky + selected_safe
        rng.shuffle(selected)

        for tmpl in selected:
            clause_id = str(uuid.uuid4())
            db["lease_clauses"][clause_id] = {
                "clause_id": clause_id,
                "lease_id": lease_id,
                "org_id": lease_org_id,
                "clause_ref": tmpl["clause_ref"],
                "clause_name": tmpl["clause_name"],
                "clause_type": tmpl["clause_type"],
                "full_text": tmpl["full_text"],
                "is_risky": tmpl["is_risky"],
                "risk_level": tmpl["risk_level"],
                "risk_reason": tmpl["risk_reason"],
                "risk_recommendation": tmpl["risk_recommendation"],
            }

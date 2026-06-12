from __future__ import annotations

import re
from typing import Any

from .schemas import AttributeSpec


REFERENCE_HINTS: dict[str, Any] = {
    "by_suffix": {
        "Property name": {
            "hint": "Official name of the building, property, project, complex, center, or premises.",
            "look_in": "Cover page, recitals, definitions, premises description, schedules, address blocks.",
            "format": "Proper noun only.",
            "exclude": "Do not return tenant name, landlord name, city name alone, or generic building type.",
        },
        "Street": {
            "hint": "Street/road/address line for the leased property, excluding street number when separable.",
            "look_in": "Property address, premises description, land/building schedules.",
            "format": "Street name or address line only.",
            "exclude": "Do not use landlord/tenant mailing address unless it is clearly the property address.",
        },
        "Street no": {
            "hint": "Street number, plot number, CTS/survey number, building number, or equivalent property identifier.",
            "look_in": "Property address, land description, premises schedule.",
            "format": "Numeric/alphanumeric identifier as stated.",
            "exclude": "Do not include full street name unless inseparable from the identifier.",
        },
        "Postal code": {
            "hint": "Postal code, ZIP, PIN, or P.O. Box number for the leased property address.",
            "look_in": "Property address, premises/site description, schedules, same-site notice address.",
            "format": "Code only where possible; do not add labels like P.O. Box unless needed for clarity.",
            "exclude": "Do not use tenant's separate correspondence postal code.",
        },
        "City": {
            "hint": "City or municipality where the leased property is located.",
            "look_in": "Property address, land/building schedules, registration details.",
            "format": "City name only.",
            "exclude": "Do not return state/province or country alone.",
        },
        "County": {
            "hint": "County, district, taluka, village, emirate, or administrative area of the property.",
            "look_in": "Property address, land schedule, registration/valuation pages.",
            "format": "Administrative area name.",
            "exclude": "Return null if no comparable administrative area is stated.",
        },
        "State / province": {
            "hint": "State, province, region, or equivalent where the property is located.",
            "look_in": "Property address, registration details, governing location clauses.",
            "format": "State/province name.",
            "exclude": "Do not use incorporation jurisdiction unless it is also the property location.",
        },
        "Country": {
            "hint": "Country where the leased property is located.",
            "look_in": "Property address, governing law, registration details, metadata in document.",
            "format": "Full country name when possible.",
            "exclude": "Do not use party domicile if different from property location.",
        },
        "Building Type": {
            "hint": "Asset/building classification based on the property use.",
            "look_in": "Property description, recitals, premises schedule, permitted use clause.",
            "format": "Short descriptor such as Office, Retail, Industrial, Warehouse, Mixed-use.",
            "exclude": "Do not return the tenant's business type unless it also describes the asset.",
        },
        "Total building area": {
            "hint": "Total area of the entire building/property, preserving whether the source describes built-up, usable, leasable, rentable, carpet, or another area basis.",
            "look_in": "Building description, area schedules, valuation tables, annexures.",
            "format": "Area number, with the stated area basis and unit when needed to preserve meaning.",
            "exclude": "Do not silently substitute leased-premises or usable area for total built-up building area. If only a different area basis is stated, preserve that qualification.",
        },
        "UOM": {
            "hint": "Unit of measure tied to total building area.",
            "look_in": "Same table or sentence as Total building area.",
            "format": "sq ft, square feet, sq m, sqm, acres, etc.",
            "exclude": "Do not invent a unit when the area value is not supported.",
        },
        "Landlord Name": {
            "hint": "Full legal name and role of each current landlord in the lease chain, including direct landlord/sub-lessor and superior/head/ground landlord when supported.",
            "look_in": "Parties clause, recitals, underlying/head lease references, definitions, signature block, amendments, assignment documents.",
            "format": "One item per landlord with full legal entity/person name and a concise role.",
            "exclude": "Do not return tenant/licensee/sub-lessee name.",
        },
        "Tenant Name": {
            "hint": "Full legal name of tenant, lessee, licensee, sub-lessee, occupant, or assignee tenant.",
            "look_in": "Parties clause, recitals, signature block, amendments, assignment documents.",
            "format": "Full legal entity/person name including suffix.",
            "exclude": "Do not return landlord/licensor/sub-lessor name.",
        },
        "Effective Date": {
            "hint": "Effective date of the underlying lease.",
            "look_in": "Opening clause, cover page, recitals, effective-date language.",
            "format": "Date as stated; normalize only if unambiguous.",
            "exclude": "Do not replace the lease effective date with an amendment date unless the amendment expressly changes the lease effective date.",
        },
        "Execution Date": {
            "hint": "Date the document was signed or executed.",
            "look_in": "Signature block, execution clause, notary/registration block.",
            "format": "Date as stated; normalize only if unambiguous.",
            "exclude": "Do not use cover-page date if signature/execution date is separately stated.",
        },
        "Original Commencement Date": {
            "hint": "First commencement date under the original/base lease.",
            "look_in": "Base lease term clause, commencement certificate, recitals in amendments.",
            "format": "Date as stated; calculate only if the lease directly supplies enough inputs.",
            "exclude": "Do not replace with current renewal/amendment commencement date.",
        },
        "Rent Commencement Date": {
            "hint": "Date or condition when rent/license fee starts accruing or becoming payable, including separate commencement dates for tranches or portions of the premises.",
            "look_in": "Rent clause, commencement clause, free-rent/fit-out provisions, amendments.",
            "format": "Preserve conditional language. Return separate items when multiple simultaneous tranche or area-specific commencement dates apply.",
            "exclude": "Do not assume it equals lease commencement if rent-free language says otherwise.",
        },
        "Current Commencement Date": {
            "hint": "Commencement date of the currently operative term after amendments/renewals.",
            "look_in": "Latest amendment, renewal, term sheet, current term clause, base lease if no amendments.",
            "format": "Date as stated.",
            "exclude": "Do not use prior/original term date when later documents replace the term.",
        },
        "Current Expiration Date": {
            "hint": "Expiration/expiry/end date of the currently operative term.",
            "look_in": "Latest amendment, renewal, term sheet, current term clause, base lease if no amendments.",
            "format": "Date as stated.",
            "exclude": "Do not infer expiration only from the last rent period, bank guarantee validity, insurance expiry, or option deadline.",
        },
        "Original Expiration Date": {
            "hint": "Expiration/end date of the original/base lease term.",
            "look_in": "Base lease term clause, recitals of amendment/renewal.",
            "format": "Date as stated; calculate from original commencement plus duration only when directly supported.",
            "exclude": "Do not replace with current expiration date from amendments.",
        },
        "Possession Date": {
            "hint": "Date tenant receives or takes physical possession/access to premises.",
            "look_in": "Possession, handover, delivery, fit-out, commencement provisions.",
            "format": "Date as stated.",
            "exclude": "Do not assume commencement date unless possession is expressly tied to it.",
        },
        "Delivery Date": {
            "hint": "Date landlord delivers/hands over the premises.",
            "look_in": "Delivery, handover, possession, conditions precedent clauses.",
            "format": "Date as stated.",
            "exclude": "Return null if no delivery/handover date is stated.",
        },
        "Term Duration": {
            "hint": "Length of the lease term or current operative term.",
            "look_in": "Term clause, renewal/amendment term clause, schedule of basic terms.",
            "format": "Period string such as '10 years' or '5 years 2 months'.",
            "exclude": "Do not return dates only.",
        },
        "Lease Status": {
            "hint": "Current lease state such as active, expired, terminated, surrendered, renewed, executed, or registered.",
            "look_in": "Execution/registration data, latest amendment, surrender/termination documents, term dates.",
            "format": "Short status phrase.",
            "exclude": "Do not overstate active/expired if dates or documents do not support it.",
        },
        "Default": {
            "hint": "Default and remedies provisions, including monetary/non-monetary default, cure periods, notices, and remedies.",
            "look_in": "Default, breach, remedies, termination, events of default clauses.",
            "format": "Detailed factual abstraction with cure periods and remedies where stated.",
            "exclude": "Do not substitute an ordinary convenience termination right for a default provision. Use Lease is silent only if no default/remedy provision is addressed.",
        },
        "Estoppel": {
            "hint": "Estoppel certificate/statement obligations and delivery timeframe.",
            "look_in": "Estoppel, certificate, statement, confirmation clauses.",
            "format": "State who must deliver, to whom, timing, and required content.",
            "exclude": "Do not confuse with notices or financial statements.",
        },
        "Business Hours": {
            "hint": "Business/operating/access hours and after-hours service rules.",
            "look_in": "Business hours, operating hours, building rules, services/HVAC clauses, annexures.",
            "format": "Hours by day/category, plus exceptions or after-hours charges.",
            "exclude": "Use Lease is silent only if hours are not addressed anywhere.",
        },
        "Late Charges": {
            "hint": "Late fee, default interest, penalty, grace period, overdue charge, and compounding/payment basis.",
            "look_in": "Late payment, default interest, rent payment, default clauses.",
            "format": "Single paragraph with rate/amount, trigger, grace period, and payment basis.",
            "exclude": "Do not include general default remedies unless tied to late payment.",
        },
        "Repair and Maintenance": {
            "hint": "Landlord and tenant repair, maintenance, replacement, service-level, and upkeep obligations.",
            "look_in": "Repair, maintenance, tenant obligations, landlord services, annexures.",
            "format": "Detailed abstraction separating landlord and tenant duties where possible.",
            "exclude": "Do not collapse unrelated utility/payment obligations unless part of maintenance.",
        },
        "Insurance Requirements": {
            "hint": "Insurance obligations, coverage types/limits, additional insureds, proof, renewal, and cancellation notice.",
            "look_in": "Insurance, indemnity, compliance, casualty clauses.",
            "format": "Detailed abstraction of required coverage and obligations.",
            "exclude": "Do not confuse property insurance reimbursement with tenant liability coverage.",
        },
        "Parking": {
            "hint": "Parking rights, allocations, locations, reserved/open spaces, charges, and rules.",
            "look_in": "Grant clause, parking clause, common areas, schedules, premises description.",
            "format": "State number/type/location/charges/restrictions if stated.",
            "exclude": "Do not use general common area rights as parking unless parking is mentioned.",
        },
        "Signage": {
            "hint": "Signage rights, restrictions, approval requirements, locations, maintenance, and removal.",
            "look_in": "Signage, branding, name board, facade, directory, visual merchandising clauses.",
            "format": "Detailed abstraction of rights and approval/removal obligations.",
            "exclude": "Do not confuse signage lighting with general utilities unless connected.",
        },
        "Surrender": {
            "hint": "Surrender, yield-up, restoration, removal, handback condition, and settlement obligations.",
            "look_in": "Surrender, expiry, termination, restoration, removal, handback clauses.",
            "format": "Summarize the general handback obligations at expiry or termination, plus materially different special scenarios.",
            "exclude": "Do not treat assignment/sublet as surrender.",
        },
        "Holdover": {
            "hint": "Holdover/overstay consequences after expiry or termination.",
            "look_in": "Holdover, overholding, unauthorized occupation, continued possession clauses.",
            "format": "State rent multiplier/damages/status/remedies if stated.",
            "exclude": "Do not include ordinary renewal options unless tied to holdover.",
        },
        "Permitted Use": {
            "hint": "Specific permitted use(s) or activities allowed in the premises.",
            "look_in": "Use clause, permitted use clause, premises/business description, license details.",
            "format": "Description of permitted activity only.",
            "exclude": "Do not return only generic compliance language, access hours, building type, or prohibited uses when actual permitted activities are stated.",
        },
        "Assignment/Sublet": {
            "hint": "Assignment, subletting, transfer, sharing, affiliate occupancy, and consent requirements.",
            "look_in": "Assignment, sublease, transfer, sharing, alienation clauses.",
            "format": "Detailed abstraction of permitted/prohibited transfers and consent rules.",
            "exclude": "Do not confuse with permitted use.",
        },
        "Alterations": {
            "hint": "Alterations, improvements, fit-out works, approvals, removal, restoration, and contractor requirements.",
            "look_in": "Alterations, tenant works, fit-out, improvements, construction clauses.",
            "format": "Detailed abstraction of rights, approvals, and restoration/removal obligations.",
            "exclude": "Do not include ordinary repair obligations unless tied to alterations.",
        },
        "Operating Expenses": {
            "hint": "Operating expenses, CAM, maintenance charges, service charges, reimbursements, caps, exclusions, and audit/payment rules.",
            "look_in": "Operating expense, CAM, service charge, maintenance charge, additional rent clauses.",
            "format": "Detailed abstraction of charge responsibility and mechanics.",
            "exclude": "Do not create rent schedule rows here; those belong under Expenses.",
        },
        "RE Taxes": {
            "hint": "Real estate/property tax obligations, assessments, increases, reimbursements, rates, cess, and levies.",
            "look_in": "Tax, real estate tax, property tax, municipal tax, assessment clauses.",
            "format": "Detailed abstraction of allocation and payment/reimbursement mechanics.",
            "exclude": "Do not treat income tax or stamp duty as RE Taxes unless property taxes are stated.",
        },
        "Property Insurance": {
            "hint": "Property/building insurance obligations, premiums, reimbursement, insured property, and casualty coverage.",
            "look_in": "Insurance, property insurance, casualty, building insurance clauses.",
            "format": "Detailed abstraction distinguishing property insurance from liability insurance.",
            "exclude": "Do not duplicate general insurance requirements unless property-specific.",
        },
        "Restricted Uses": {
            "hint": "Uses that are restricted, conditioned, or require consent.",
            "look_in": "Use restrictions, rules, prohibited/restricted use, building regulations.",
            "format": "List or paragraph of restrictions and conditions.",
            "exclude": "Do not include permitted uses unless they define the restriction.",
        },
        "Prohibited Uses": {
            "hint": "Uses and activities expressly prohibited at the premises.",
            "look_in": "Prohibited use, restrictions, nuisance, hazardous materials, illegal use clauses.",
            "format": "List or paragraph of prohibited activities.",
            "exclude": "Do not infer prohibitions not stated.",
        },
        "Exclusive Use": {
            "hint": "Exclusive use rights, exclusivity protection, competing tenant restrictions, and carve-outs.",
            "look_in": "Exclusive use, non-exclusive, exclusivity, competing tenant clauses.",
            "format": "State exclusivity grant or explicit non-exclusive/no-exclusivity language.",
            "exclude": "Use null/not_found only if exclusivity is not addressed.",
        },
        "Percentage Rent (Payment)": {
            "hint": "Percentage/turnover rent payment obligation, breakpoint, rate, payment timing, and thresholds.",
            "look_in": "Percentage rent, turnover rent, gross sales rent clauses.",
            "format": "Detailed abstraction with rate/breakpoint/timing if stated.",
            "exclude": "Use null/not_found if percentage rent is not addressed.",
        },
        "Gross Sales (Reporting)": {
            "hint": "Gross sales reporting obligations, statements, audit rights, frequency, and exclusions.",
            "look_in": "Gross sales, sales reporting, percentage rent, audit clauses.",
            "format": "Detailed abstraction of reporting/audit requirements.",
            "exclude": "Do not report sales payment terms unless reporting is addressed.",
        },
        "Go dark": {
            "hint": "Go-dark, continuous operation, closure, vacancy, non-operation, and related remedies.",
            "look_in": "Continuous operation, go dark, closure, abandonment, operating covenant clauses.",
            "format": "Detailed abstraction of operation requirement or right to cease operations.",
            "exclude": "Do not confuse with business hours unless tied to continuous operation.",
        },
        "Co-Tenancy": {
            "hint": "Co-tenancy requirements, anchor tenant conditions, remedies, rent relief, and termination rights.",
            "look_in": "Co-tenancy, anchor tenant, occupancy condition clauses.",
            "format": "Detailed abstraction with triggers and remedies.",
            "exclude": "Use null/not_found if no co-tenancy provision is stated.",
        },
        "Radius Restrictions": {
            "hint": "Geographic restriction on competing locations/businesses, distance, duration, exceptions, and remedies.",
            "look_in": "Radius restriction, competing business, restricted area clauses.",
            "format": "Detailed abstraction with distance/geography and restricted business.",
            "exclude": "Do not infer a radius from exclusive-use language alone.",
        },
        "Tenant Improvement Allowance": {
            "hint": "TI/fit-out/work allowance amount, eligible costs, disbursement, documentation, deadlines, unused treatment.",
            "look_in": "Tenant improvement allowance, work letter, fit-out contribution, landlord works clauses.",
            "format": "Detailed abstraction with amount and conditions where stated.",
            "exclude": "Do not include security deposit or rent abatement unless described as allowance.",
        },
        "Brokers": {
            "hint": "Broker/agent names, commission obligations, representations, and indemnities.",
            "look_in": "Broker, agent, commission, brokerage clauses.",
            "format": "State landlord broker, tenant broker, obligations/indemnity if stated.",
            "exclude": "Use Lease is silent only if broker/agent provisions are absent.",
        },
        "Notices": {
            "hint": "Notice methods, addresses, delivery timing, deemed receipt, attention lines, and update procedure.",
            "look_in": "Notices clause, service of notices, party address blocks, amendments.",
            "format": "Detailed abstraction of notice mechanics and current addresses.",
            "exclude": "Do not omit later amendment address changes.",
        },
        "Base Rent Comments": {
            "hint": "Narrative base rent/license fee payment terms, concessions, escalations, abatements, amendments, or superseding notes.",
            "look_in": "Rent clause, payment terms, schedules, footnotes, amendments, term sheets.",
            "format": "Detailed abstraction of special rent terms.",
            "exclude": "Do not duplicate structured Expenses rows unless explaining rent comments.",
        },
        "Utilities": {
            "hint": "Utility supply, payment, metering, submetering, power backup, water, sewer, HVAC, excess/after-hours usage.",
            "look_in": "Utilities, services, power, water, sewerage, HVAC, metering clauses and annexures.",
            "format": "Detailed abstraction of utility responsibilities and payment mechanics.",
            "exclude": "Do not include unrelated operating expenses unless utilities are part of them.",
        },
        "Rent Type": {"hint": "Classification of rent/expense row.", "format": "Base Rent, License Fee, CAM, RET, INS, Parking Fee, etc.", "exclude": "Do not return an amount or date."},
        "Start date": {"hint": "Start date for this row/item.", "format": "Date as stated or directly calculated from supported term dates.", "exclude": "Do not use execution date unless it is the row start date."},
        "End date": {"hint": "End date for this row/item.", "format": "Date as stated or directly calculated from supported term dates.", "exclude": "Do not use option deadline unless it is the row end date."},
        "Monthly Amount": {"hint": "Monthly amount for this rent/expense row.", "format": "Numeric amount without commentary.", "exclude": "Do not include currency unless inseparable."},
        "Monthly Amount per SF": {"hint": "Monthly amount per square foot/metre.", "format": "Numeric amount.", "exclude": "Do not calculate unless area and total/monthly amount are clearly available."},
        "Annual Amount": {"hint": "Annual amount for this rent/expense row.", "format": "Numeric amount without commentary.", "exclude": "Do not include VAT/taxes unless the schedule includes them in the amount."},
        "Annual amount per SF": {"hint": "Annual amount per square foot/metre.", "format": "Numeric amount.", "exclude": "Do not calculate unless area and annual amount are clearly available."},
        "Currency": {"hint": "Currency for the amount.", "format": "Symbol/code/name exactly as stated.", "exclude": "Do not infer if not clear from context."},
        "On Day": {"hint": "Day of month or timing when payment is due.", "format": "e.g. first day of each month, 5th day, monthly in advance.", "exclude": "Do not invent a due day."},
        "Payment Frequency": {"hint": "Payment interval.", "format": "Monthly, Quarterly, Annually, One-time, etc.", "exclude": "Do not infer unless schedule clearly shows installments."},
        "Option Effective Date": {"hint": "Date or condition when the option first becomes exercisable.", "format": "Date or operative condition as stated.", "exclude": "Do not use the lease commencement date unless the option is exercisable from that date."},
        "Option Earliest Notice": {"hint": "Earliest date or period when notice exercising the option may be given.", "format": "Date, month range, or notice condition as stated.", "exclude": "Do not use a breach cure period."},
        "Option Latest Notice Deadline": {"hint": "Latest deadline or minimum advance notice required to exercise the option.", "format": "Date or notice period as stated.", "exclude": "A default cure period is not an option notice deadline. Resolve referenced clauses instead of returning only 'as per Clause X'."},
    },
    "core_address_note": "PROPERTY ADDRESS: use the leased premises/property address only. Postal code may be a PIN/ZIP/postcode/P.O. Box for the site; return the code only where possible.",
    "date_fields_note": "DATE FIELDS: source_clause must quote the sentence/table cell containing the date. Execution Date is signature/execution date when stated. Effective Date is made/effective/as-of date. Current dates come from latest operative amendment when it supersedes base terms.",
    "clauses_group_note": "CLAUSE FIELDS: value must be a detailed factual abstraction, not a one-line label. Include triggers, notice periods, amounts, approval requirements, party obligations, conditions, and exceptions. Use Lease is silent only when the topic is genuinely not addressed in provided context.",
    "repeatable_slots_note": "REPEATABLE ATTRIBUTES: return one enumerated item per distinct row/item found. Do not pad empty rows. Preserve document/order logic, and keep superseded history in trace rather than current enumerated_values.",
    "area_group_note": "AREA: one item per distinct premises/space row, suite, floor, phased area, added/reduced space, or non-consecutive area period. Preserve all stated decimal precision. When detailed floor rows are returned, do not also return aggregate Total/Premises Total rows. Normalize an obvious OCR floor label such as D/G to Ground when the matching schedule clearly identifies that row as Ground.",
    "expenses_group_note": "EXPENSES: one item per current rent schedule period or recurring charge line with a stated amount, including zero/abated periods. When a later schedule says rent shall stand revised, it replaces earlier rows for overlapping periods; keep superseded rows only in trace. Do not return overlapping current rent rows.",
    "allowance_group_note": "ALLOWANCE: one item per distinct allowance or landlord contribution, with amount, deadline, eligible costs, documentation, disbursement, unused treatment, and repayment conditions where stated.",
    "security_deposit_group_note": "SECURITY DEPOSIT: one item per separate deposit/security instrument or tranche, including cash deposit, bank guarantee, letter of credit, top-up, return, interest, deductions, and replenishment rules.",
    "options_group_note": "OPTIONS: one item per distinct renewal, extension, termination, expansion, contraction, ROFO/ROFR, purchase, relocation, or other option. Do not combine separate option rights.",
    "pass_notes": {
        "renewal_dates": "RENEWAL / AMENDMENT DOCUMENTS: Original Commencement/Expiration = prior/base term dates; Current Commencement/Expiration = latest operative current term. Never copy current term dates into original fields unless the document states they are original dates."
    },
}


def slugify_attribute_name(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip().lower()).strip("_")
    return re.sub(r"_+", "_", slug)


def hint_suffix(name: str) -> str:
    for prefix in ("Lease_Abstraction.", "Clauses."):
        if name.startswith(prefix):
            return name[len(prefix):]
    if "." in name:
        return name.rsplit(".", 1)[-1]
    return name


def reference_guidance(name: str, category: str, expected_fields: list[str], repeatable_hint: bool) -> dict[str, Any]:
    by_suffix = REFERENCE_HINTS.get("by_suffix", {})
    guidance: dict[str, Any] = {}
    suffix = hint_suffix(name)
    field_hint = by_suffix.get(suffix) if isinstance(by_suffix, dict) else None
    if isinstance(field_hint, dict):
        guidance.update({key: value for key, value in field_hint.items() if value})

    notes: list[str] = []
    if suffix == "Postal code":
        _append_note(notes, "core_address_note")
    if category == "Dates":
        _append_note(notes, "date_fields_note")
        pass_notes = REFERENCE_HINTS.get("pass_notes", {})
        if isinstance(pass_notes, dict):
            renewal_note = pass_notes.get("renewal_dates")
            if renewal_note:
                notes.append(str(renewal_note))
    if category == "Clauses":
        _append_note(notes, "clauses_group_note")
        guidance.setdefault(
            "value_style",
            "Return a detailed factual abstraction with operative rights, obligations, triggers, notice periods, amounts, approval requirements, conditions, and exceptions. Quote the supporting source clause separately.",
        )
    if repeatable_hint:
        _append_note(notes, "repeatable_slots_note")
        group_note_keys = {
            "Area": "area_group_note",
            "Expenses": "expenses_group_note",
            "Allowance": "allowance_group_note",
            "Security Deposit": "security_deposit_group_note",
            "Options": "options_group_note",
        }
        note_key = group_note_keys.get(name)
        if note_key:
            _append_note(notes, note_key)
        field_guidance = []
        for field in expected_fields:
            item = by_suffix.get(field) if isinstance(by_suffix, dict) else None
            if isinstance(item, dict):
                line = f"{field}: {item.get('hint') or ''}"
                if item.get("format"):
                    line += f" Format: {item['format']}"
                if item.get("exclude"):
                    line += f" Exclude: {item['exclude']}"
                field_guidance.append(line)
        if field_guidance:
            guidance["field_guidance"] = field_guidance
    if notes:
        guidance["special_rules"] = notes
    return guidance


def _append_note(notes: list[str], key: str) -> None:
    note = REFERENCE_HINTS.get(key)
    if note:
        notes.append(str(note))


def guidance_to_hints(guidance: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    labels = {
        "hint": "Definition",
        "look_in": "Look in",
        "format": "Expected format",
        "example": "Example",
        "exclude": "Exclude",
        "value_style": "Value style",
    }
    for key, label in labels.items():
        value = guidance.get(key)
        if value:
            hints.append(f"{label}: {value}")
    for item in guidance.get("field_guidance", []) or []:
        hints.append(f"Field guidance: {item}")
    for item in guidance.get("special_rules", []) or []:
        hints.append(f"Special rule: {item}")
    return hints


def spec(
    name: str,
    category: str,
    hints: list[str],
    *,
    expected_fields: list[str] | None = None,
    profiler_name: str | None = None,
    profiler_aliases: list[str] | None = None,
    repeatable_hint: bool = False,
) -> AttributeSpec:
    fields = expected_fields or ["Value"]
    guidance = reference_guidance(name, category, fields, repeatable_hint)
    merged_hints = guidance_to_hints(guidance) + hints
    return AttributeSpec(
        attribute_key=slugify_attribute_name(name),
        display_name=name,
        category=category,
        profiler_name=profiler_name or name,
        profiler_aliases=profiler_aliases or [],
        expected_fields=fields,
        hints=merged_hints,
        guidance=guidance,
        repeatable_hint=repeatable_hint,
    )


ATTRIBUTE_SPECS: list[AttributeSpec] = [
    spec("Property name", "Property", ["Extract the building, project, complex, mall, or property name for the leased premises.", "Look near recitals, definitions, schedules, premises descriptions, and property address blocks."]),
    spec("Street", "Property", ["Extract the street, road, block, complex road, or address line excluding city/state/country when possible.", "Use premises address, demised premises, schedule of land/building, and notice address only when it identifies the property."]),
    spec("Street no", "Property", ["Extract the street number, plot number, CTS number, survey number, building number, or equivalent property identifier.", "Prefer property identifiers in the premises or land description over party notice addresses."]),
    spec("Postal code", "Property", ["Extract ZIP/PIN/postal code for the leased property address.", "Use property schedule or premises address, not landlord or tenant mailing address unless it is clearly the property."]),
    spec("City", "Property", ["Extract the city or municipality of the leased property.", "Look for city names in the property address, schedule, and premises description."]),
    spec("County", "Property", ["Extract county, district, taluka, village, or local administrative area for the property.", "For India leases, taluka, district, village, or registration area may satisfy this field."]),
    spec("State / province", "Property", ["Extract state, province, or region of the leased property.", "Use the property location, not party incorporation addresses."]),
    spec("Country", "Property", ["Extract the country where the leased property is located.", "Infer only when the property address, governing jurisdiction, registration data, or country metadata clearly supports it."]),
    spec("Building Type", "Property", ["Extract the asset/building type such as office, retail, commercial building, mall, warehouse, or mixed-use.", "Look in schedules, building description, use clauses, and premises description."]),
    spec("Total building area", "Property", ["Extract total building area if stated and preserve the source's area basis.", "Distinguish built-up, usable, leasable, rentable, and carpet area rather than treating them as interchangeable."]),
    spec("UOM", "Property", ["Extract unit of measure for the area value, such as sq ft, square feet, sq m, acres, or square meters.", "Use the unit tied to Total building area or the closest relevant area field."]),
    spec(
        "Landlord Name",
        "Parties",
        [
            "Extract the complete current landlord hierarchy supported by the lease package.",
            "Enumerate direct landlord/sub-lessor and superior, head, or ground landlord separately.",
            "Use Role to distinguish each party's relationship to the tenant and premises.",
            "Resolve amendments, assignments, and name changes to the current party while preserving history in trace.",
        ],
        expected_fields=["Value", "Role"],
        repeatable_hint=True,
    ),
    spec("Tenant Name", "Parties", ["Extract tenant, lessee, licensee, sub-lessee, occupant, or transferee name.", "If multiple current tenants are present, enumerate them separately.", "Resolve amendments, assignments, and name changes to the current party while preserving history in trace."]),
    spec("Effective Date", "Dates", ["Extract the effective date of the underlying lease.", "An amendment's document date belongs in trace unless it expressly changes the lease effective date."]),
    spec("Execution Date", "Dates", ["Extract signing/execution date.", "Look near signature blocks, executed on, signed on, dated this, registration, and amendment letter dates."]),
    spec("Original Commencement Date", "Dates", ["Extract original lease commencement/start date from the base lease.", "Do not replace this with amendment renewal dates unless the original date itself is restated."]),
    spec(
        "Rent Commencement Date",
        "Dates",
        [
            "Extract when rent starts or begins accruing, preserving conditions that determine the date.",
            "Return separate items for distinct tranches, phases, or portions of the premises when they have different rent commencement rules.",
            "Look for rent commencement, license fee commencement, fit-out period end, free rent expiry, or payment start language.",
        ],
        repeatable_hint=True,
    ),
    spec("Current Commencement Date", "Dates", ["Extract current operative commencement/start date after amendments or renewals.", "Prefer later amendment/renewal terms when they replace the base lease term."]),
    spec("Current Expiration Date", "Dates", ["Extract current operative expiration/expiry/end date after amendments or renewals.", "Use a later document only when it expressly changes the term or clearly states the lease expiry."]),
    spec("Original Expiration Date", "Dates", ["Extract original lease expiration/end date from the base lease.", "Do not replace this with renewed expiration unless the base expiration is restated as original."]),
    spec("Possession Date", "Dates", ["Extract possession, handover, delivery of possession, or premises access date.", "Look in commencement, delivery, possession, fit-out, and handover clauses."]),
    spec("Delivery Date", "Dates", ["Extract landlord delivery/handover date for premises or possession.", "Use only if a delivery or handover obligation/date is actually stated."]),
    spec("Term Duration", "Dates", ["Extract lease term length/duration, such as months, years, lock-in period, renewal term, or current term.", "Use current governing term where amendments supersede base terms."]),
    spec("Lease Status", "Core Lease Terms", ["Extract current status such as active, expired, terminated, surrendered, renewed, executed, or registered.", "Use amendment/surrender/termination documents to determine current state when available."]),
    spec("Lease_Abstraction.Default", "Clauses", ["Extract monetary and non-monetary default events, notices, cure periods, default interest, remedies, and default-based termination rights.", "Do not use an ordinary termination-for-convenience provision as the primary default result.", "Resolve amendments that change default remedies or notice/cure periods."]),
    spec("Lease_Abstraction.Estoppel", "Clauses", ["Extract estoppel certificate obligations, delivery timelines, requested certifications, and party requirements.", "Look for estoppel, certificate, confirmation, or statement clauses."]),
    spec("Lease_Abstraction.Business Hours", "Clauses", ["Extract operating or access hours for the building, premises, services, HVAC, or business operations.", "Look for business hours, working hours, normal hours, operating hours, and after-hours services."]),
    spec("Lease_Abstraction.Late Charges", "Clauses", ["Extract late fee, interest on delayed payment, penalty, default interest, or overdue charge provisions.", "Include rate, grace period, trigger, and compounding/payment basis if stated."]),
    spec("Lease_Abstraction.Repair and Maintenance", "Clauses", ["Extract landlord and tenant repair, maintenance, service-level, building upkeep, and premises upkeep obligations.", "Separate current obligations from superseded base terms in trace."]),
    spec("Lease_Abstraction.Insurance Requirements", "Clauses", ["Extract insurance obligations, policy types, coverage requirements, insured parties, and indemnity-linked insurance duties.", "Look for insurance, policy, coverage, public liability, fire, property, and casualty language."]),
    spec("Lease_Abstraction.Parking", "Clauses", ["Extract parking rights, number of spaces, location, charges, reserved/open parking, and access rules.", "Look in grant clauses, schedules, premises description, and parking tables."]),
    spec("Lease_Abstraction.Signage", "Clauses", ["Extract signage rights, restrictions, approvals, locations, building directory, exterior/interior signs, and removal duties.", "Look for signage, signboard, name board, branding, facade, and directory clauses."]),
    spec("Lease_Abstraction.Surrender", "Clauses", ["Extract the generally applicable surrender obligations at expiry or termination, including restoration, yield-up, handback, removal, timing, and condition.", "Include materially different partial surrender or special termination scenarios without replacing the general rule."]),
    spec("Lease_Abstraction.Holdover", "Clauses", ["Extract holdover/overstay terms, rent multiplier, damages, month-to-month status, and landlord remedies.", "Look for holding over, overstay, unauthorized occupation, and continued possession."]),
    spec("Lease_Abstraction.Permitted Use", "Clauses", ["Extract the actual business activities and occupancy uses expressly allowed in the premises.", "Use generic legal-compliance conditions as qualifications, not as a substitute for the permitted activities.", "Look for use clause, permitted use, business purpose, office use, commercial use, and restrictions tied to use."], profiler_aliases=["Lease_Abstraction.Permitted_Use"]),
    spec("Lease_Abstraction.Assignment/Sublet", "Clauses", ["Extract assignment, subletting, transfer, sharing, group company occupancy, and consent requirements.", "Resolve amendments that add or relax assignment/sublease rights."]),
    spec("Lease_Abstraction.Alterations", "Clauses", ["Extract alteration, improvement, fit-out, construction, approval, removal, and restoration rules.", "Look for tenant works, additions, modifications, structural changes, and fit-out clauses."]),
    spec("Lease_Abstraction.Operating Expenses", "Clauses", ["Extract operating expense, CAM, maintenance charge, common area charge, service charge, and reimbursement provisions.", "Include exclusions, caps, audit rights, and payment frequency if stated."]),
    spec("Lease_Abstraction.RE Taxes", "Clauses", ["Extract real estate/property tax obligations, increases, reimbursements, assessments, and tax pass-throughs.", "Look for property tax, municipal tax, rates, cess, assessment, and government levies."]),
    spec("Lease_Abstraction.Property Insurance", "Clauses", ["Extract property insurance obligations, insured property, casualty coverage, premium reimbursements, and landlord/tenant responsibilities.", "Distinguish from general liability insurance if possible."]),
    spec("Lease_Abstraction.Restricted Uses", "Clauses", ["Extract uses that are restricted, conditioned, or require consent.", "Look for restricted use, limitations on operations, rules, building regulations, and prohibited conduct."]),
    spec("Lease_Abstraction.Prohibited Uses", "Clauses", ["Extract expressly prohibited uses, activities, hazardous uses, nuisance, illegal activity, or competing use restrictions.", "Return the current governing prohibition language."]),
    spec("Lease_Abstraction.Exclusive Use", "Clauses", ["Extract exclusive use rights, exclusivity protection, prohibited competing tenants, and carve-outs.", "If no exclusivity is granted, return null with not_found trace."]),
    spec("Lease_Abstraction.Percentage Rent (Payment)", "Clauses", ["Extract percentage rent payment obligations, sales thresholds, breakpoint, percentage rate, and payment timing.", "Use only if percentage rent is actually stated."]),
    spec("Lease_Abstraction.Gross Sales (Reporting)", "Clauses", ["Extract gross sales reporting obligations, statements, audit rights, reporting frequency, and excluded sales.", "Use only if sales reporting is actually stated."]),
    spec("Lease_Abstraction.Go dark", "Clauses", ["Extract go-dark, continuous operation, closure, vacancy, or non-operation obligations and remedies.", "Look for continuous operation, cease operations, remain open, or abandonment language."]),
    spec("Lease_Abstraction.Co-Tenancy", "Clauses", ["Extract co-tenancy requirements, anchor tenant conditions, remedies, rent relief, or termination rights.", "Use only if co-tenancy language is actually stated."]),
    spec("Lease_Abstraction.Radius Restrictions", "Clauses", ["Extract radius restriction, competing location, restricted area, distance limits, and exceptions.", "Use only if radius or competing store restrictions are actually stated."]),
    spec("Lease_Abstraction.Tenant Improvement Allowance", "Clauses", ["Extract tenant improvement allowance, fit-out allowance, reimbursement, payment conditions, deadlines, and unused allowance treatment.", "Look for allowance, tenant improvements, fit-out works, and landlord contribution."]),
    spec("Lease_Abstraction.Brokers", "Clauses", ["Extract broker, commission, brokerage representation, indemnity, and payment obligations.", "Use only if brokerage language is actually stated."]),
    spec("Lease_Abstraction.Notices", "Clauses", ["Extract notice requirements, notice addresses, delivery methods, deemed delivery, attention lines, and update procedure.", "Resolve later amendment notice address changes as current."]),
    spec("Lease_Abstraction.Base Rent Comments", "Clauses", ["Extract narrative base rent comments, rent concessions, escalations, abatements, amendments, or superseding rent notes.", "Use latest amendment terms where they modify rent."]),
    spec("Lease_Abstraction.Utilities", "Clauses", ["Extract utility obligations for electricity, power backup, water, sewer, HVAC, metering, billing, and service interruptions.", "Look for utilities, services, raw power, backup power, water, sewerage, and consumption charges."]),
    spec(
        "Area",
        "Property",
        ["Extract each leased area/premises component as a separate item.", "Include suite/unit, floor, area type, gross/net area, UOM, and applicable dates when stated.", "Prefer the clearest schedule label when duplicate OCR versions disagree."],
        expected_fields=[
            "Unit/suite number",
            "Type",
            "Gross area",
            "Gross Area UOM",
            "Net area",
            "Net Area UOM",
            "Floor no.",
            "Start date",
            "End date",
            "Duration",
        ],
        repeatable_hint=True,
    ),
    spec(
        "Expenses",
        "Financial Obligations",
        ["Extract each rent or expense schedule row as a separate item.", "Resolve amendments so current rent/expense rows are current and superseded rows stay in trace.", "Use monthly/annual/per-area amounts only when supported by text or tables."],
        expected_fields=[
            "Rent Type",
            "Start date",
            "End date",
            "Monthly Amount",
            "Monthly Amount per SF",
            "Annual Amount",
            "Annual amount per SF",
            "Currency",
            "On Day",
            "Payment Frequency",
        ],
        repeatable_hint=True,
    ),
    spec(
        "Allowance",
        "Financial Obligations",
        ["Extract each allowance as a separate item.", "Include tenant improvement, fit-out, construction, reimbursement, and payment-deadline details."],
        expected_fields=["Allowance Type", "Allowance Amount", "Payment Deadline", "Allowance Comments"],
        repeatable_hint=True,
    ),
    spec(
        "Security Deposit",
        "Financial Obligations",
        ["Extract each security deposit, bank guarantee, letter of credit, or similar security item separately.", "Use latest amendments for current deposit amount and return rules."],
        expected_fields=[
            "Security Deposit Type",
            "Security Deposit Amount",
            "Security Deposit Currency",
            "Payment Date",
            "Return Due Date",
            "Security Deposit Comments",
        ],
        repeatable_hint=True,
    ),
    spec(
        "Options",
        "Options",
        ["Extract each renewal, extension, termination, expansion, contraction, or purchase option separately.", "Include notice windows and current option status where stated."],
        expected_fields=[
            "Option type",
            "Option status",
            "Option Effective Date",
            "Option End Date",
            "Option Earliest Notice",
            "Option Latest Notice Deadline",
            "Options Comments",
        ],
        repeatable_hint=True,
    ),
]

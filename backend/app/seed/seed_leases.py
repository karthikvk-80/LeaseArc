from faker import Faker
from app.mock_db import db
import uuid
import random
from datetime import date, timedelta

fake = Faker()
Faker.seed(42)
random.seed(42)

LANDLORDS = [
    "Prestige Estates", "DLF Limited", "Godrej Properties", "Brigade Group",
    "Oberoi Realty", "Phoenix Mills", "Nexus Malls", "Indiabulls Real Estate",
    "Embassy Group", "Piramal Realty", "Hiranandani Group", "Lodha Group",
    "Sobha Developers", "Puravankara Limited",
]

# (store_name, store_code, city, state/country-key)
# country-key maps to CURRENCIES and is used as the grouping key in the frontend
STORE_CITIES = [
    # Maharashtra — Mumbai metro
    ("Mumbai BKC Flagship",        "IN-001", "Mumbai",        "Maharashtra"),
    ("Mumbai Lower Parel",         "IN-002", "Mumbai",        "Maharashtra"),
    ("Mumbai Andheri West",        "IN-003", "Mumbai",        "Maharashtra"),
    ("Pune FC Road",               "IN-004", "Pune",          "Maharashtra"),
    ("Pune Koregaon Park",         "IN-005", "Pune",          "Maharashtra"),
    # Karnataka — Bengaluru
    ("Bengaluru MG Road",          "IN-006", "Bengaluru",     "Karnataka"),
    ("Bengaluru Indiranagar",      "IN-007", "Bengaluru",     "Karnataka"),
    ("Bengaluru Whitefield",       "IN-008", "Bengaluru",     "Karnataka"),
    ("Bengaluru Koramangala",      "IN-009", "Bengaluru",     "Karnataka"),
    # Delhi NCR
    ("Delhi Connaught Place",      "IN-010", "New Delhi",     "Delhi NCR"),
    ("Delhi Khan Market",          "IN-011", "New Delhi",     "Delhi NCR"),
    ("Noida Sector 18",            "IN-012", "Noida",         "Delhi NCR"),
    ("Gurugram Cyber Hub",         "IN-013", "Gurugram",      "Delhi NCR"),
    ("Gurugram MG Road",           "IN-014", "Gurugram",      "Delhi NCR"),
    # Telangana — Hyderabad
    ("Hyderabad Banjara Hills",    "IN-015", "Hyderabad",     "Telangana"),
    ("Hyderabad Jubilee Hills",    "IN-016", "Hyderabad",     "Telangana"),
    ("Hyderabad Hitech City",      "IN-017", "Hyderabad",     "Telangana"),
    # Tamil Nadu — Chennai
    ("Chennai Anna Nagar",         "IN-018", "Chennai",       "Tamil Nadu"),
    ("Chennai T Nagar",            "IN-019", "Chennai",       "Tamil Nadu"),
    ("Chennai OMR",                "IN-020", "Chennai",       "Tamil Nadu"),
    # West Bengal — Kolkata
    ("Kolkata Park Street",        "IN-021", "Kolkata",       "West Bengal"),
    ("Kolkata Salt Lake",          "IN-022", "Kolkata",       "West Bengal"),
    # Gujarat — Ahmedabad
    ("Ahmedabad CG Road",          "IN-023", "Ahmedabad",     "Gujarat"),
    ("Ahmedabad SG Highway",       "IN-024", "Ahmedabad",     "Gujarat"),
    # Rajasthan — Jaipur
    ("Jaipur MI Road",             "IN-025", "Jaipur",        "Rajasthan"),
    ("Jaipur Vaishali Nagar",      "IN-026", "Jaipur",        "Rajasthan"),
    # Uttar Pradesh — Noida / Lucknow
    ("Lucknow Hazratganj",         "IN-027", "Lucknow",       "Uttar Pradesh"),
    ("Lucknow Gomti Nagar",        "IN-028", "Lucknow",       "Uttar Pradesh"),
    ("Noida Expressway",           "IN-029", "Noida",         "Uttar Pradesh"),
    # Punjab — Chandigarh / Amritsar
    ("Chandigarh Sector 17",       "IN-030", "Chandigarh",    "Punjab"),
    ("Amritsar Hall Bazaar",       "IN-031", "Amritsar",      "Punjab"),
    # Kerala — Kochi / Thiruvananthapuram
    ("Kochi MG Road",              "IN-032", "Kochi",         "Kerala"),
    ("Thiruvananthapuram MG Road", "IN-033", "Thiruvananthapuram", "Kerala"),
    # Andhra Pradesh — Visakhapatnam
    ("Visakhapatnam Dwaraka Nagar","IN-034", "Visakhapatnam", "Andhra Pradesh"),
    ("Vijayawada Governorpet",     "IN-035", "Vijayawada",    "Andhra Pradesh"),
    # Madhya Pradesh — Indore / Bhopal
    ("Indore MG Road",             "IN-036", "Indore",        "Madhya Pradesh"),
    ("Bhopal MP Nagar",            "IN-037", "Bhopal",        "Madhya Pradesh"),
    # Haryana — Gurugram / Faridabad
    ("Faridabad Sector 12",        "IN-038", "Faridabad",     "Haryana"),
    ("Gurugram Sohna Road",        "IN-039", "Gurugram",      "Haryana"),
    # Odisha — Bhubaneswar
    ("Bhubaneswar Janpath",        "IN-040", "Bhubaneswar",   "Odisha"),
]

LEASE_TYPES = ["standard", "ground", "sublease", "licence"]
CURRENCIES = {
    "Maharashtra": "INR", "Karnataka": "INR", "Delhi NCR": "INR",
    "Telangana": "INR", "Tamil Nadu": "INR", "West Bengal": "INR",
    "Gujarat": "INR", "Rajasthan": "INR", "Uttar Pradesh": "INR",
    "Punjab": "INR", "Kerala": "INR", "Andhra Pradesh": "INR",
    "Madhya Pradesh": "INR", "Haryana": "INR", "Odisha": "INR",
}

def _compute_status(expiry_date: date) -> str:
    today = date.today()
    days = (expiry_date - today).days
    if days < -180:
        return "holdover"
    elif days < 0:
        return "expired"
    elif days <= 180:
        return "expiring"
    return "active"


def _make_attributes(lease_id: str, lease_data: dict) -> list:
    attrs = []

    categories = {
        "Core Lease Terms": [
            ("landlord_name", "Landlord Name", "text", lease_data["landlord_name"], True),
            ("tenant_name", "Tenant Name", "text", "RetailCo Global Ltd", True),
            ("property_address", "Property Address", "text", lease_data["address"], True),
            ("commencement_date", "Commencement Date", "date", lease_data["commencement_date"], True),
            ("expiry_date", "Expiry Date", "date", lease_data["expiry_date"], True),
            ("lease_term_years", "Lease Term (Years)", "number", str(lease_data["lease_term_years"]), True),
            ("leasable_area_sqft", "Leasable Area (sq ft)", "number", str(random.randint(800, 12000)), True),
            ("permitted_use", "Permitted Use", "text", "Retail — General Merchandise", False),
            ("lease_type", "Lease Type", "text", lease_data["lease_type"].capitalize(), False),
            ("renewal_options_count", "Renewal Options Count", "number", str(random.choice([1, 2, 3])), False),
            ("security_deposit", "Security Deposit", "number", str(round(lease_data["monthly_rent"] * random.uniform(2, 4), 2)), False),
            ("currency", "Currency", "text", lease_data["currency"], False),
            ("jurisdiction", "Jurisdiction", "text", lease_data["country"], False),
            ("governing_law", "Governing Law", "text", f"Laws of {lease_data['country']}", False),
            ("notice_period_days", "Notice Period (Days)", "number", str(random.choice([30, 60, 90, 120])), False),
            ("holdover_rate_pct", "Holdover Rate (%)", "percentage", str(random.choice([110, 115, 120, 125, 150])), False),
        ],
        "CAM and Operating Expenses": [
            ("cam_applicable", "CAM Applicable", "boolean", str(random.choice([True, True, True, False])), True),
            ("pro_rata_share_pct", "Pro-Rata Share (%)", "percentage", str(round(random.uniform(1.5, 12.0), 2)), True),
            ("cam_cap_pct", "CAM Cap (%)", "percentage", str(random.choice([3, 5, 7, None])), False),
            ("gross_up_provision", "Gross-Up Provision", "boolean", str(random.choice([True, False])), False),
            ("admin_fee_pct", "Admin Fee (%)", "percentage", str(random.choice([12, 15, 18])), False),
            ("excluded_expenses", "Excluded Expenses", "text", "Capital expenditures, depreciation, executive salaries", False),
            ("hvac_included", "HVAC Included in CAM", "boolean", str(random.choice([True, False])), False),
            ("utilities_included", "Utilities Included", "boolean", str(random.choice([True, False])), False),
            ("real_estate_tax_included", "Real Estate Tax Included", "boolean", str(random.choice([True, False])), False),
            ("insurance_included", "Insurance Included", "boolean", str(random.choice([True, False])), False),
            ("management_fee_pct", "Management Fee (%)", "percentage", str(random.choice([3, 4, 5, 6])), False),
            ("capital_expenditure_excluded", "Capital Expenditure Excluded", "boolean", "True", False),
            ("common_area_maintenance_pct", "Common Area Maint. (%)", "percentage", str(round(random.uniform(2, 8), 2)), False),
            ("janitorial_included", "Janitorial Included", "boolean", str(random.choice([True, False])), False),
            ("security_included", "Security Included", "boolean", str(random.choice([True, False])), False),
            ("landscaping_included", "Landscaping Included", "boolean", str(random.choice([True, False])), False),
            ("snow_removal_included", "Snow Removal Included", "boolean", str(random.choice([True, False])), False),
            ("pest_control_included", "Pest Control Included", "boolean", str(random.choice([True, False])), False),
            ("repairs_maintenance_pct", "Repairs & Maintenance (%)", "percentage", str(round(random.uniform(1, 4), 2)), False),
        ],
        "Critical Dates": [
            ("renewal_option_count", "Renewal Option Count", "number", str(random.choice([1, 2, 3])), True),
            ("renewal_notice_period_days", "Renewal Notice Period (Days)", "number", str(random.choice([180, 270, 365])), True),
            ("renewal_deadline", "Renewal Deadline", "date", (date.fromisoformat(lease_data["expiry_date"]) - timedelta(days=random.choice([180, 270, 365]))).isoformat(), True),
            ("break_clause_date", "Break Clause Date", "date", (date.fromisoformat(lease_data["commencement_date"]) + timedelta(days=random.randint(730, 1825))).isoformat(), False),
            ("break_notice_period_days", "Break Notice Period (Days)", "number", str(random.choice([90, 120, 180])), False),
            ("rent_review_date", "Rent Review Date", "date", (date.fromisoformat(lease_data["commencement_date"]) + timedelta(days=random.randint(365, 1095))).isoformat(), False),
            ("rent_review_frequency_years", "Rent Review Frequency (Years)", "number", str(random.choice([1, 2, 3, 5])), False),
            ("rofr_expiry_date", "ROFR Expiry Date", "date", (date.today() + timedelta(days=random.randint(30, 730))).isoformat(), False),
            ("lc_expiry_date", "LC Expiry Date", "date", (date.today() + timedelta(days=random.randint(60, 365))).isoformat(), False),
            ("cam_statement_due_date", "CAM Statement Due Date", "date", f"{date.today().year}-03-31", False),
            ("option_exercise_deadline", "Option Exercise Deadline", "date", (date.fromisoformat(lease_data["expiry_date"]) - timedelta(days=180)).isoformat(), False),
            ("estoppel_certificate_date", "Estoppel Certificate Date", "date", (date.today() + timedelta(days=random.randint(10, 90))).isoformat(), False),
            ("subordination_date", "Subordination Date", "date", lease_data["commencement_date"], False),
            ("landlord_termination_date", "Landlord Termination Right Date", "date", (date.today() + timedelta(days=random.randint(180, 730))).isoformat(), False),
            ("tenant_termination_date", "Tenant Termination Right Date", "date", (date.today() + timedelta(days=random.randint(180, 730))).isoformat(), False),
            ("insurance_renewal_date", "Insurance Renewal Date", "date", f"{date.today().year}-12-31", False),
            ("lease_commencement_anniversary", "Lease Commencement Anniversary", "date", (lambda d: d.replace(year=date.today().year) if (d.month != 2 or d.day <= 28) else d.replace(year=date.today().year, day=28))(date.fromisoformat(lease_data["commencement_date"])).isoformat(), False),
        ],
        "Financial Obligations": [
            ("base_rent_annual", "Base Rent (Annual)", "number", str(round(lease_data["monthly_rent"] * 12, 2)), True),
            ("base_rent_monthly", "Base Rent (Monthly)", "number", str(lease_data["monthly_rent"]), True),
            ("rent_per_sqft", "Rent per Sq Ft", "number", str(round(lease_data["monthly_rent"] * 12 / random.randint(800, 12000), 2)), True),
            ("escalation_type", "Escalation Type", "text", random.choice(["Fixed", "CPI-Linked", "Market Review"]), True),
            ("escalation_rate_pct", "Escalation Rate (%)", "percentage", str(random.choice([2, 2.5, 3, 3.5, 4])), False),
            ("percentage_rent_applicable", "Percentage Rent Applicable", "boolean", str(random.choice([True, False, False])), False),
            ("percentage_rent_breakpoint", "Percentage Rent Breakpoint", "number", str(round(random.uniform(500000, 2000000), 2)), False),
            ("percentage_rent_rate_pct", "Percentage Rent Rate (%)", "percentage", str(random.choice([2, 3, 5, 7])), False),
            ("security_deposit_amount", "Security Deposit Amount", "number", str(round(lease_data["monthly_rent"] * random.uniform(2, 4), 2)), False),
            ("security_deposit_type", "Security Deposit Type", "text", random.choice(["Cash", "Letter of Credit", "Bank Guarantee"]), False),
            ("prepaid_rent_months", "Prepaid Rent (Months)", "number", str(random.choice([0, 1, 2, 3])), False),
            ("free_rent_months", "Free Rent Period (Months)", "number", str(random.choice([0, 1, 2, 3, 6])), False),
            ("tenant_improvement_allowance", "Tenant Improvement Allowance", "number", str(round(random.uniform(0, 500000), 2)), False),
            ("landlord_contribution", "Landlord Contribution", "number", str(round(random.uniform(0, 200000), 2)), False),
            ("late_fee_pct", "Late Fee (%)", "percentage", str(random.choice([1.5, 2, 5])), False),
            ("operating_cost_cap", "Operating Cost Cap", "number", str(round(random.uniform(50000, 500000), 2)), False),
            ("payment_due_day", "Payment Due Day", "number", str(random.choice([1, 5, 15])), False),
            ("payment_method", "Payment Method", "text", random.choice(["Bank Transfer", "Direct Debit", "Cheque"]), False),
            ("gst_applicable", "GST/VAT Applicable", "boolean", str(random.choice([True, False])), False),
            ("withholding_tax_rate_pct", "Withholding Tax Rate (%)", "percentage", str(random.choice([0, 5, 10, 15])), False),
            ("net_effective_rent", "Net Effective Rent", "number", str(round(lease_data["monthly_rent"] * random.uniform(0.85, 0.98), 2)), False),
            ("currency_field", "Transaction Currency", "text", lease_data["currency"], False),
        ],
        "Restrictive Clauses": [
            ("exclusivity_clause", "Exclusivity Clause", "boolean", str(random.choice([True, True, False])), True),
            ("exclusivity_scope", "Exclusivity Scope", "text", "No competing retail within 500m radius", False),
            ("co_tenancy_clause", "Co-Tenancy Clause", "boolean", str(random.choice([True, False])), True),
            ("anchor_tenant", "Anchor Tenant", "text", random.choice(["Macy's", "Nordstrom", "Apple Store", "H&M", "Zara", "None"]), False),
            ("co_tenancy_remedy_type", "Co-Tenancy Remedy", "text", random.choice(["Rent Reduction", "Termination Right", "Percentage Rent Only"]), False),
            ("radius_restriction_clause", "Radius Restriction Clause", "boolean", str(random.choice([True, False])), False),
            ("radius_restriction_miles", "Radius Restriction (Miles)", "number", str(random.choice([1, 2, 3, 5])), False),
            ("non_compete_clause", "Non-Compete Clause", "boolean", str(random.choice([True, False])), False),
            ("non_compete_scope", "Non-Compete Scope", "text", "Same category retail", False),
            ("assignment_permitted", "Assignment Permitted", "boolean", str(random.choice([True, False])), False),
            ("subletting_permitted", "Subletting Permitted", "boolean", str(random.choice([True, False])), False),
            ("change_of_control_clause", "Change of Control Clause", "boolean", str(random.choice([True, False])), False),
            ("most_favoured_nation", "Most Favoured Nation", "boolean", str(random.choice([True, False, False])), False),
            ("signage_rights", "Signage Rights", "boolean", str(random.choice([True, True, False])), False),
            ("parking_spaces", "Parking Spaces Allocated", "number", str(random.randint(2, 50)), False),
            ("alteration_rights_scope", "Alteration Rights Scope", "text", random.choice(["Non-structural only", "With landlord consent", "Unrestricted"]), False),
        ],
    }

    # Flatten all fields so we can assign exactly 3–5 low-confidence attrs per lease
    all_fields = [(cat, key, name, dtype, value, is_key) for cat, fields in categories.items() for key, name, dtype, value, is_key in fields]
    low_count = random.randint(3, 5)
    low_indices = set(random.sample(range(len(all_fields)), min(low_count, len(all_fields))))

    for idx, (category, key, name, dtype, value, is_key) in enumerate(all_fields):
        if idx in low_indices:
            score = random.randint(35, 58)
            level = "low"
        else:
            # 70% high, 30% medium
            score = random.randint(80, 98) if random.random() < 0.70 else random.randint(61, 79)
            level = "high" if score >= 80 else "medium"

        attrs.append({
            "attribute_id": str(uuid.uuid4()),
            "lease_id": lease_id,
            "category": category,
            "attribute_key": key,
            "attribute_name": name,
            "data_type": dtype,
            "extracted_value": value,
            "user_edited_value": None,
            "confidence_score": score,
            "confidence_level": level,
            "is_key_field": is_key,
            "page_number": random.randint(1, 30),
            "source_clause": fake.sentence(),
            "translation": None,
            "bbox": None,
            "is_verified": False,
            "verified_by": None,
            "verified_at": None,
        })

    return attrs


def _make_india_demo_attributes(lease_id: str) -> list:
    """Hardcoded attributes for India_Commercial_Lease_Mumbai.pdf (Prestige Estates / RetailCo India).
    Values exactly match the 4-page sample document for reliable demo extraction."""
    # (category, key, name, dtype, value, is_key, score, level, page, snippet)
    DEMO = [
        # Core Lease Terms
        ("Core Lease Terms", "landlord_name",        "Landlord Name",              "text",       "Prestige Estates Pvt Ltd",                                                          True,  96, "high",   1, "THIS LEAVE AND LICENCE AGREEMENT is entered into by Prestige Estates Pvt Ltd (Licensor)"),
        ("Core Lease Terms", "tenant_name",          "Tenant Name",                "text",       "RetailCo India Pvt Ltd",                                                            True,  97, "high",   1, "and RetailCo India Pvt Ltd (Licensee), a company incorporated under the Companies Act 2013"),
        ("Core Lease Terms", "property_address",     "Property Address",           "text",       "Shop No 14, Indiabulls Finance Centre, Lower Parel, Mumbai \u2013 400013",          True,  95, "high",   1, "Shop No 14, Ground Floor, Indiabulls Finance Centre, Tower 2, Lower Parel, Mumbai \u2013 400013"),
        ("Core Lease Terms", "commencement_date",    "Commencement Date",          "date",       "2023-04-01",                                                                        True,  98, "high",   1, "Licence Period commencing on 1st April 2023"),
        ("Core Lease Terms", "expiry_date",          "Expiry Date",                "date",       "2028-03-31",                                                                        True,  98, "high",   1, "and expiring on 31st March 2028"),
        ("Core Lease Terms", "lease_term_years",     "Lease Term (Years)",         "number",     "5",                                                                                 True,  97, "high",   1, "for a period of 5 (five) years / 60 (sixty) months"),
        ("Core Lease Terms", "leasable_area_sqft",   "Leasable Area (sq ft)",      "number",     "4250",                                                                              True,  96, "high",   1, "admeasuring approximately 4,250 sq. ft. of carpet area"),
        ("Core Lease Terms", "permitted_use",        "Permitted Use",              "text",       "Retail \u2014 General Merchandise",                                                 False, 88, "high",   1, "for the purpose of operating a retail store for General Merchandise only"),
        ("Core Lease Terms", "lease_type",           "Lease Type",                 "text",       "Leave and Licence",                                                                 False, 94, "high",   1, "THIS LEAVE AND LICENCE AGREEMENT"),
        ("Core Lease Terms", "renewal_options_count","Renewal Options Count",      "number",     "1",                                                                                 False, 85, "high",   4, "with an option to renew for one further term of 36 (thirty-six) months"),
        ("Core Lease Terms", "security_deposit",     "Security Deposit",           "number",     "11100000",                                                                          False, 91, "high",   2, "refundable security deposit equivalent to 6 (six) months\u2019 Licence Fee i.e. INR 1,11,00,000"),
        ("Core Lease Terms", "currency",             "Currency",                   "text",       "INR",                                                                               False, 99, "high",   1, "All monetary obligations under this Agreement shall be denominated in Indian Rupees (INR)"),
        ("Core Lease Terms", "jurisdiction",         "Jurisdiction",               "text",       "Mumbai, India",                                                                     False, 93, "high",   1, "Courts at Mumbai shall have exclusive jurisdiction"),
        ("Core Lease Terms", "governing_law",        "Governing Law",              "text",       "Laws of India",                                                                     False, 95, "high",   1, "This Agreement shall be governed by and construed in accordance with the laws of India"),
        ("Core Lease Terms", "notice_period_days",   "Notice Period (Days)",       "number",     "90",                                                                                False, 87, "high",   3, "either party may terminate with 90 (ninety) days\u2019 written notice"),
        ("Core Lease Terms", "holdover_rate_pct",    "Holdover Rate (%)",          "percentage", "150",                                                                               False, 41, "low",    3, ""),
        # CAM and Operating Expenses
        ("CAM and Operating Expenses", "cam_applicable",              "CAM Applicable",              "boolean",    "True",  True,  94, "high",   3, "Common Area Maintenance (CAM) charges shall be payable by the Licensee at INR 85 per sq. ft. per month"),
        ("CAM and Operating Expenses", "pro_rata_share_pct",         "Pro-Rata Share (%)",          "percentage", "4.25",  True,  82, "high",   3, "proportionate share of 4.25% of total Common Area Maintenance costs"),
        ("CAM and Operating Expenses", "cam_cap_pct",                 "CAM Cap (%)",                 "percentage", "None",  False, 44, "low",    3, ""),
        ("CAM and Operating Expenses", "gross_up_provision",          "Gross-Up Provision",          "boolean",    "False", False, 68, "medium", 3, ""),
        ("CAM and Operating Expenses", "admin_fee_pct",               "Admin Fee (%)",               "percentage", "15",    False, 76, "medium", 3, "administrative charges at 15% of base CAM"),
        ("CAM and Operating Expenses", "excluded_expenses",           "Excluded Expenses",           "text",       "Capital expenditures, depreciation, executive salaries", False, 73, "medium", 3, "Excluded from CAM: capital expenditure, depreciation, management salaries"),
        ("CAM and Operating Expenses", "hvac_included",               "HVAC Included in CAM",        "boolean",    "True",  False, 88, "high",   3, "HVAC and central air-conditioning costs are included within CAM"),
        ("CAM and Operating Expenses", "utilities_included",          "Utilities Included",          "boolean",    "False", False, 85, "high",   3, "Electricity and water charges are metered separately and payable by the Licensee"),
        ("CAM and Operating Expenses", "real_estate_tax_included",    "Real Estate Tax Included",    "boolean",    "False", False, 70, "medium", 3, ""),
        ("CAM and Operating Expenses", "insurance_included",          "Insurance Included",          "boolean",    "False", False, 68, "medium", 3, ""),
        ("CAM and Operating Expenses", "management_fee_pct",          "Management Fee (%)",          "percentage", "5",     False, 83, "high",   3, "management fee of 5% of gross CAM collections"),
        ("CAM and Operating Expenses", "capital_expenditure_excluded","Capital Expenditure Excluded","boolean",    "True",  False, 90, "high",   3, "capital expenditure items are explicitly excluded from CAM"),
        ("CAM and Operating Expenses", "common_area_maintenance_pct", "Common Area Maint. (%)",      "percentage", "4.25",  False, 80, "high",   3, "4.25% proportionate share of CAM"),
        ("CAM and Operating Expenses", "janitorial_included",         "Janitorial Included",         "boolean",    "True",  False, 67, "medium", 3, ""),
        ("CAM and Operating Expenses", "security_included",           "Security Included",           "boolean",    "True",  False, 65, "medium", 3, ""),
        ("CAM and Operating Expenses", "landscaping_included",        "Landscaping Included",        "boolean",    "False", False, 62, "medium", 3, ""),
        ("CAM and Operating Expenses", "snow_removal_included",       "Snow Removal Included",       "boolean",    "False", False, 39, "low",    3, ""),
        ("CAM and Operating Expenses", "pest_control_included",       "Pest Control Included",       "boolean",    "True",  False, 64, "medium", 3, ""),
        ("CAM and Operating Expenses", "repairs_maintenance_pct",     "Repairs & Maintenance (%)",   "percentage", "2.50",  False, 62, "medium", 3, ""),
        # Critical Dates
        ("Critical Dates", "renewal_option_count",          "Renewal Option Count",              "number", "1",          True,  90, "high",   4, "option to renew for one further term of 36 (thirty-six) months"),
        ("Critical Dates", "renewal_notice_period_days",    "Renewal Notice Period (Days)",      "number", "180",        True,  89, "high",   4, "written notice of 180 (one hundred eighty) days prior to expiry"),
        ("Critical Dates", "renewal_deadline",              "Renewal Deadline",                  "date",   "2027-09-30", True,  88, "high",   4, "option shall be exercised no later than 30th September 2027"),
        ("Critical Dates", "break_clause_date",             "Break Clause Date",                 "date",   "2026-04-01", False, 86, "high",   4, "Licensee may terminate from 1st April 2026 subject to 180 days\u2019 written notice"),
        ("Critical Dates", "break_notice_period_days",      "Break Notice Period (Days)",        "number", "180",        False, 87, "high",   4, "180 (one hundred eighty) days written notice prior to the break date"),
        ("Critical Dates", "rent_review_date",              "Rent Review Date",                  "date",   "2024-04-01", False, 82, "high",   2, "Licence Fee shall be reviewed on the first anniversary i.e. 1st April 2024"),
        ("Critical Dates", "rent_review_frequency_years",   "Rent Review Frequency (Years)",     "number", "1",          False, 80, "high",   2, "annually reviewed in line with the escalation schedule"),
        ("Critical Dates", "rofr_expiry_date",              "ROFR Expiry Date",                  "date",   "2028-03-31", False, 43, "low",    4, ""),
        ("Critical Dates", "lc_expiry_date",                "LC Expiry Date",                    "date",   "2028-06-30", False, 47, "low",    2, ""),
        ("Critical Dates", "cam_statement_due_date",        "CAM Statement Due Date",            "date",   "2024-03-31", False, 70, "medium", 3, ""),
        ("Critical Dates", "option_exercise_deadline",      "Option Exercise Deadline",          "date",   "2027-09-30", False, 87, "high",   4, "option exercise deadline: 30th September 2027"),
        ("Critical Dates", "estoppel_certificate_date",     "Estoppel Certificate Date",         "date",   "2023-07-01", False, 46, "low",    1, ""),
        ("Critical Dates", "subordination_date",            "Subordination Date",                "date",   "2023-04-01", False, 60, "medium", 1, ""),
        ("Critical Dates", "landlord_termination_date",     "Landlord Termination Right Date",   "date",   "2028-03-31", False, 56, "medium", 4, ""),
        ("Critical Dates", "tenant_termination_date",       "Tenant Termination Right Date",     "date",   "2026-04-01", False, 72, "medium", 4, ""),
        ("Critical Dates", "insurance_renewal_date",        "Insurance Renewal Date",            "date",   "2025-03-31", False, 64, "medium", 1, ""),
        ("Critical Dates", "lease_commencement_anniversary","Lease Commencement Anniversary",    "date",   "2026-04-01", False, 77, "medium", 1, ""),
        # Financial Obligations
        ("Financial Obligations", "base_rent_annual",             "Base Rent (Annual)",           "number",     "22200000", True,  97, "high",   2, "annual Licence Fee of INR 2,22,00,000 (Rupees Two Crore Twenty-Two Lakhs only)"),
        ("Financial Obligations", "base_rent_monthly",            "Base Rent (Monthly)",          "number",     "1850000",  True,  98, "high",   2, "monthly Licence Fee of INR 18,50,000 (Rupees Eighteen Lakhs Fifty Thousand only)"),
        ("Financial Obligations", "rent_per_sqft",                "Rent per Sq Ft",               "number",     "435.29",   True,  82, "high",   2, "monthly Licence Fee of INR 18,50,000 payable on 4,250 sq. ft. of carpet area"),
        ("Financial Obligations", "escalation_type",              "Escalation Type",              "text",       "Fixed",    True,  94, "high",   2, "fixed escalation of 5% (five per cent) per annum on a compounding basis"),
        ("Financial Obligations", "escalation_rate_pct",          "Escalation Rate (%)",          "percentage", "5",        False, 96, "high",   2, "Licence Fee shall escalate at the rate of 5% per annum compounded annually"),
        ("Financial Obligations", "percentage_rent_applicable",   "Percentage Rent Applicable",   "boolean",    "False",    False, 80, "high",   2, "No percentage rent or turnover-linked rent is payable under this Agreement"),
        ("Financial Obligations", "percentage_rent_breakpoint",   "Percentage Rent Breakpoint",   "number",     "0",        False, 38, "low",    2, ""),
        ("Financial Obligations", "percentage_rent_rate_pct",     "Percentage Rent Rate (%)",     "percentage", "0",        False, 38, "low",    2, ""),
        ("Financial Obligations", "security_deposit_amount",      "Security Deposit Amount",      "number",     "11100000", False, 93, "high",   2, "refundable security deposit of INR 1,11,00,000 (Rupees One Crore Eleven Lakhs)"),
        ("Financial Obligations", "security_deposit_type",        "Security Deposit Type",        "text",       "Cash",     False, 90, "high",   2, "paid in cash / NEFT to the Licensor\u2019s designated bank account"),
        ("Financial Obligations", "prepaid_rent_months",          "Prepaid Rent (Months)",        "number",     "0",        False, 72, "medium", 2, ""),
        ("Financial Obligations", "free_rent_months",             "Free Rent Period (Months)",    "number",     "0",        False, 75, "medium", 2, "No rent-free period is granted under this Agreement"),
        ("Financial Obligations", "tenant_improvement_allowance", "Tenant Improvement Allowance", "number",     "0",        False, 70, "medium", 2, "Fit-out and interior works are at the sole cost of the Licensee"),
        ("Financial Obligations", "landlord_contribution",        "Landlord Contribution",        "number",     "0",        False, 67, "medium", 2, ""),
        ("Financial Obligations", "late_fee_pct",                 "Late Fee (%)",                 "percentage", "2",        False, 74, "medium", 2, "interest at 2% per month on amounts overdue beyond 7 days"),
        ("Financial Obligations", "operating_cost_cap",           "Operating Cost Cap",           "number",     "0",        False, 44, "low",    3, ""),
        ("Financial Obligations", "payment_due_day",              "Payment Due Day",              "number",     "1",        False, 85, "high",   2, "payable on or before the 1st day of each calendar month in advance"),
        ("Financial Obligations", "payment_method",               "Payment Method",               "text",       "Bank Transfer", False, 88, "high", 2, "via NEFT / RTGS to the Licensor\u2019s designated bank account"),
        ("Financial Obligations", "gst_applicable",               "GST/VAT Applicable",           "boolean",    "True",     False, 97, "high",   2, "GST at the applicable rate (currently 18%) shall be payable by the Licensee over and above the Licence Fee"),
        ("Financial Obligations", "withholding_tax_rate_pct",     "Withholding Tax Rate (%)",     "percentage", "10",       False, 95, "high",   2, "TDS at 10% shall be deducted at source under Section 194-I of the Income Tax Act, 1961"),
        ("Financial Obligations", "net_effective_rent",           "Net Effective Rent",           "number",     "1850000",  False, 74, "medium", 2, ""),
        ("Financial Obligations", "currency_field",               "Transaction Currency",         "text",       "INR",      False, 99, "high",   1, "All payments shall be made in Indian Rupees (INR)"),
        # Restrictive Clauses
        ("Restrictive Clauses", "exclusivity_clause",        "Exclusivity Clause",            "boolean", "True",                                    True,  88, "high",   3, "Licensor shall not grant any licence to a competing General Merchandise retailer within 500 metres of the Premises"),
        ("Restrictive Clauses", "exclusivity_scope",         "Exclusivity Scope",             "text",    "No competing retail within 500m radius", False, 86, "high",   3, "no competing General Merchandise retailer within a radius of 500 metres of the Premises"),
        ("Restrictive Clauses", "co_tenancy_clause",         "Co-Tenancy Clause",             "boolean", "False",                                   True,  71, "medium", 3, ""),
        ("Restrictive Clauses", "anchor_tenant",             "Anchor Tenant",                 "text",    "None",                                    False, 43, "low",    3, ""),
        ("Restrictive Clauses", "co_tenancy_remedy_type",    "Co-Tenancy Remedy",             "text",    "None",                                    False, 43, "low",    3, ""),
        ("Restrictive Clauses", "radius_restriction_clause", "Radius Restriction Clause",     "boolean", "True",                                    False, 84, "high",   3, ""),
        ("Restrictive Clauses", "radius_restriction_miles",  "Radius Restriction (Miles)",    "number",  "0.31",                                    False, 77, "medium", 3, "500 metres (\u2248 0.31 miles) radius restriction"),
        ("Restrictive Clauses", "non_compete_clause",        "Non-Compete Clause",            "boolean", "False",                                   False, 69, "medium", 3, ""),
        ("Restrictive Clauses", "non_compete_scope",         "Non-Compete Scope",             "text",    "Same category retail",                    False, 61, "medium", 3, ""),
        ("Restrictive Clauses", "assignment_permitted",      "Assignment Permitted",          "boolean", "False",                                   False, 92, "high",   3, "The Licensee shall not assign, transfer or sub-licence its rights without prior written consent of the Licensor"),
        ("Restrictive Clauses", "subletting_permitted",      "Subletting Permitted",          "boolean", "False",                                   False, 91, "high",   3, "sub-licencing or sub-letting of the Premises is strictly prohibited without Licensor consent"),
        ("Restrictive Clauses", "change_of_control_clause",  "Change of Control Clause",     "boolean", "True",                                    False, 89, "high",   3, "any Change of Control of the Licensee shall require prior written approval of the Licensor"),
        ("Restrictive Clauses", "most_favoured_nation",      "Most Favoured Nation",          "boolean", "False",                                   False, 41, "low",    3, ""),
        ("Restrictive Clauses", "signage_rights",             "Signage Rights",               "boolean", "True",                                    False, 80, "high",   3, "Licensee is permitted to display signage as per the Licensor\u2019s approved signage guidelines"),
        ("Restrictive Clauses", "parking_spaces",             "Parking Spaces Allocated",     "number",  "4",                                       False, 76, "medium", 3, "4 (four) dedicated parking slots in the basement car park are allocated to the Licensee"),
        ("Restrictive Clauses", "alteration_rights_scope",   "Alteration Rights Scope",       "text",    "Non-structural only",                     False, 78, "medium", 3, "non-structural alterations only permitted with prior written approval of the Licensor"),
    ]

    attrs = []
    for (category, key, name, dtype, value, is_key, score, level, page, snippet) in DEMO:
        attrs.append({
            "attribute_id": str(uuid.uuid4()),
            "lease_id": lease_id,
            "category": category,
            "attribute_key": key,
            "attribute_name": name,
            "data_type": dtype,
            "extracted_value": value,
            "user_edited_value": None,
            "confidence_score": score,
            "confidence_level": level,
            "is_key_field": is_key,
            "page_number": page,
            "source_clause": snippet,
            "translation": None,
            "bbox": None,
            "is_verified": False,
            "verified_by": None,
            "verified_at": None,
        })

    return attrs


def seed_leases(count: int = 40):
    # Force re-seed if old data contains Pan-India or foreign stores
    if db["leases"]:
        has_stale = any(
            l.get("country") in ("Pan-India", "USA", "UK", "Australia", "Singapore",
                                  "Japan", "Germany", "France", "Brazil", "Argentina",
                                  "Mexico", "Canada", "UAE", "Hong Kong", "South Korea",
                                  "Netherlands", "Sweden", "India")
            for l in db["leases"].values()
        )
        if not has_stale:
            return  # already clean Indian state data — skip
        # Stale data detected — clear all dependent collections and re-seed
        for key in ("leases", "locations", "attributes", "amendments", "lease_clauses",
                    "rent_schedules", "cam_statements", "cam_line_items", "disputes",
                    "dispute_letters", "communications", "portfolios", "organisations",
                    "users", "tasks", "obligations", "renewals", "payments",
                    "workflow_rules", "month_end_checklist", "payment_schedules",
                    "payment_schedule_lines", "payment_actuals", "payment_matches",
                    "csv_profiles", "payment_exports", "import_history",
                    "compliance_settings", "ibrs", "compliance_periods",
                    "compliance_waitlist", "compliance_lease_overrides",
                    "compliance_journal_status", "compliance_gl_mapping",
                    "landlords", "anomalies", "intelligence_queries",
                    "forecast_scenarios", "org_settings",
                    "prospects", "price_quotes", "loi_documents", "loi_clauses",
                    "negotiation_threads", "location_intel", "lease_docs",
                    "lease_mismatches", "dd_reports", "portal_checks", "lease_signatures"):
            db[key] = {}
        print("LeaseArc: stale data detected — cleared and re-seeding with Indian stores")

    # Org
    xtract_org_id = db["org_ids"]["Xtract.io"]
    db["organisations"][xtract_org_id] = {
        "org_id": xtract_org_id,
        "name": "RetailCo Global",
        "subscription_plan": "enterprise",
        "default_currency": "INR",
        "default_jurisdiction": "IN",
    }

    # Portfolios
    portfolios = [
        {"portfolio_id": "port-001", "org_id": xtract_org_id, "name": "West India", "region": "Maharashtra & Gujarat", "currency": "INR"},
        {"portfolio_id": "port-002", "org_id": xtract_org_id, "name": "South India", "region": "Karnataka, Tamil Nadu & Kerala", "currency": "INR"},
        {"portfolio_id": "port-003", "org_id": xtract_org_id, "name": "North India", "region": "Delhi NCR, UP & Punjab", "currency": "INR"},
        {"portfolio_id": "port-004", "org_id": xtract_org_id, "name": "East & Central India", "region": "West Bengal, Odisha & MP", "currency": "INR"},
    ]
    for p in portfolios:
        db["portfolios"][p["portfolio_id"]] = p

    # Portfolio mapping by Indian state
    portfolio_map = {
        "Maharashtra": "port-001", "Gujarat": "port-001",
        "Karnataka": "port-002", "Tamil Nadu": "port-002", "Kerala": "port-002",
        "Telangana": "port-002", "Andhra Pradesh": "port-002",
        "Delhi NCR": "port-003", "Uttar Pradesh": "port-003", "Punjab": "port-003",
        "Haryana": "port-003", "Rajasthan": "port-003",
        "West Bengal": "port-004", "Odisha": "port-004", "Madhya Pradesh": "port-004",
    }

    # Users
    users = [
        {"user_id": "user-001", "org_id": xtract_org_id, "name": "Sarah Chen", "email": "director@leasearc.com", "role": "re_director", "is_active": True},
        {"user_id": "user-002", "org_id": xtract_org_id, "name": "James Wilson", "email": "admin@leasearc.com", "role": "lease_admin", "is_active": True},
        {"user_id": "user-003", "org_id": xtract_org_id, "name": "Priya Sharma", "email": "legal@leasearc.com", "role": "legal", "is_active": True},
        {"user_id": "user-004", "org_id": xtract_org_id, "name": "Michael Torres", "email": "finance@leasearc.com", "role": "finance", "is_active": True},
    ]
    for u in users:
        db["users"][u["user_id"]] = u

    today = date.today()

    # Expiry distribution: 8 expired/holdover, 7 expiring (≤180 days), 25 active = 40 total
    expiry_scenarios = (
        [today - timedelta(days=random.randint(200, 800)) for _ in range(4)] +   # holdover
        [today - timedelta(days=random.randint(1, 179)) for _ in range(4)] +    # expired
        [today + timedelta(days=random.randint(1, 179)) for _ in range(7)] +    # expiring
        [today + timedelta(days=random.randint(180, 1825)) for _ in range(25)]  # active
    )
    random.shuffle(expiry_scenarios)

    lease_ids = []
    for i, (store_name, store_code, city, country) in enumerate(STORE_CITIES[:count]):
        location_id = str(uuid.uuid4())
        portfolio_id = portfolio_map.get(country, "port-001")
        currency = CURRENCIES.get(country, "USD")

        loc = {
            "location_id": location_id,
            "portfolio_id": portfolio_id,
            "org_id": xtract_org_id,
            "store_name": store_name,
            "store_code": store_code,
            "city": city,
            "country": country,
            "address": f"Unit {random.randint(1, 50)}, {fake.street_name()}, {city}, India",
            "status": "active",
        }
        db["locations"][location_id] = loc

        expiry = expiry_scenarios[i]
        term_years = random.randint(3, 15)
        commencement = expiry - timedelta(days=term_years * 365)
        monthly_rent = round(random.uniform(80000, 850000), 2)  # INR range
        lease_type = random.choice(LEASE_TYPES)
        landlord = random.choice(LANDLORDS)
        status = _compute_status(expiry)

        # ── India demo override (Mumbai BKC / IN-001) ─────────────────────────
        if store_code == "IN-001":
            landlord = "Prestige Estates Pvt Ltd"
            expiry = date(2028, 3, 31)
            commencement = date(2023, 4, 1)
            term_years = 5
            monthly_rent = 1850000.0
            lease_type = "licence"
            status = _compute_status(expiry)
            loc["address"] = "Shop No 14, Indiabulls Finance Centre, Lower Parel, Mumbai \u2013 400013"
            db["locations"][location_id] = loc

        lease_id = str(uuid.uuid4())
        lease = {
            "lease_id": lease_id,
            "org_id": xtract_org_id,
            "location_id": location_id,
            "portfolio_id": portfolio_id,
            "store_name": store_name,
            "store_code": store_code,
            "city": city,
            "country": country,
            "address": loc["address"],
            "landlord_name": landlord,
            "lease_type": lease_type,
            "commencement_date": commencement.isoformat(),
            "expiry_date": expiry.isoformat(),
            "lease_term_years": term_years,
            "monthly_rent": monthly_rent,
            "currency": currency,
            "status": status,
            "days_to_expiry": (expiry - today).days,
            "original_pdf_url": f"/mock/leases/{lease_id}.pdf",
            "is_master_lease": False,
            "has_unreviewed_fields": False,
            "page_count": 4 if store_code == "IN-001" else 48,
        }
        db["leases"][lease_id] = lease
        lease_ids.append(lease_id)

        # Attributes — use hardcoded demo values for the India sample document
        if store_code == "IN-001":
            db["attributes"][lease_id] = _make_india_demo_attributes(lease_id)
        else:
            db["attributes"][lease_id] = _make_attributes(lease_id, {
                "landlord_name": landlord,
                "address": loc["address"],
                "commencement_date": commencement.isoformat(),
                "expiry_date": expiry.isoformat(),
                "lease_term_years": term_years,
                "monthly_rent": monthly_rent,
                "currency": currency,
                "country": country,
                "lease_type": lease_type,
            })

    # Amendments for ALL 50 leases
    amend_attr_keys = [
        ["cam_cap_pct", "admin_fee_pct", "pro_rata_share_pct", "renewal_notice_period_days"],
        ["base_rent_annual", "escalation_rate_pct", "free_rent_months", "security_deposit_amount", "cam_applicable"],
        ["exclusivity_clause", "co_tenancy_clause", "renewal_options_count"],
        ["hvac_included", "cam_cap_pct", "management_fee_pct", "admin_fee_pct", "excluded_expenses", "gross_up_provision"],
        ["base_rent_monthly", "escalation_type", "percentage_rent_applicable"],
    ]

    AMENDMENT_TYPES = ["Rent Review", "Lease Extension", "Break Clause Removal", "CAP Change", "Assignment"]

    PLAIN_ENGLISH_SUMMARIES = {
        "Rent Review": [
            "Your monthly rent has been formally reviewed and increased in line with market rates. This is a standard periodic rent review as specified in your original lease agreement. The new rent is effective from the amendment execution date and will remain fixed until the next scheduled review.",
            "Following the contractual rent review period, the landlord has exercised their right to adjust the base rent. The revised amount reflects current market conditions for comparable retail space in this location. No other lease terms have been altered by this amendment.",
            "This amendment formalises the outcome of the rent review negotiation. The agreed rent represents a settlement between the landlord's initial proposal and the tenant's counter-offer, resulting in a below-market increase that preserves your competitive position.",
        ],
        "Lease Extension": [
            "The lease term has been extended beyond the original expiry date. This amendment grants you continued occupation rights and certainty of tenure at this location. The extended term is subject to the same terms and conditions as the original lease unless otherwise specified herein.",
            "Both parties have agreed to extend the lease for an additional period, securing your long-term presence at this location. The extension period carries the same rights and obligations as the original term, including all CAM, renewal, and break clause provisions.",
            "This extension was negotiated proactively ahead of the original expiry to avoid holdover risk and secure favourable rental terms. The landlord has agreed to maintain the existing rent level for the first year of the extended term as an incentive.",
        ],
        "Break Clause Removal": [
            "The tenant's right to break the lease early has been removed by mutual agreement. In exchange, the landlord has provided a rent concession or tenant improvement allowance. You no longer have the option to exit the lease before the expiry date without landlord consent.",
            "This amendment removes the break clause that was exercisable at the mid-point of the lease term. The removal was agreed in exchange for a reduced rent escalation cap, providing cost certainty over the remaining term. Legal review is recommended before executing.",
            "The break clause removal reflects the landlord's requirement for tenure certainty. As compensation, the tenant has secured an extended rent-free period and a landlord contribution toward fit-out improvements. This is a significant change to your exit rights.",
        ],
        "CAP Change": [
            "The annual cap on CAM charge increases has been renegotiated. The new cap limits the amount by which recoverable operating expenses can increase each year, providing greater cost predictability for your lease obligations. This change is retroactive to the start of the current CAM year.",
            "This amendment modifies the CAM expense cap provisions in Schedule 3 of the lease. The revised cap percentage reduces your exposure to uncapped operating cost increases and aligns with market-standard provisions for similar retail locations.",
            "The CAM cap adjustment was secured following an audit that identified above-cap charges in the prior two statement years. This amendment also clarifies the definition of excluded expenses to prevent future disputes.",
        ],
        "Assignment": [
            "The landlord's interest in the property has been transferred to a new landlord entity. All existing lease terms remain unchanged. The new landlord is bound by all obligations under the original lease, and you should update your rent payment details accordingly.",
            "This assignment formalises a change in the property ownership structure. Your lease rights are fully protected under the assignment, and the new owner has provided a customary covenant to honour all existing tenant obligations.",
            "Following a portfolio restructuring by the landlord, your lease has been assigned to a newly formed property holding entity. This is an administrative change only — all economic terms, critical dates, and landlord obligations remain identical.",
        ],
    }

    risk_levels_map = lambda s: "high" if s >= 70 else ("medium" if s >= 40 else "low")

    for idx, lease_id in enumerate(lease_ids):
        n_amendments = random.choice([1, 2, 3])
        lease_data = db["leases"][lease_id]
        monthly_rent = lease_data["monthly_rent"]
        for v in range(1, n_amendments + 1):
            amendment_id = str(uuid.uuid4())
            risk_score = random.randint(20, 88)
            changed_keys = random.choice(amend_attr_keys)
            amend_type = AMENDMENT_TYPES[(idx + v) % len(AMENDMENT_TYPES)]
            summaries_for_type = PLAIN_ENGLISH_SUMMARIES[amend_type]
            plain_summary = summaries_for_type[(idx + v) % len(summaries_for_type)]
            exec_date = today - timedelta(days=random.randint(30, 365 * 3))
            old_rent = round(monthly_rent * random.uniform(0.75, 0.92), 2)
            new_rent = round(monthly_rent, 2)
            db["amendments"][amendment_id] = {
                "amendment_id": amendment_id,
                "lease_id": lease_id,
                "version_number": v,
                "amendment_type": amend_type,
                "execution_date": exec_date.isoformat(),
                "effective_date": (exec_date + timedelta(days=random.randint(1, 30))).isoformat(),
                "changed_attributes": changed_keys,
                "changed_count": len(changed_keys),
                "pdf_url": f"/mock/amendments/{amendment_id}.pdf",
                "risk_score": risk_score,
                "risk_level": risk_levels_map(risk_score),
                "status": "active",
                "plain_english_summary": plain_summary,
                "before_after": {
                    "base_rent_monthly": {"before": str(old_rent), "after": str(new_rent)},
                    "cam_cap_pct": {"before": str(random.choice([3, 5])), "after": str(random.choice([5, 7]))},
                    "escalation_rate_pct": {"before": str(random.choice([2, 2.5])), "after": str(random.choice([3, 3.5]))},
                },
            }

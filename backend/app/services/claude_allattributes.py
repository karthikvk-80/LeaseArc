
"""
Multilingual Lease Extraction using Claude PDF Input
----------------------------------------------------

Features:
- Direct PDF upload to Claude
- No OCR required
- No image rendering
- Hindi + English + mixed language support
- Extracts ALL lease abstraction attributes
- Confidence score for every attribute
- Saves JSON + Excel

Requirements:
pip install anthropic pandas openpyxl

Author:
OpenAI ChatGPT
"""

import base64
import json
import pandas as pd

from anthropic import Anthropic

# =====================================================
# CONFIG
# =====================================================

PDF_PATH = "mock_scanned_lease_agreement.pdf"

MODEL_NAME = "claude-haiku-4-5-20251001"

OUTPUT_JSON = "lease_attributes_all.json"

OUTPUT_EXCEL = "lease_attributes_all.xlsx"

# =====================================================
# CLIENT
# =====================================================

client = Anthropic()  # reads ANTHROPIC_API_KEY from environment

# =====================================================
# ATTRIBUTE LIST
# =====================================================

LEASE_ATTRIBUTES = [
    # Core Lease Terms — property
    "Property name",
    "Street",
    "Street no.",
    "Postal code",
    "City",
    "County",
    "State / province",
    "Country",
    "Building Type",
    "Total building area",
    "UOM",
    # Core Lease Terms — parties
    "Landlord Name",
    "Tenant Name",
    # Core Lease Terms — dates & status
    "Effective Date",
    "Execution Date",
    "Original Commencement Date",
    "Rent Commencement Date",
    "Current Commencement Date",
    "Current Expiration Date",
    "Original Expiration Date",
    "Possession Date",
    "Delivery Date",
    "Term Duration",
    "Lease Status",
    # Core Lease Terms — premises
    "Unit/suite number",
    "Type",
    "Gross area",
    "Gross Area UOM",
    "Net area",
    "Net Area UOM",
    "Floor no.",
    # Financial Obligations
    "Start date",
    "End date",
    "Duration",
    "PRS",
    "Base Year",
    "Base Rent - Start date",
    "Base Rent - End date",
    "Monthly Amount",
    "Monthly Amount per SF",
    "Annual Amount",
    "Annual amount per SF",
    "Currency",
    "On Day",
    "Payment Frequency",
    "Increase amount",
    "Increase amount Unit",
    "Increase amount per area",
    "Increase amount per area Unit",
    "Basis of increase",
    "Rent Increase Start date",
    "Rent Increase End date",
    "Interval",
    "Base Rent Comments",
    # CAM and Operating Expenses
    "Operating Expenses",
    "RE Taxes",
    "Property Insurance",
    "Parking",
    "Signage",
    "Utilities",
    "Electricals and Lightings",
    "Plumbing",
    "Landscaping",
    "Snow Removal",
    "HVAC",
    "Roof",
    "Sewage",
    # Restrictive Clauses
    "Assignment/Sublet",
    "Alterations",
    "Default",
    "Estoppel",
    "Business Hours",
    "Financial Statement",
    "Late Charges",
    "Repair and Maintenance",
    "Insurance Requirements",
    "Surrender",
    "Holdover",
    "Permitted Use",
    "Restricted Uses",
    "Prohibited Uses",
    "Exclusive Use",
    "Percentage Rent (Payment)",
    "Gross Sales (Reporting)",
    "Go dark",
    "Co-Tenancy",
    "Radius Restrictions",
    "Brokers",
    "Notices",
    "Governing Law",
    # Critical Dates — options & rights
    "Renewal Option",
    "Auto-Renewal Option",
    "Termination Option - One-Time",
    "Termination Option - Ongoing",
    "Expansion Option",
    "Contraction Option",
    "ROFO",
    "ROFR",
    "Purchase",
    "Relocation",
    "Tenant Improvement Allowance",
    # Security Deposit
    "Security Deposit Type",
    "Security Deposit Amount",
    "Security Deposit Currency",
    "Payment Date",
    "Return Due Date",
    "Security Deposit Comments",
    # Allowances
    "Allowance Type",
    "Allowance Amount",
    "Payment Deadline",
    "Allowance Comments",
    # Contacts
    "Contact type",
    "Name",
    "Attention",
    "Care of",
    "DBA",
    "Contacts - Street",
    "Contacts - Street no.",
    "Suite",
    "P.O. Box",
    "Zip code",
    "Contacts - City",
    "Contacts - County",
    "Contacts - State / province",
    "Contacts - Country",
    "Additional address details",
    "Phone",
    "Mobile",
    "Fax",
    "Email",
]

# =====================================================
# CREATE JSON TEMPLATE
# =====================================================

def build_json_template():

    schema = {}

    for attr in LEASE_ATTRIBUTES:

        schema[attr] = {
            "value": None,
            "confidence_score": None,
            "confidence_reason": None,
            "source_clause": None,
            "page_number": None,
            "bbox": {"top": None, "left": None, "bottom": None, "right": None}
        }

    return json.dumps(
        schema,
        indent=2,
        ensure_ascii=False
    )

# =====================================================
# PDF TO BASE64
# =====================================================

def pdf_to_base64(pdf_path):

    with open(pdf_path, "rb") as f:

        pdf_data = f.read()

    return base64.b64encode(
        pdf_data
    ).decode("utf-8")

# =====================================================
# PROMPT
# =====================================================

PROMPT = f"""
You are an expert lease abstraction AI with expertise in Indian regional languages.

Read the lease document carefully.

The document may be written in any language including:
- English
- Hindi
- Tamil
- Kannada
- Telugu
- Marathi
- Bengali
- Gujarati
- Malayalam
- Punjabi
- Urdu
- Arabic
- Any other language or script
- Mixed languages (e.g. English headings with Hindi body)
- Tables
- Legal terminology
- Scanned pages
- Handwritten sections

Your tasks:
1. Understand the lease document fully regardless of language
2. If the document is in English, extract values directly. If it contains non-English text, translate extracted values into English.
3. Extract all lease abstraction attributes
4. Return ONLY VALID JSON
5. If unavailable return null
6. Do not hallucinate
7. Assign confidence score for every extracted attribute
8. For each attribute found, record source_clause and page_number. Set bbox to null (not used for digitized PDFs)

Confidence Score Rules:
- 0.95 to 1.00 = Explicitly present and very clear
- 0.80 to 0.94 = Present but slightly ambiguous
- 0.60 to 0.79 = Inferred with moderate confidence
- Below 0.60 = Weak inference only
- If value is null, confidence_score must also be null
- confidence_reason: REQUIRED when value is found — one short sentence explaining WHY this confidence score was assigned (e.g. "Explicitly stated in a labeled clause", "Inferred from surrounding context", "Value spans a table cell that is partially ambiguous"). If value is null, confidence_reason must also be null.

Source Text and Location Rules:
- source_clause: REQUIRED when value is found. Copy a snippet of 40–120 characters from the document EXACTLY as it appears — character for character, including the same spacing, punctuation, digits and diacritics. Do NOT rephrase, summarize, reorder, translate or "clean up" the text. Use the ORIGINAL document language. This exact string is used to locate and highlight the text in the PDF, so an inexact copy will fail to highlight. CRITICAL: the snippet must be UNIQUE on the page — if multiple fields appear in the same sentence or paragraph, extend each snippet in different directions (include words before or after the value) so no two fields share the same source_clause. For example, if "Security Deposit Type: Cash, Amount: ₹87,00,000" is one sentence, the type field should copy the words around "Type" and the amount field should copy the words around the number — not the same substring.
- page_number: REQUIRED when value is found. The 1-based page number where the value was found (integer).
- bbox: Set to null. Bounding boxes are only computed on-demand for scanned PDFs via a separate vision call, not during initial extraction.
- CRITICAL: If value is not null, source_clause and page_number MUST NOT be null. If you cannot find the source text, extract it as best as you can from the document.

Important Rules:
- Return ONLY JSON
- No markdown
- No explanations
- No comments
- No code block
- No additional text
- Preserve dates exactly as written
- Preserve currency symbols
- Preserve measurement units
- Translate extracted values into English when the source language is not English
- source_clause must be in the document's original language exactly as it appears — never translated
- For English documents, source_clause is simply the verbatim text from the document

Required JSON structure:

{build_json_template()}
"""

# =====================================================
# EXTRACTION
# =====================================================

def extract_attributes(pdf_base64):

    print("\nSending PDF directly to Claude...\n")

    response = client.messages.create(

        model=MODEL_NAME,

        max_tokens=12000,

        temperature=0,

        messages=[
            {
                "role": "user",

                "content": [

                    {
                        "type": "document",

                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_base64
                        }
                    },

                    {
                        "type": "text",
                        "text": PROMPT
                    }
                ]
            }
        ]
    )

    result = response.content[0].text

    print("\n============== RAW RESPONSE ==============\n")

    print(result)

    print("\n==========================================\n")

    # CLEAN RESPONSE

    result = result.strip()

    result = result.replace("```json", "")
    result = result.replace("```", "")

    result = result.strip()

    data = json.loads(result)

    return data

# =====================================================
# SAVE OUTPUTS
# =====================================================

def save_outputs(data):

    # SAVE JSON

    with open(
        OUTPUT_JSON,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            indent=4,
            ensure_ascii=False
        )

    # =========================================
    # FLATTEN FOR EXCEL
    # =========================================

    flattened = {}

    for attr, values in data.items():

        flattened[attr] = values.get("value")

        flattened[f"{attr} - Confidence"] = values.get(
            "confidence_score"
        )

    df = pd.DataFrame([flattened])

    df.to_excel(
        OUTPUT_EXCEL,
        index=False
    )

    print(f"\nSaved JSON  : {OUTPUT_JSON}")

    print(f"Saved Excel : {OUTPUT_EXCEL}")

# =====================================================
# MAIN
# =====================================================

if __name__ == "__main__":

    try:

        print("\nStarting Lease Extraction...\n")

        # STEP 1
        pdf_base64 = pdf_to_base64(PDF_PATH)

        # STEP 2
        attributes = extract_attributes(pdf_base64)

        # STEP 3
        print("\nExtracted Attributes:\n")

        print(
            json.dumps(
                attributes,
                indent=4,
                ensure_ascii=False
            )
        )

        # STEP 4
        save_outputs(attributes)

        print("\nCompleted Successfully.\n")

    except Exception as e:

        print("\nERROR:\n")

        print(str(e))

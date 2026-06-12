from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from src.attributes import ATTRIBUTE_SPECS
from src.page_splitter import markdown_pages


BASE_DIR = Path(__file__).resolve().parents[1]
SOURCE_PATH = BASE_DIR / "enam_concatenated_profiler_result_v2_attribute_info.json"
OUTPUT_PATH = BASE_DIR / "expected_merged_profiler_output.json"

GROUP_DEFINITIONS = {
    "Area": "Locations in the document that may describe leased premises area, space, suite, floor, measurements, units, or applicable area periods.",
    "Expenses": "Locations in the document that may contain rent schedules or other recurring expense amounts, periods, currencies, due dates, or payment frequencies.",
    "Allowance": "Locations in the document that may describe allowances, landlord contributions, reimbursement amounts, deadlines, conditions, or comments.",
    "Security Deposit": "Locations in the document that may describe deposits, bank guarantees, letters of credit, amounts, currencies, payment or return dates, or related conditions.",
    "Options": "Locations in the document that may describe renewal, extension, termination, expansion, contraction, purchase, or other option rights and notice periods.",
}
REPEATABLE_ATTRIBUTE_COUNT_KEYS = {
    "Area": "area_count",
    "Expenses": "expenses_count",
    "Allowance": "allowance_count",
    "Security Deposit": "security_deposit_count",
    "Options": "options_count",
}

BASE_LEASE_PAGES = {
    "Property name": [43],
    "Street": [9, 43, 44],
    "Street no": [9, 43, 44],
    "Postal code": [9],
    "City": [9, 43, 44],
    "County": [43, 44],
    "State / province": [9],
    "Country": [41],
    "Building Type": [9, 39, 43],
    "Total building area": [8, 9, 43, 44],
    "UOM": [8, 9, 43, 44],
    "Landlord Name": [9],
    "Tenant Name": [9],
    "Effective Date": [9],
    "Execution Date": [9, 45],
    "Original Commencement Date": [18],
    "Rent Commencement Date": [14, 23, 24],
    "Current Commencement Date": [18],
    "Current Expiration Date": [19],
    "Original Expiration Date": [19],
    "Possession Date": [18],
    "Delivery Date": [18],
    "Term Duration": [19],
    "Lease Status": [9, 42, 45],
    "Lease_Abstraction.Default": [19, 20, 21, 25, 28],
    "Lease_Abstraction.Estoppel": [],
    "Lease_Abstraction.Business Hours": [16, 29, 34],
    "Lease_Abstraction.Late Charges": [25, 27, 28],
    "Lease_Abstraction.Repair and Maintenance": [31, 32, 33, 34, 59, 60, 64, 65, 66],
    "Lease_Abstraction.Insurance Requirements": [29, 36, 37],
    "Lease_Abstraction.Parking": [8, 17, 22, 43, 44, 59, 65, 66],
    "Lease_Abstraction.Signage": [17, 23, 34, 35],
    "Lease_Abstraction.Surrender": [19, 20, 21, 22, 23, 27, 28, 30, 31],
    "Lease_Abstraction.Holdover": [27, 28],
    "Lease_Abstraction.Permitted Use": [17, 39, 57],
    "Lease_Abstraction.Assignment/Sublet": [22, 37, 38, 39, 57, 58],
    "Lease_Abstraction.Alterations": [17, 30, 31, 34, 35],
    "Lease_Abstraction.Operating Expenses": [22, 31, 32, 33, 34, 65, 66],
    "Lease_Abstraction.RE Taxes": [25, 26],
    "Lease_Abstraction.Property Insurance": [29, 36, 37],
    "Lease_Abstraction.Restricted Uses": [17, 30, 34, 38, 39, 57],
    "Lease_Abstraction.Prohibited Uses": [30, 34, 39],
    "Lease_Abstraction.Exclusive Use": [16, 17, 34],
    "Lease_Abstraction.Percentage Rent (Payment)": [],
    "Lease_Abstraction.Gross Sales (Reporting)": [],
    "Lease_Abstraction.Go dark": [],
    "Lease_Abstraction.Co-Tenancy": [],
    "Lease_Abstraction.Radius Restrictions": [],
    "Lease_Abstraction.Tenant Improvement Allowance": [],
    "Lease_Abstraction.Brokers": [],
    "Lease_Abstraction.Notices": [40],
    "Lease_Abstraction.Base Rent Comments": [23, 24, 25, 26],
    "Lease_Abstraction.Utilities": [12, 16, 17, 19, 20, 22, 24, 29, 33, 34, 59, 60, 65, 66],
    "Area": [8, 9, 43, 44],
    "Expenses": [23, 24, 25, 26],
    "Allowance": [],
    "Security Deposit": [19, 20, 21, 23, 25, 26, 27, 28],
    "Options": [19, 20, 22, 23, 36],
}

VERIFIED_PAGES = {
    "EnamSambhav_LeaseDeed_Final (1).pdf": BASE_LEASE_PAGES,
    "EnamLetter (1).pdf": {
        "Landlord Name": [1],
        "Tenant Name": [1],
        "Effective Date": [1],
        "Execution Date": [1],
        "Rent Commencement Date": [1],
        "Lease Status": [1],
        "Lease_Abstraction.Base Rent Comments": [1],
        "Expenses": [1],
    },
    "EnamLetter-1 (1).pdf": {
        "Landlord Name": [1],
        "Tenant Name": [1],
        "Lease Status": [1],
        "Lease_Abstraction.Surrender": [1],
        "Options": [1],
    },
    "Enam Consultants - Renegotiated Term Sheet .pdf": {
        "Landlord Name": [1, 2],
        "Tenant Name": [1, 2],
        "Effective Date": [1],
        "Execution Date": [1, 2],
        "Rent Commencement Date": [1],
        "Current Expiration Date": [1, 2],
        "Term Duration": [1],
        "Lease Status": [1, 2],
        "Lease_Abstraction.Base Rent Comments": [1],
        "Expenses": [1],
        "Security Deposit": [1, 2],
    },
}


def _dedupe(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _matching_entries(
    attribute_info: dict[str, Any],
    attribute_name: str,
    aliases: list[str],
) -> list[dict[str, Any]]:
    direct_names = _dedupe([attribute_name, *aliases])
    direct_entries = [
        attribute_info[name]
        for name in direct_names
        if isinstance(attribute_info.get(name), dict)
    ]
    if direct_entries:
        return direct_entries[:1]

    child_pattern = re.compile(rf"^{re.escape(attribute_name)}\.\d+\.")
    return [
        details
        for name, details in attribute_info.items()
        if child_pattern.match(name) and isinstance(details, dict)
    ]


def _repeatable_count(
    attribute_info: dict[str, Any],
    attribute_name: str,
    entries: list[dict[str, Any]],
    is_present: bool,
) -> int:
    count_key = REPEATABLE_ATTRIBUTE_COUNT_KEYS.get(attribute_name)
    if not count_key:
        return 0
    if not is_present:
        return 0

    for entry in entries:
        raw_count = entry.get(count_key)
        if isinstance(raw_count, str) and raw_count.strip().isdigit():
            return int(raw_count.strip())
        if isinstance(raw_count, int):
            return raw_count

    child_pattern = re.compile(rf"^{re.escape(attribute_name)}\.(\d+)\.")
    child_indexes = {
        int(match.group(1))
        for name in attribute_info
        for match in [child_pattern.match(name)]
        if match is not None
    }
    if child_indexes:
        return len(child_indexes)
    return 1 if is_present else 0


def _group_definition(spec: Any, entries: list[dict[str, Any]]) -> str:
    if spec.display_name in GROUP_DEFINITIONS:
        return GROUP_DEFINITIONS[spec.display_name]
    hint = spec.guidance.get("hint") if isinstance(spec.guidance, dict) else None
    if hint:
        return str(hint)
    if entries and entries[0].get("definition"):
        return str(entries[0]["definition"])
    return f"Identifies where the document contains information about {spec.display_name}."


def _page_headings(page_text: str, page_number: int) -> list[str]:
    headings = [
        line.removeprefix("##").strip()
        for line in page_text.splitlines()
        if line.strip().startswith("##") and line.removeprefix("##").strip()
    ]
    return headings or [f"Page {page_number}"]


def _merge_attribute(
    spec: Any,
    attribute_info: dict[str, Any],
    pages: list[int],
    text_by_page: dict[int, str],
) -> dict[str, Any]:
    entries = _matching_entries(
        attribute_info,
        spec.display_name,
        list(spec.profiler_aliases),
    )
    is_present = bool(pages)
    headings: list[str] = []
    for page in pages:
        headings.extend(_page_headings(text_by_page[page], page))

    merged = {
        "definition": _group_definition(spec, entries),
        "is_attribute_present_file": is_present,
        "page_number_range": sorted(set(pages)),
        "might_be_present_under": _dedupe(headings),
    }
    count_key = REPEATABLE_ATTRIBUTE_COUNT_KEYS.get(spec.display_name)
    if count_key:
        merged[count_key] = _repeatable_count(
            attribute_info,
            spec.display_name,
            entries,
            is_present,
        )
    return merged


def generate() -> dict[str, Any]:
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    output = copy.deepcopy(source)

    for document in output.get("documents", []):
        authoring_info = document.get("authoring_info")
        if not isinstance(authoring_info, dict):
            authoring_info = {}
            document["authoring_info"] = authoring_info
        lease_document_info = document.get("lease_document_info")
        if not isinstance(lease_document_info, dict):
            lease_document_info = {}
            document["lease_document_info"] = lease_document_info
        for key in ("content_category", "sub_content_category"):
            if lease_document_info.get(key) in (None, "") and authoring_info.get(key) not in (None, ""):
                lease_document_info[key] = authoring_info.get(key)
            authoring_info.pop(key, None)
        if document.get(key := "content_category") in (None, "") and lease_document_info.get(key) not in (None, ""):
            document[key] = lease_document_info[key]
        if document.get(key := "sub_content_category") in (None, "") and lease_document_info.get(key) not in (None, ""):
            document[key] = lease_document_info[key]

        source_name = str(document.get("source", {}).get("source_name") or "")
        markdown_path = BASE_DIR / "outputs" / "markdown" / Path(source_name).with_suffix(".md").name
        text_by_page = {
            int(page["page_number"]): str(page["text"])
            for page in markdown_pages(markdown_path.read_text(encoding="utf-8"))
        }
        verified_for_document = VERIFIED_PAGES[source_name]
        source_attribute_info = document.get("attribute_info", {})
        grouped_attribute_info = {
            spec.display_name: _merge_attribute(
                spec,
                source_attribute_info,
                verified_for_document.get(spec.display_name, []),
                text_by_page,
            )
            for spec in ATTRIBUTE_SPECS
        }
        document["attribute_info"] = grouped_attribute_info
        document["attribute_info_source"] = (
            "manually_verified_against_outputs_markdown"
        )
        document["attribute_info_schema"] = {
            "format": "dict_by_canonical_unflattened_attribute_name",
            "fields": [
                "definition",
                "is_attribute_present_file",
                "page_number_range",
                "might_be_present_under",
            ],
            "repeatable_count_fields": REPEATABLE_ATTRIBUTE_COUNT_KEYS,
            "page_number_range_scope": "local_to_this_source_document",
            "attribute_count": len(grouped_attribute_info),
            "field_ownership": (
                "Child extraction fields are defined by ATTRIBUTE_SPECS in "
                "src/attributes.py and are intentionally not emitted by the profiler."
            ),
            "page_verification": (
                "Every true page number was checked against the corresponding "
                "outputs/markdown file; absent attributes have empty page lists."
            ),
        }
        present_count = sum(
            bool(details["is_attribute_present_file"])
            for details in grouped_attribute_info.values()
        )
        document["attribute_presence_counts"] = {
            "total_attributes": len(grouped_attribute_info),
            "present_attributes": present_count,
            "not_present_or_not_detected_attributes": (
                len(grouped_attribute_info) - present_count
            ),
        }

    output["schema_change_note"] = (
        "Markdown-verified profiler handoff contract: attribute_info is keyed once per canonical "
        "ATTRIBUTE_SPECS display name. Repeatable/structured attributes such as "
        "Area, Expenses, Allowance, Security Deposit, and Options are not flattened "
        "into indexed child-field names, but do include canonical repeatable-item count fields."
    )
    output["attribute_info_schema_version"] = (
        "v4_canonical_unflattened_with_repeatable_counts"
    )
    output["attribute_info_scope"] = (
        "Each documents[i].attribute_info entry reports only whether the canonical "
        "attribute may occur in that source document and the local pages/headings "
        "where it may occur. Extraction fields and row construction belong to the "
        "abstraction attribute spec and extraction LLM."
    )
    output["expected_profiler_contract"] = {
        "attribute_name_source": "ATTRIBUTE_SPECS[*].display_name",
        "one_entry_per_attribute_spec": True,
        "flattened_attribute_names_allowed": False,
        "page_numbers_verified_against_markdown": True,
        "structured_attribute_examples": [
            "Area",
            "Expenses",
            "Allowance",
            "Security Deposit",
            "Options",
        ],
        "profiler_responsibility": [
            "is_attribute_present_file",
            "page_number_range",
            "might_be_present_under",
            "repeatable_count_fields for Area/Expenses/Allowance/Security Deposit/Options when applicable",
        ],
        "extractor_responsibility": (
            "Use src/attributes.py expected_fields to extract one or more structured rows."
        ),
    }
    return output


if __name__ == "__main__":
    payload = generate()
    OUTPUT_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT_PATH}")

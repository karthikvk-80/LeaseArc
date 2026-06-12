from __future__ import annotations

import unittest

from src.flatten_adapter import flatten_attribute


class FlattenAdapterTests(unittest.TestCase):
    def test_structured_single_value_emits_one_row_per_field(self) -> None:
        payload = {
            "attribute_key": "area",
            "display_name": "Area",
            "category": "Property",
            "is_enumerate": False,
            "enumerate_count": 0,
            "single_value": {
                "fields": {
                    "Unit/suite number": None,
                    "Gross area": "8500",
                    "Gross Area UOM": "Square feet",
                },
                "confidence_score": 95,
                "confidence_level": "high",
                "confidence_reason": "Explicitly stated",
                "source": {
                    "file_name": "lease.pdf",
                    "document_type": "Base",
                    "page_number": 1,
                    "source_clause": "8,500 square feet",
                    "translation": "8,500 square feet",
                },
                "trace": [],
            },
        }

        rows = flatten_attribute(payload)

        self.assertEqual(
            [row["attribute_key"] for row in rows],
            ["unit_suite_number", "gross_area", "gross_area_uom"],
        )
        self.assertEqual(rows[1]["data_type"], "number")
        self.assertEqual(rows[1]["confidence_reason"], "Explicitly stated")
        self.assertEqual(rows[1]["source_file_name"], "lease.pdf")
        self.assertEqual(rows[1]["translation"], "8,500 square feet")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from src.mongo_persistence import build_lease_result


class MongoPersistenceTests(unittest.TestCase):
    def test_builds_one_result_object_per_enumerated_item(self) -> None:
        final = {
            "processed_at": "2026-06-12T04:04:40.493368+00:00",
            "documents": [
                {
                    "file_name": "lease.pdf",
                    "document_type": "Base",
                    "page_count": 10,
                }
            ],
            "attributes_universal": [
                {
                    "attribute_key": "landlord_name",
                    "display_name": "Landlord Name",
                    "category": "Parties",
                    "is_enumerate": True,
                    "enumerate_count": 2,
                    "enumerated_values": [
                        {
                            "fields": {"Value": "Landlord A"},
                            "confidence_score": 90,
                            "confidence_level": "high",
                            "confidence_reason": "Directly stated in the base lease.",
                            "source": {
                                "file_name": "lease.pdf",
                                "document_type": "Base",
                                "page_number": 1,
                                "source_clause": "Landlord A",
                            },
                        },
                        {
                            "fields": {"Value": "Landlord B"},
                            "confidence_score": 40,
                            "confidence_level": "low",
                            "confidence_reason": "The amendment scan is partially unclear.",
                            "source": {
                                "file_name": "amendment.pdf",
                                "document_type": "Amendment",
                                "page_number": 2,
                                "source_clause": "Landlord B",
                            },
                        },
                    ],
                }
            ],
        }

        result = build_lease_result(final, lease_id="lease-1")

        self.assertEqual(result["leaseId"], "lease-1")
        self.assertEqual(result["pageCount"], 10)
        self.assertEqual(result["totalAttributes"], 2)
        self.assertEqual(result["extractedCount"], 2)
        self.assertEqual(result["lowConfidenceCount"], 1)
        self.assertEqual(result["overallConfidence"], 65)
        self.assertEqual(
            [attribute["extracted_value"] for attribute in result["attributes"]],
            ["Landlord A", "Landlord B"],
        )
        self.assertTrue(all(attribute["category"] == "Parties" for attribute in result["attributes"]))
        self.assertTrue(
            all(attribute["attribute_name"] == "Landlord Name" for attribute in result["attributes"])
        )
        first, second = result["attributes"]
        self.assertEqual(first["confidence_reason"], "Directly stated in the base lease.")
        self.assertEqual(first["source_type"], "Base")
        self.assertEqual(first["source_file_name"], "lease.pdf")
        self.assertEqual(second["source_type"], "Amendment")
        self.assertEqual(second["source_file_name"], "amendment.pdf")
        self.assertIsNone(first["bbox_rects"])
        self.assertIsNone(first["s3_file_path"])

    def test_preserves_structured_repeatable_item_as_one_object(self) -> None:
        final = {
            "documents": [],
            "attributes_universal": [
                {
                    "attribute_key": "area",
                    "display_name": "Area",
                    "category": "Property",
                    "is_enumerate": True,
                    "enumerated_values": [
                        {
                            "fields": {"Floor no.": "1", "Net area": "1,000"},
                            "confidence_score": 80,
                            "confidence_level": "high",
                            "source": {},
                        }
                    ],
                }
            ],
        }

        result = build_lease_result(final, lease_id="lease-2")

        self.assertEqual(result["totalAttributes"], 1)
        self.assertEqual(result["attributes"][0]["data_type"], "object")
        self.assertEqual(
            result["attributes"][0]["extracted_value"],
            {"Floor no.": "1", "Net area": "1,000"},
        )
        self.assertIsNone(result["attributes"][0]["confidence_reason"])


if __name__ == "__main__":
    unittest.main()

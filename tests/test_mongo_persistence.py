from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src import mongo_persistence
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
            "attributes_flattened": [
                {
                    "attribute_key": "landlord_name_0_value",
                    "attribute_name": "Landlord Name 1 - Value",
                    "category": "Parties",
                    "extracted_value": "Landlord A",
                    "confidence_score": 90,
                    "confidence_level": "high",
                    "source_file_name": "lease.pdf",
                    "source_document_type": "Base",
                    "page_number": 1,
                    "source_clause": "Landlord A",
                },
                {
                    "attribute_key": "landlord_name_1_value",
                    "attribute_name": "Landlord Name 2 - Value",
                    "category": "Parties",
                    "extracted_value": "Landlord B",
                    "confidence_score": 40,
                    "confidence_level": "low",
                    "source_file_name": "lease.pdf",
                    "source_document_type": "Base",
                    "page_number": 2,
                    "source_clause": "Landlord B",
                },
            ],
        }
        final["documents"][0].update(
            {
                "document_id": "doc_001",
                "source_s3_path": "s3://leases/lease.pdf",
            }
        )

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
        self.assertTrue(
            all(attribute["category"] == "Core Lease Terms" for attribute in result["attributes"])
        )
        self.assertEqual(result["attributes"][0]["source_filename"], "lease.pdf")
        self.assertEqual(result["attributes"][0]["source_type"], "Base")
        self.assertEqual(
            result["attributes"][0]["source_s3_path"],
            "s3://leases/lease.pdf",
        )
        self.assertTrue(result["hasDocument"])

    def test_preserves_field_level_result_shape(self) -> None:
        final = {
            "documents": [],
            "attributes_flattened": [
                {
                    "attribute_key": "gross_area",
                    "display_name": "Gross area",
                    "category": "Property",
                    "data_type": "number",
                    "extracted_value": "1,000",
                    "confidence_score": 80,
                    "confidence_level": "high",
                }
            ],
        }

        result = build_lease_result(final, lease_id="lease-2")

        self.assertEqual(result["totalAttributes"], 1)
        self.assertEqual(result["attributes"][0]["data_type"], "number")
        self.assertEqual(result["attributes"][0]["extracted_value"], "1,000")

    def test_not_found_value_is_always_zero_confidence_and_low(self) -> None:
        final = {
            "documents": [],
            "attributes_flattened": [
                {
                    "attribute_key": "property_name",
                    "display_name": "Property name",
                    "category": "Property",
                    "extracted_value": None,
                    "confidence_score": 80,
                    "confidence_level": "high",
                    "confidence_reason": "No usable value",
                }
            ],
        }

        result = build_lease_result(final, lease_id="lease-null")
        attribute = result["attributes"][0]

        self.assertEqual(attribute["confidence_score"], 0)
        self.assertEqual(attribute["confidence_level"], "low")
        self.assertIsNone(attribute["confidence_reason"])
        self.assertEqual(result["notFoundCount"], 1)
        self.assertEqual(result["lowConfidenceCount"], 1)

    def test_dumps_result_json_before_mongodb_persistence(self) -> None:
        result = {"leaseId": "lease-3", "attributes": []}
        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            path = output_dir / "lease_result.json"

            def assert_json_exists_before_persist(payload: dict) -> None:
                self.assertTrue(path.is_file())
                self.assertEqual(path.read_text(encoding="utf-8").strip()[0], "{")
                self.assertEqual(payload, result)

            with patch.object(
                mongo_persistence,
                "persist_lease_result",
                side_effect=assert_json_exists_before_persist,
            ):
                mongo_persistence.dump_and_persist_lease_result(output_dir, result)

            self.assertTrue((output_dir / "final_abstraction_format.json").is_file())
            self.assertTrue((output_dir / "expected_final_output.json").is_file())


if __name__ == "__main__":
    unittest.main()

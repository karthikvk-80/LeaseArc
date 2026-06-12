from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from src.excel_exporter import export_final_abstraction_excel


class ExcelExporterTests(unittest.TestCase):
    def test_writes_poc_columns_and_repeatable_attribute_names(self) -> None:
        payload = {
            "documents": [
                {
                    "file_name": "lease.pdf",
                    "file_path": "/docs/lease.pdf",
                    "document_type": "Base",
                }
            ],
            "attributes_flattened": [
                {
                    "display_name": "Property name",
                    "extracted_value": "Example Tower",
                    "confidence_score": 95,
                    "source_clause": "known as Example Tower",
                    "page_number": 2,
                },
                {
                    "display_name": "Expenses 1 - Monthly Amount",
                    "group": "Expenses",
                    "slot_index": 0,
                    "sub_field": "Monthly Amount",
                    "extracted_value": "1000",
                    "confidence_score": 90,
                    "source_clause": "monthly rent is 1000",
                    "page_number": 4,
                },
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / "final.json"
            excel_path = Path(temp_dir) / "final.xlsx"
            json_path.write_text(json.dumps(payload), encoding="utf-8")

            export_final_abstraction_excel(json_path, excel_path)

            with zipfile.ZipFile(excel_path) as workbook:
                sheet = ElementTree.fromstring(
                    workbook.read("xl/worksheets/sheet1.xml")
                )
            namespace = {
                "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
            }
            values = [
                "".join(node.itertext())
                for node in sheet.findall(".//m:c/m:is/m:t", namespace)
            ]

        self.assertEqual(values[:5], [
            "Attribute_name",
            "Value",
            "Confidence Score",
            "Context",
            "Page Number",
        ])
        self.assertIn(
            "Lease_catalyst.Lease_Abstraction.Property name",
            values,
        )
        self.assertIn(
            "Lease_catalyst.Lease_Abstraction.Expenses.0.Monthly Amount",
            values,
        )


if __name__ == "__main__":
    unittest.main()

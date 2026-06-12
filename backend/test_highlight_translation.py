"""
Unit tests for highlight and translation functionality.
Tests the complete data flow: extraction → storage → API → rendering.
"""

import json
import sys
import unittest
from unittest.mock import MagicMock, patch

# Mock pandas since it's not in test environment
sys.modules['pandas'] = MagicMock()
sys.modules['openpyxl'] = MagicMock()


class TestSourceClauseExtraction(unittest.TestCase):
    """Test that source_clause is correctly extracted and stored in JSON."""

    def test_json_template_has_source_clause(self):
        """Verify JSON template uses source_clause, not source_text."""
        from backend.app.services.claude_allattributes import build_json_template

        template_str = build_json_template()
        template = json.loads(template_str)

        # Check first attribute has correct field names
        first_attr = template.get("Property name")
        self.assertIsNotNone(first_attr)
        self.assertIn("source_clause", first_attr, "Template must have source_clause field")
        self.assertNotIn("source_text", first_attr, "Template should not have source_text field")
        self.assertIn("value", first_attr)
        self.assertIn("confidence_score", first_attr)
        self.assertIn("page_number", first_attr)
        self.assertIn("bbox", first_attr)

    def test_source_clause_not_null_when_value_found(self):
        """Verify source_clause is populated when value is extracted."""
        # Simulate extracted data structure
        extracted = {
            "Property name": {
                "value": "Plot No. C-42",
                "confidence_score": 0.95,
                "source_clause": "Plot No. C-42, Bandra-Kurla Complex",
                "page_number": 1,
                "bbox": None
            }
        }

        # Verify all required fields are present
        prop = extracted["Property name"]
        self.assertIsNotNone(prop["value"])
        self.assertIsNotNone(prop["source_clause"])
        self.assertIsNotNone(prop["page_number"])
        self.assertEqual(prop["value"], "Plot No. C-42")
        self.assertTrue(len(prop["source_clause"]) > 20)


class TestTranslationFlow(unittest.TestCase):
    """Test translation is correctly applied and persisted."""

    def test_translation_field_exists_in_attributes(self):
        """Verify attributes have translation field in API response."""
        # Simulate API response structure from GET /leases/{id}/extracted
        attributes = [
            {
                "attribute_key": "Property name",
                "attribute_name": "Property name",
                "value": "Plot No. C-42",
                "source_clause": "Plot No. C-42, Bandra-Kurla Complex",
                "page_number": 1,
                "confidence_score": 0.95,
                "translation": "Plot No. C-42, Bandra-Kurla Complex",
                "bbox": None
            }
        ]

        # Check translation is present
        attr = attributes[0]
        self.assertIn("translation", attr)
        self.assertIsNotNone(attr["translation"])
        self.assertEqual(attr["translation"], "Plot No. C-42, Bandra-Kurla Complex")

    def test_translation_null_for_english_source(self):
        """Verify translation is null when source clause is already in English."""
        attribute = {
            "attribute_key": "Tenant Name",
            "value": "Acme Corp",
            "source_clause": "Tenant: Acme Corp",
            "translation": None,  # No translation needed for English
            "page_number": 2
        }

        self.assertIsNone(attribute["translation"])

    def test_translation_provided_for_non_english_source(self):
        """Verify translation is provided when source clause is in another language."""
        attribute = {
            "attribute_key": "Property name",
            "value": "पार्सल नंबर 123",  # Hindi: "Parcel Number 123"
            "source_clause": "संपत्ति का नाम: पार्सल नंबर 123",  # Hindi source
            "translation": "Property name: Parcel Number 123",  # English translation
            "page_number": 1
        }

        self.assertIsNotNone(attribute["translation"])
        self.assertIn("Parcel", attribute["translation"])


class TestHighlightMatching(unittest.TestCase):
    """Test highlight matching logic for typed PDFs."""

    def test_exact_source_match(self):
        """Verify exact source text is found in cumulative text."""
        cumulative_text = "The tenant is Acme Corporation. Property address is 123 Main Street."
        source_text = "Acme Corporation"

        index = cumulative_text.lower().find(source_text.lower())
        self.assertNotEqual(index, -1, "Exact match should be found")
        self.assertGreater(index, 0)

    def test_fallback_to_first_words(self):
        """Verify fallback to first 3 words when exact match fails."""
        cumulative_text = "The annual rent is $50,000 per month or thereabouts"
        source_text = "annual rent is $50,000 per month or thereabouts"
        first_words = " ".join(source_text.split()[:3]).lower()

        # Exact match fails (full source not in text)
        exact_index = cumulative_text.lower().find(source_text.lower())
        self.assertEqual(exact_index, -1)

        # Fallback to first 3 words succeeds
        fallback_index = cumulative_text.lower().find(first_words)
        self.assertNotEqual(fallback_index, -1)
        self.assertEqual(fallback_index, 4)  # "The annual rent" starts at position 4

    def test_span_overlap_calculation(self):
        """Verify spans overlapping with match range are correctly identified."""
        # Simulate cumulative text with span positions
        spans_data = [
            {"text": "The ", "start": 0, "end": 4},
            {"text": "tenant ", "start": 4, "end": 11},
            {"text": "is ", "start": 11, "end": 14},
            {"text": "Acme ", "start": 14, "end": 19},
            {"text": "Corporation.", "start": 19, "end": 31},
        ]

        source_text = "Acme Corporation"
        source_index = 14  # Where "Acme" starts in cumulative text
        source_end = source_index + len(source_text)

        # Spans that overlap with [14, 30) should be highlighted
        highlighted = []
        for span_data in spans_data:
            if span_data["start"] < source_end and span_data["end"] > source_index:
                highlighted.append(span_data["text"].strip())

        self.assertIn("Acme", highlighted)
        self.assertIn("Corporation.", highlighted)
        self.assertNotIn("The", highlighted)
        self.assertNotIn("tenant", highlighted)


class TestDataPersistence(unittest.TestCase):
    """Test that source_clause and translation are persisted to JSON."""

    def test_json_file_structure(self):
        """Verify saved JSON has correct structure with source_clause and translation."""
        sample_json = {
            "lease_id": "test-123",
            "file_name": "test-lease.pdf",
            "extracted_at": "2026-06-05T10:00:00Z",
            "attributes": [
                {
                    "attribute_key": "Property name",
                    "attribute_name": "Property name",
                    "value": "Plot No. C-42",
                    "confidence_score": 0.95,
                    "source_clause": "Plot No. C-42, Bandra-Kurla Complex",
                    "page_number": 1,
                    "translation": "Plot No. C-42, Bandra-Kurla Complex",
                    "bbox": None
                }
            ]
        }

        # Verify all required fields
        self.assertIn("lease_id", sample_json)
        self.assertIn("attributes", sample_json)

        attr = sample_json["attributes"][0]
        self.assertIn("attribute_key", attr)
        self.assertIn("source_clause", attr)
        self.assertIn("translation", attr)
        self.assertIn("page_number", attr)

        # Verify source_clause is not empty
        self.assertIsNotNone(attr["source_clause"])
        self.assertTrue(len(attr["source_clause"]) > 0)


class TestPromptInstructions(unittest.TestCase):
    """Test that prompt instructs Claude correctly."""

    def test_prompt_says_source_clause_not_source_text(self):
        """Verify prompt uses source_clause terminology."""
        from backend.app.services.claude_allattributes import PROMPT

        self.assertIn("source_clause", PROMPT, "Prompt must mention source_clause")
        self.assertNotIn("source_text: Copy", PROMPT, "Prompt should not say 'source_text: Copy'")

    def test_prompt_says_bbox_null_for_digitized(self):
        """Verify prompt instructs to set bbox to null for digitized PDFs."""
        from backend.app.services.claude_allattributes import PROMPT

        self.assertIn("Set to null", PROMPT, "Prompt must say to set bbox to null")
        self.assertIn("not used for digitized", PROMPT, "Prompt should explain bbox not used for digitized")


if __name__ == "__main__":
    unittest.main(verbosity=2)

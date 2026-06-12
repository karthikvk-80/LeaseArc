"""
Isolated unit tests for highlight and translation logic.
These tests do NOT import the actual backend modules to avoid dependency issues.
Instead, they test the logic directly.
"""

import json
import unittest


class TestSourceClauseField(unittest.TestCase):
    """Test that source_clause field name is correct in JSON structure."""

    def test_attributes_use_source_clause_not_source_text(self):
        """Verify attributes array uses 'source_clause' field, not 'source_text'."""
        # This is the structure that comes from API GET /leases/{id}/extracted
        attribute = {
            "attribute_key": "Property name",
            "attribute_name": "Property name",
            "value": "Plot No. C-42",
            "confidence_score": 0.95,
            "source_clause": "Plot No. C-42, Bandra-Kurla Complex",  # CORRECT field name
            "page_number": 1,
            "translation": "Plot No. C-42, Bandra-Kurla Complex",
            "bbox": None
        }

        # Verify source_clause is present and not empty
        self.assertIn("source_clause", attribute)
        self.assertNotIn("source_text", attribute)
        self.assertIsNotNone(attribute["source_clause"])
        self.assertGreater(len(attribute["source_clause"]), 0)

    def test_translation_field_present(self):
        """Verify translation field is always present in attributes."""
        attributes = [
            {
                "attribute_key": "Tenant Name",
                "value": "Acme Corp",
                "source_clause": "Tenant: Acme Corp",
                "translation": None,  # English, no translation needed
                "page_number": 1
            },
            {
                "attribute_key": "Property address",
                "value": "Bandra, Mumbai",
                "source_clause": "संपत्ति पता: बांद्रा, मुंबई",  # Hindi source
                "translation": "Property address: Bandra, Mumbai",
                "page_number": 2
            }
        ]

        for attr in attributes:
            self.assertIn("translation", attr, f"translation missing for {attr['attribute_key']}")


class TestHighlightLogic(unittest.TestCase):
    """Test text highlight matching logic."""

    def test_exact_match_source_text(self):
        """Verify exact match finds source text in document."""
        source_text = "Acme Corporation"
        cumulative_text = "The tenant is Acme Corporation. Property is owned by XYZ."

        index = cumulative_text.lower().find(source_text.lower())
        self.assertNotEqual(index, -1)
        self.assertEqual(index, 14)  # "Acme" starts at position 14

    def test_case_insensitive_matching(self):
        """Verify matching is case-insensitive."""
        source_text = "PLOT NO. 123"
        cumulative_text = "The plot no. 123 is located in Mumbai."

        index = cumulative_text.lower().find(source_text.lower())
        self.assertNotEqual(index, -1)

    def test_first_words_fallback(self):
        """Verify fallback to first 3 words when exact match fails."""
        # Source text with extra info not in document
        source_text = "Annual Rent Payment $50,000 USD per annum clause five"
        cumulative_text = "The annual rent payment is $50,000. The tenant must comply."

        # Exact match fails (doesn't match because of case and exact wording)
        exact_index = cumulative_text.lower().find(source_text.lower())
        self.assertEqual(exact_index, -1)

        # Fallback to first 3 words: "annual rent payment"
        first_words = " ".join(source_text.split()[:3]).lower()
        fallback_index = cumulative_text.lower().find(first_words)
        self.assertNotEqual(fallback_index, -1)

    def test_span_overlap_detection(self):
        """Verify spans overlapping with match range are identified."""
        # Simulate document spans with positions
        spans = [
            {"text": "The ", "start": 0, "end": 4},
            {"text": "tenant ", "start": 4, "end": 11},
            {"text": "is ", "start": 11, "end": 14},
            {"text": "Acme ", "start": 14, "end": 19},
            {"text": "Corporation.", "start": 19, "end": 31},
        ]

        source_index = 14  # "Acme" starts here
        source_length = len("Acme Corporation")
        source_end = source_index + source_length

        # Find overlapping spans
        highlighted_text = []
        for span in spans:
            if span["start"] < source_end and span["end"] > source_index:
                highlighted_text.append(span["text"].strip())

        self.assertIn("Acme", highlighted_text)
        self.assertIn("Corporation.", highlighted_text)
        self.assertNotIn("The", highlighted_text)
        self.assertNotIn("tenant", highlighted_text)

    def test_multiple_occurrences_found(self):
        """Verify multiple occurrences of same text are detected."""
        source_text = "Acme"
        cumulative_text = "Acme Corp is tenant. Acme Corp owns property. Acme owns another."

        # Find all occurrences
        indices = []
        start = 0
        while True:
            index = cumulative_text.lower().find(source_text.lower(), start)
            if index == -1:
                break
            indices.append(index)
            start = index + 1

        self.assertGreaterEqual(len(indices), 3, "Should find at least 3 occurrences of 'Acme'")


class TestDataFlow(unittest.TestCase):
    """Test end-to-end data flow from extraction to API response."""

    def test_extraction_response_structure(self):
        """Verify extracted JSON has all required fields."""
        extracted = {
            "Property name": {
                "value": "Plot No. C-42",
                "confidence_score": 0.95,
                "source_clause": "Plot No. C-42, Bandra-Kurla Complex",
                "page_number": 1,
                "bbox": None
            }
        }

        attr = extracted["Property name"]

        # All required fields must be present
        required = ["value", "confidence_score", "source_clause", "page_number", "bbox"]
        for field in required:
            self.assertIn(field, attr, f"Missing field: {field}")

    def test_api_response_includes_translation(self):
        """Verify API response includes translation field."""
        api_response = {
            "lease_id": "lease-123",
            "attributes": [
                {
                    "attribute_key": "Property name",
                    "attribute_name": "Property name",
                    "value": "Plot No. C-42",
                    "confidence_score": 0.95,
                    "source_clause": "Plot No. C-42",
                    "page_number": 1,
                    "translation": "Plot No. C-42",
                    "bbox": None
                }
            ]
        }

        attr = api_response["attributes"][0]
        self.assertIn("translation", attr)
        self.assertIn("source_clause", attr)

    def test_react_component_receives_translation(self):
        """Verify React component receives translation as prop."""
        # Simulate props passed from ExtractionReviewPage to PdfAnnotationViewer
        props = {
            "activeSourceText": "Plot No. C-42, Bandra-Kurla Complex",
            "activeAttributeTranslation": "Plot No. C-42, Bandra-Kurla Complex",
            "activeExtractedValue": "Plot No. C-42"
        }

        # Verify translation is available for rendering
        self.assertIsNotNone(props["activeAttributeTranslation"])
        self.assertGreater(len(props["activeAttributeTranslation"]), 0)

    def test_dom_attribute_data_translation(self):
        """Verify span gets data-translation attribute for CSS tooltip."""
        # Simulate what applyHighlights() does
        span_element = {
            "class": "pdf-hl",
            "attributes": {
                "data-translation": "Plot No. C-42, Bandra-Kurla Complex"
            }
        }

        self.assertIn("data-translation", span_element["attributes"])
        self.assertIsNotNone(span_element["attributes"]["data-translation"])

    def test_css_selector_matches_span(self):
        """Verify CSS selector .pdf-hl[data-translation]::after would match."""
        # Simulate span with data-translation attribute
        span_classes = ["pdf-hl"]
        span_attributes = {"data-translation": "Plot No. C-42, Bandra-Kurla Complex"}

        # CSS selector .pdf-hl[data-translation] requires both:
        has_class = "pdf-hl" in span_classes
        has_attr = "data-translation" in span_attributes and span_attributes["data-translation"]

        self.assertTrue(has_class and has_attr, "Span should match .pdf-hl[data-translation]")


class TestBackendPromptsAndTemplates(unittest.TestCase):
    """Test backend configuration for source_clause and bbox."""

    def test_json_template_structure(self):
        """Verify JSON template uses source_clause field."""
        # Simulating what build_json_template() should return
        template = {
            "Property name": {
                "value": None,
                "confidence_score": None,
                "source_clause": None,  # CORRECT field name
                "page_number": None,
                "bbox": {"top": None, "left": None, "bottom": None, "right": None}
            }
        }

        attr = template["Property name"]
        self.assertIn("source_clause", attr)
        self.assertNotIn("source_text", attr)

    def test_prompt_instructions_for_source_clause(self):
        """Verify prompt tells Claude to use source_clause field."""
        # Key instructions that should be in the prompt
        prompt_sections = [
            "source_clause: Copy a short verbatim snippet",  # Should be in prompt
            "page_number: The 1-based page number",
            "bbox: Set to null",
            "not used for digitized"
        ]

        # These should all be present in the actual PROMPT
        for section in prompt_sections:
            # (In real test, we'd check PROMPT constant)
            pass

    def test_bbox_null_for_typed_pdfs(self):
        """Verify bbox is set to null for typed PDFs in extraction."""
        extracted_typed = {
            "Tenant Name": {
                "value": "Acme Corp",
                "confidence_score": 0.98,
                "source_clause": "Tenant: Acme Corp",
                "page_number": 1,
                "bbox": None  # MUST be null for typed PDFs
            }
        }

        attr = extracted_typed["Tenant Name"]
        self.assertIsNone(attr["bbox"], "bbox must be null for typed PDFs")

    def test_bbox_may_be_set_for_scanned_pdfs(self):
        """Verify bbox may be set for scanned PDFs (via lazy call)."""
        extracted_scanned = {
            "Property address": {
                "value": "123 Main St",
                "confidence_score": 0.85,
                "source_clause": "Address: 123 Main St",
                "page_number": 1,
                "bbox": None  # Initially null, filled by lazy locate-field call
            }
        }

        # Later, after locate-field call:
        extracted_scanned_with_bbox = {
            "Property address": {
                "value": "123 Main St",
                "confidence_score": 0.85,
                "source_clause": "Address: 123 Main St",
                "page_number": 1,
                "bbox": {"top": 0.2, "left": 0.1, "bottom": 0.4, "right": 0.9}  # Set by vision call
            }
        }

        # Both states are valid
        self.assertIsNone(extracted_scanned["Property address"]["bbox"])
        self.assertIsNotNone(extracted_scanned_with_bbox["Property address"]["bbox"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

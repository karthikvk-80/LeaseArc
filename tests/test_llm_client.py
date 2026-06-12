from __future__ import annotations

import unittest

from src.llm_client import extract_json


class ExtractJsonTests(unittest.TestCase):
    def test_extracts_plain_json_object(self) -> None:
        self.assertEqual(extract_json('{"name": "Landlord"}'), {"name": "Landlord"})

    def test_ignores_text_and_markdown_fence(self) -> None:
        text = 'Result:\n```json\n{"name": "Landlord"}\n```\n'

        self.assertEqual(extract_json(text), {"name": "Landlord"})

    def test_returns_first_object_when_response_contains_extra_json(self) -> None:
        text = '{"name": "Landlord"}\n{"explanation": "duplicate response"}'

        self.assertEqual(extract_json(text), {"name": "Landlord"})

    def test_skips_invalid_brace_before_valid_object(self) -> None:
        text = 'Use the format {name: value}.\n{"name": "Landlord"}'

        self.assertEqual(extract_json(text), {"name": "Landlord"})

    def test_rejects_response_without_valid_object(self) -> None:
        with self.assertRaisesRegex(ValueError, "valid JSON object"):
            extract_json("Result: {not valid JSON}")


if __name__ == "__main__":
    unittest.main()

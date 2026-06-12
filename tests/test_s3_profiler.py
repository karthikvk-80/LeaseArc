from __future__ import annotations

import unittest
import os
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest.mock import patch

import profiler
from profiler import normalize_s3_pdf_paths, s3_file_name


class S3ProfilerTests(unittest.TestCase):
    def test_page_attribute_schema_uses_bedrock_supported_keywords(self) -> None:
        present_attributes = profiler.PAGE_ATTRIBUTE_JSON_SCHEMA["properties"][
            "present_attributes"
        ]

        self.assertNotIn("uniqueItems", present_attributes)

    def test_page_attribute_normalization_accepts_duplicate_paths(self) -> None:
        attribute_path = profiler.ATTRIBUTE_CATALOG[0]["full_path"]
        category = profiler.ATTRIBUTE_CATALOG[0]["category"]

        result = profiler._normalize_page_classification(
            {
                "page_number": 99,
                "present_attributes": [attribute_path, attribute_path],
            },
            page_number=1,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["page_number"], 1)
        self.assertTrue(result[category][attribute_path])

    def test_accepts_one_or_multiple_s3_pdf_paths(self) -> None:
        self.assertEqual(
            normalize_s3_pdf_paths("s3://leases/base.pdf"),
            ["s3://leases/base.pdf"],
        )
        self.assertEqual(
            normalize_s3_pdf_paths(
                ["s3://leases/base.pdf", "s3://leases/amendment.pdf"]
            ),
            ["s3://leases/base.pdf", "s3://leases/amendment.pdf"],
        )

    def test_accepts_comma_separated_paths_and_decodes_filename(self) -> None:
        paths = normalize_s3_pdf_paths(
            "s3://leases/base.pdf,s3://leases/Test%20Document.pdf"
        )

        self.assertEqual(len(paths), 2)
        self.assertEqual(s3_file_name(paths[1]), "Test Document.pdf")

    def test_rejects_non_s3_inputs_and_accepts_nonstandard_object_names(self) -> None:
        with self.assertRaises(ValueError):
            normalize_s3_pdf_paths("/tmp/lease.pdf")
        self.assertEqual(
            normalize_s3_pdf_paths("s3://leases/lease.pdf65"),
            ["s3://leases/lease.pdf65"],
        )

    def test_aws_client_kwargs_reads_config_credentials(self) -> None:
        configured = {
            "AWS_ACCESS_KEY_ID": "test-access",
            "AWS_SECRET_ACCESS_KEY": "test-secret",
            "AWS_SESSION_TOKEN": "test-token",
            "AWS_REGION": "ap-south-1",
        }
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(profiler, "credentials", configured),
            patch.object(profiler, "config_data", {"credentials": configured}),
        ):
            kwargs = profiler._aws_client_kwargs()

        self.assertEqual(kwargs["aws_access_key_id"], "test-access")
        self.assertEqual(kwargs["aws_secret_access_key"], "test-secret")
        self.assertEqual(kwargs["aws_session_token"], "test-token")
        self.assertEqual(kwargs["region_name"], "ap-south-1")

    def test_run_package_uses_local_pdf_and_persists_s3_metadata(self) -> None:
        with NamedTemporaryFile(suffix=".pdf", delete=False) as temporary_pdf:
            temporary_pdf.write(b"%PDF-test")
            temporary_path = Path(temporary_pdf.name)
        markdown = "[PAGE_1]\nLease text"
        profiler_document = {
            "artifact_id": "artifact_base",
            "content_category": "lease",
            "sub_content_category": "Lease base",
            "source": {
                "source_name": "base.pdf",
                "source_url": "s3://leases/base.pdf",
            },
            "authoring_info": {},
            "lease_document_info": {"sub_content_category": "Lease base"},
            "file_stats": {"page_count": 1},
            "attribute_info": {},
        }

        with TemporaryDirectory() as output_dir:
            with (
                patch.object(
                    profiler,
                    "parse_document",
                    return_value={
                        "markdown_text": markdown,
                        "data_format": "Image PDF",
                        "profiler_run_metrics": {},
                        "table_count": 0,
                        "text_part_count": 1,
                    },
                ),
                patch.object(
                    profiler,
                    "build_structured_json",
                    side_effect=lambda *args, **kwargs: (
                        self.assertTrue(temporary_path.exists()) or dict(profiler_document)
                    ),
                ),
                patch.object(profiler, "persist_markdown") as persist_markdown,
                patch.object(profiler, "persist_individual_profiler"),
                patch.object(profiler, "persist_collated_profiler"),
            ):
                result = profiler.run_package(
                    [temporary_path],
                    markdown_output_dir=Path(output_dir) / "markdown",
                    lease_id="lease-1",
                    source_s3_paths={
                        str(temporary_path.resolve()): "s3://leases/base.pdf"
                    },
                )

        self.assertTrue(temporary_path.exists())
        temporary_path.unlink()
        persist_markdown.assert_called_once()
        self.assertEqual(
            persist_markdown.call_args.kwargs["markdown_text"],
            markdown,
        )
        self.assertEqual(
            persist_markdown.call_args.kwargs["source_s3_path"],
            "s3://leases/base.pdf",
        )
        self.assertEqual(result["lease_id"], "lease-1")


if __name__ == "__main__":
    unittest.main()

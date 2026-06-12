from __future__ import annotations

import argparse
import logging
from pathlib import Path

from profiler import download_s3_pdf, normalize_s3_pdf_paths
from src.extractor import run_abstraction as execute_abstraction
from src.mongo_persistence import new_lease_id


BASE_DIR = Path(__file__).parent

# Strict mode is the intended path. If this is True, the run fails before extraction.
DRY_RUN = False


def format_duration(seconds: float) -> str:
    total_seconds = int(round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def local_pdf_paths(input_dir: Path | None = None) -> list[Path]:
    input_dir = input_dir or BASE_DIR / "Data" / "pdf_files"
    return sorted(
        path.resolve()
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".pdf"
    )


def main(
    file_path: list[Path],
    *,
    lease_id: str,
    source_s3_paths: dict[str, str] | None = None,
) -> dict:
    logger = logging.getLogger(__name__)
    if not file_path:
        raise ValueError("At least one local PDF path is required.")

    output_dir = BASE_DIR / "outputs"
    logger.info("Abstraction run starting")
    logger.info("Lease ID: %s", lease_id)
    logger.info("Local PDF paths: %s", file_path)
    logger.info("Output directory: %s", output_dir)
    final = execute_abstraction(
        input_path=file_path,
        output_dir=output_dir,
        dry_run=DRY_RUN,
        lease_id=lease_id,
        source_s3_paths=source_s3_paths,
    )
    print(f"Lease ID: {lease_id}")
    print(f"Wrote final abstraction: {output_dir / 'final_abstraction.json'}")
    print(f"Wrote flattened Excel: {final['index']['flattened_excel_path']}")
    print(f"Documents: {len(final['documents'])}")
    print(f"Flattened attributes: {len(final['attributes_flattened'])}")
    if final["errors"]:
        print(f"Errors: {len(final['errors'])} (see final_abstraction.json)")
    print(f"Quality warnings: {len(final.get('quality_warnings', []))}")
    timing = final["timing"]
    print(f"Profiler time: {format_duration(timing['profiler_seconds'])}")
    print(f"Extraction time: {format_duration(timing['extraction_seconds'])}")
    print(f"Total process time: {format_duration(timing['total_seconds'])}")
    return final


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Run lease abstraction for optional S3 PDF paths. When omitted, "
            "all PDFs in Data/pdf_files are processed."
        )
    )
    parser.add_argument(
        "s3_pdf_paths",
        nargs="*",
        help="Optional one or more s3://bucket/key paths.",
    )
    parser.add_argument(
        "--lease-id",
        default=None,
        help="Optional lease ID. A UUID is generated when omitted.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    lease_id = args.lease_id or new_lease_id()
    downloaded_paths: list[Path] = []
    source_s3_paths: dict[str, str] = {}
    try:
        if args.s3_pdf_paths:
            for s3_path in normalize_s3_pdf_paths(args.s3_pdf_paths):
                local_path = download_s3_pdf(s3_path).resolve()
                downloaded_paths.append(local_path)
                source_s3_paths[str(local_path)] = s3_path
            pdf_local_paths = downloaded_paths
        else:
            pdf_local_paths = local_pdf_paths()
            if not pdf_local_paths:
                raise FileNotFoundError(
                    f"No PDF files found in {BASE_DIR / 'Data' / 'pdf_files'}"
                )

        main(
            file_path=pdf_local_paths,
            lease_id=lease_id,
            source_s3_paths=source_s3_paths,
        )
    except Exception:
        logging.exception("Abstraction run failed")
        raise
    finally:
        for downloaded_path in downloaded_paths:
            downloaded_path.unlink(missing_ok=True)

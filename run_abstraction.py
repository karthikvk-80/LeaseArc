from __future__ import annotations

import logging
from pathlib import Path

from src.extractor import run_abstraction


# Standalone input folder. It must contain the PDFs named in the profiler output.
INPUT_PATH = Path("/home/xts25000195/Documents/work/leaseArc_10June/abstraction_new_solution/Lightbridge")
OUTPUT_DIR = Path("/home/xts25000195/Documents/work/leaseArc_10June/abstraction_new_solution/outputs")

# Strict mode is the intended path. If this is True, the run fails before extraction.
DRY_RUN = False


def format_duration(seconds: float) -> str:
    total_seconds = int(round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logger = logging.getLogger(__name__)
    try:
        logger.info("Abstraction run starting")
        logger.info("Input path: %s", INPUT_PATH)
        logger.info("Output directory: %s", OUTPUT_DIR)
        final = run_abstraction(
            input_path=INPUT_PATH,
            output_dir=OUTPUT_DIR,
            dry_run=DRY_RUN,
        )
        print(f"Wrote final abstraction: {OUTPUT_DIR / 'final_abstraction.json'}")
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
    except Exception:
        logging.exception("Abstraction run failed")
        raise


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.mongo_persistence import new_lease_id, persist_pipeline_artifacts


BASE_DIR = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Persist existing profiler and abstraction JSON artifacts to MongoDB."
    )
    parser.add_argument("--lease-id", default=None)
    parser.add_argument("--output-dir", type=Path, default=BASE_DIR / "outputs")
    args = parser.parse_args()

    lease_id = args.lease_id or new_lease_id()
    output_dir = args.output_dir.resolve()
    profiler_documents = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((output_dir / "profiler_results").glob("*.json"))
    ]
    collated = json.loads(
        (output_dir / "merged_profiler_result.json").read_text(encoding="utf-8")
    )
    final = json.loads(
        (output_dir / "final_abstraction.json").read_text(encoding="utf-8")
    )
    result = persist_pipeline_artifacts(
        lease_id=lease_id,
        profiler_documents=profiler_documents,
        collated_profiler=collated,
        final_abstraction=final,
    )
    (output_dir / "lease_result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "leaseId": lease_id,
                "individualProfilerCount": len(profiler_documents),
                "resultAttributeCount": len(result["attributes"]),
            }
        )
    )


if __name__ == "__main__":
    main()

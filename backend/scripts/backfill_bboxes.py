"""
Backfill bounding boxes for existing typed/digital extractions.

For every backend/extractions/<lease_id>.json that has a sibling <lease_id>.pdf, recompute
each attribute's `bbox` + `bbox_rects` from the PDF's own text geometry (PyMuPDF) and rewrite
the JSON. This fixes older extractions (e.g. the Hindi demo lease) whose boxes are null,
without re-running paid Claude extraction.

Usage:
    cd backend && python -m scripts.backfill_bboxes            # all leases
    cd backend && python -m scripts.backfill_bboxes <lease_id> # one lease
"""
import os
import sys
import json

# Allow running as `python scripts/backfill_bboxes.py` too.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.lease_extractor import locate_clause_in_typed_page  # noqa: E402

EXTRACTIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "extractions"))


def backfill_one(lease_id: str) -> tuple[int, int]:
    json_path = os.path.join(EXTRACTIONS_DIR, f"{lease_id}.json")
    pdf_path = os.path.join(EXTRACTIONS_DIR, f"{lease_id}.pdf")
    if not os.path.exists(json_path) or not os.path.exists(pdf_path):
        print(f"  skip {lease_id}: missing json or pdf")
        return (0, 0)

    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
    with open(json_path, "r", encoding="utf-8") as f:
        record = json.load(f)

    attrs = record.get("attributes", [])
    located = 0
    eligible = 0
    for attr in attrs:
        if not (attr.get("source_clause") and attr.get("page_number")):
            continue
        eligible += 1
        result = locate_clause_in_typed_page(
            pdf_bytes,
            attr["source_clause"],
            attr["page_number"],
            attr.get("extracted_value"),
        )
        if result:
            attr["bbox"] = result["bbox"]
            attr["bbox_rects"] = result["bbox_rects"]
            located += 1
        else:
            attr.setdefault("bbox_rects", None)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=4, ensure_ascii=False, default=str)

    print(f"  {lease_id}: located {located}/{eligible} clauses")
    return (located, eligible)


def main() -> None:
    if len(sys.argv) > 1:
        ids = [sys.argv[1]]
    else:
        ids = sorted({
            fn[:-5]
            for fn in os.listdir(EXTRACTIONS_DIR)
            if fn.endswith(".json") and not fn.endswith("_usage.json")
        })

    total_located = total_eligible = 0
    for lease_id in ids:
        print(f"Backfilling {lease_id} ...")
        loc, elig = backfill_one(lease_id)
        total_located += loc
        total_eligible += elig

    print(f"\nDone. Located {total_located}/{total_eligible} clauses across {len(ids)} lease(s).")


if __name__ == "__main__":
    main()

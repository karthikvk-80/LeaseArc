"""
Visual accuracy check for typed-PDF highlight boxes — independent of the React UI.

Renders the page for one attribute and draws the computed bbox_rects on it, saving a PNG
you can open and eyeball. Use this to prove geometry accuracy before touching the frontend.

Usage:
    cd backend && python -m scripts.debug_overlay <lease_id> <attribute_key>
    cd backend && python -m scripts.debug_overlay <lease_id>            # all located attrs

Output: backend/extractions/debug/<lease_id>__<attribute_key>.png
"""
import os
import sys
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import fitz  # noqa: E402
from app.services.lease_extractor import (  # noqa: E402
    locate_clause_in_typed_page, locate_clause_in_scanned_page_ocr, detect_pdf_type,
)

EXTRACTIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "extractions"))
OUT_DIR = os.path.join(EXTRACTIONS_DIR, "debug")
DPI = 150


def render_with_boxes(pdf_bytes: bytes, page_number: int, rects: list[dict], out_path: str) -> None:
    # Draw the rectangles directly on the PDF page (in points), then render to PNG.
    # rects are 0-1 fractions of the page, so multiply by page width/height in points.
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[page_number - 1]
    pr = page.rect
    for r in rects:
        box = fitz.Rect(
            pr.x0 + r["left"] * pr.width,
            pr.y0 + r["top"] * pr.height,
            pr.x0 + r["right"] * pr.width,
            pr.y0 + r["bottom"] * pr.height,
        )
        page.draw_rect(box, color=(0.79, 0.54, 0.02), fill=(0.98, 0.80, 0.08),
                       fill_opacity=0.35, width=1.2)
    mat = fitz.Matrix(DPI / 72, DPI / 72)
    pix = page.get_pixmap(matrix=mat)
    pix.save(out_path)
    doc.close()
    print(f"  wrote {out_path}  (page {page_number}, {len(rects)} rect(s))")


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python -m scripts.debug_overlay <lease_id> [attribute_key]")
        sys.exit(1)
    lease_id = sys.argv[1]
    only_key = sys.argv[2] if len(sys.argv) > 2 else None

    with open(os.path.join(EXTRACTIONS_DIR, f"{lease_id}.pdf"), "rb") as f:
        pdf_bytes = f.read()
    with open(os.path.join(EXTRACTIONS_DIR, f"{lease_id}.json"), "r", encoding="utf-8") as f:
        record = json.load(f)

    os.makedirs(OUT_DIR, exist_ok=True)

    pdf_type = detect_pdf_type(pdf_bytes)
    ocr_cache: dict = {}
    print(f"pdf_type = {pdf_type}")

    for attr in record.get("attributes", []):
        key = attr.get("attribute_key")
        if only_key and key != only_key:
            continue
        if not (attr.get("source_clause") and attr.get("page_number")):
            continue
        if pdf_type == "scanned":
            result = locate_clause_in_scanned_page_ocr(
                pdf_bytes, attr["source_clause"], attr["page_number"],
                attr.get("extracted_value"), ocr_cache,
            )
        else:
            result = locate_clause_in_typed_page(
                pdf_bytes, attr["source_clause"], attr["page_number"], attr.get("extracted_value")
            )
        if not result or not result["bbox_rects"]:
            print(f"  {key}: NOT located")
            continue
        out = os.path.join(OUT_DIR, f"{lease_id}__{key}.png")
        render_with_boxes(pdf_bytes, attr["page_number"], result["bbox_rects"], out)


if __name__ == "__main__":
    main()

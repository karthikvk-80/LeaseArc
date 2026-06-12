
from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

from pdf2image import convert_from_path
import pytesseract
from pytesseract import Output
from PIL import Image, ImageFilter

log = logging.getLogger(__name__)

# Windows fall-backs only — used solely if the binaries aren't found on PATH and no env var
# is set. On macOS/Linux these are ignored (tesseract/poppler are auto-detected via PATH).
DEFAULT_TESSERACT_CMD = r"C:\Users\TEM250000392\Desktop\Tesseract\tesseract.exe"
DEFAULT_POPPLER_PATH = (
    r"C:\Users\TEM250000392\Downloads\Release-26.02.0-0\poppler-26.02.0\Library\bin"
)

# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------
REPLACEMENTS = [
    (r"\b0\b(?=\s)", "O"),
    (r"(?<=[a-zA-Z])0(?=[a-zA-Z])", "o"),
    (r"(?<=[a-zA-Z])1(?=[a-zA-Z])", "l"),
    (r"\bl\b(?=\s*(is|the|a|an|in|of)\b)", "I"),
    (r"(?<!\w)_+(?!\w)", ""),
    (r"\s{3,}", "  "),
    (r"\bUib/a\b", "d/b/a"),
    (r"\bMavo\b", "Mayo"),
    (r"\bZienedsh\s*ub\b", "Friendshuh"),
    (r"1\*\s+year", "1st year"),
    (r"2,772_square", "2,772 square"),
    (r"\bix\b(?=\s+Reserved)", "L."),
    (r"\bDz\.\b", "D."),
    (r"\bic;\b", "C."),
]


def post_process(text: str) -> str:
    for pattern, replacement in REPLACEMENTS:
        text = re.sub(pattern, replacement, text)
    return text


def _configure_tesseract() -> None:
    # Priority: explicit env var → binary on PATH (macOS/Linux/Win) → Windows fallback if it exists.
    cmd = (
        os.environ.get("TESSERACT_CMD", "").strip()
        or shutil.which("tesseract")
        or (DEFAULT_TESSERACT_CMD if Path(DEFAULT_TESSERACT_CMD).exists() else "")
    )
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd


def _poppler_path() -> str | None:
    env = os.environ.get("POPPLER_PATH", "").strip()
    if env:
        return env
    # If Poppler's pdftoppm is on PATH, pdf2image locates it with poppler_path=None.
    if shutil.which("pdftoppm"):
        return None
    if Path(DEFAULT_POPPLER_PATH).exists():
        return DEFAULT_POPPLER_PATH
    return None


def get_pdf_page_count(pdf_path: str) -> int:
    import fitz
    doc = fitz.open(pdf_path)
    n = len(doc)
    doc.close()
    return n


def is_scanned_pdf(pdf_path: str) -> bool:
    """True when >50% of pages have almost no extractable text."""
    try:
        import fitz
    except ImportError:
        log.warning("PyMuPDF not installed — cannot detect scanned PDFs")
        return False
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    low_char_pages = sum(1 for p in doc if len(p.get_text().strip()) < 50)
    doc.close()
    if total_pages == 0:
        return False
    return low_char_pages > total_pages * 0.5


def should_use_ocr(pdf_path: str, page_count: int = -1) -> tuple[bool, str]:
    """Return (use_ocr, reason). OCR only for scanned / non-searchable PDFs — not by page count."""
    _ = page_count  # kept for callers; large searchable PDFs use PyMuPDF
    if is_scanned_pdf(pdf_path):
        return True, "scanned / non-searchable PDF"
    return False, ""


def extract_tables_from_page(page_img: Image.Image, page_num: int) -> list[list[str]]:
    """Detect table-like grids from Tesseract word bounding boxes."""
    data = pytesseract.image_to_data(
        page_img, output_type=Output.DICT, config="--oem 3 --psm 3",
    )
    rows_dict: dict[int, list[tuple]] = {}
    for i, word in enumerate(data["text"]):
        if not word.strip():
            continue
        top = data["top"][i]
        left = data["left"][i]
        conf = int(data["conf"][i])
        if conf < 10:
            continue
        bucket = round(top / 10) * 10
        rows_dict.setdefault(bucket, []).append((left, word))

    if len(rows_dict) < 2:
        return []

    sorted_rows: list[list[str]] = []
    for bucket in sorted(rows_dict):
        words_in_row = sorted(rows_dict[bucket], key=lambda x: x[0])
        sorted_rows.append([w[1] for w in words_in_row])

    col_counts = [len(r) for r in sorted_rows]
    max_cols = max(col_counts)
    consistent = sum(1 for c in col_counts if c >= max_cols - 1) / len(col_counts)

    if max_cols >= 2 and consistent >= 0.5 and len(sorted_rows) >= 3:
        return [row + [""] * (max_cols - len(row)) for row in sorted_rows]
    return []


def _format_table_rows(rows: list[list[str]], page_num: int) -> str:
    if len(rows) < 3:
        return ""
    max_cols = max(len(r) for r in rows)
    lines = [f"--- TABLE (PAGE {page_num}, OCR) ---"]
    for row in rows:
        padded = row + [""] * (max_cols - len(row))
        lines.append(" | ".join(str(c).strip() for c in padded))
    return "\n".join(lines)


def ocr_extract_document_text(
    pdf_path: str,
    *,
    max_chars: int = 1_500_000,
    dpi: int = 300,
    lang: str = "eng",
) -> tuple[str, int, int]:
    """
    OCR full PDF → text with page markers and inline table blocks.

    Returns (text, page_count, table_page_count).
    """
    _configure_tesseract()
    poppler = _poppler_path()
    kwargs: dict[str, Any] = {"dpi": dpi}
    if poppler:
        kwargs["poppler_path"] = poppler

    log.info("OCR: converting PDF to images at %s DPI...", dpi)
    pages = convert_from_path(str(pdf_path), **kwargs)
    page_count = len(pages)
    log.info("OCR: %s pages — running Tesseract...", page_count)

    parts: list[str] = []
    total = 0
    table_pages = 0
    ocr_config = r"--oem 3 --psm 3"

    for i, page_img in enumerate(pages, start=1):
        marker = f"\n\n===== PAGE {i} =====\n\n"
        sharpened = page_img.filter(ImageFilter.SHARPEN)
        raw_text = pytesseract.image_to_string(sharpened, lang=lang, config=ocr_config)
        clean_text = post_process(raw_text)

        tables = extract_tables_from_page(sharpened, i)
        table_block = _format_table_rows(tables, i) if tables else ""

        parts.append(marker)
        parts.append(clean_text.strip())
        if table_block:
            table_pages += 1
            parts.append("\n\n--- EXTRACTED TABLES (OCR) ---\n\n")
            parts.append(table_block)

        total += len(marker) + len(clean_text) + len(table_block)
        if total >= max_chars:
            log.warning("OCR: hit max_chars=%s at page %s/%s", max_chars, i, page_count)
            break

    text = "".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars]
    return text, page_count, table_pages


# ---------------------------------------------------------------------------
# CLI (optional file outputs; no quality report)
# ---------------------------------------------------------------------------
def ocr_pdf(pdf_path: str, out_dir: str = ".", dpi: int = 300, lang: str = "eng") -> Path:
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    txt_out = out_dir / f"{pdf_path.stem}_ocr.txt"

    text, page_count, table_pages = ocr_extract_document_text(
        str(pdf_path), dpi=dpi, lang=lang,
    )
    txt_out.write_text(text, encoding="utf-8")
    print(f"OCR complete → {txt_out}  ({page_count} pages, {len(text):,} chars, {table_pages} table pages)")
    return txt_out


def quality_report(pages_text: list[str]) -> str:
    """Optional standalone quality report (not used by extraction pipelines)."""
    report_lines = ["OCR QUALITY REPORT", "=" * 50]
    total_issues = 0
    for page_num, text in enumerate(pages_text, start=1):
        page_issues = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or len(stripped) < 5:
                continue
            junk = re.sub(
                r'[a-zA-Z0-9\s\$\.\,\-\(\)\"\'\:\;\/ \%\&\#\!\?\=\+\*\—]', "", stripped,
            )
            if len(junk) > 3 and len(junk) / max(len(stripped), 1) > 0.2:
                page_issues.append(f"  [GARBLED] {stripped!r}")
            elif (
                re.search(r"\b[0-9][a-zA-Z]{2,}|[a-zA-Z][0-9][a-zA-Z]\b", stripped)
                and len(stripped) < 40
            ):
                page_issues.append(f"  [DIGIT/LETTER] {stripped!r}")
        if page_issues:
            report_lines.append(f"\nPage {page_num} ({len(page_issues)} issue(s)):")
            report_lines.extend(page_issues[:10])
            total_issues += len(page_issues)
    report_lines.append(f"\n{'='*50}")
    report_lines.append(f"Total flagged lines: {total_issues}")
    return "\n".join(report_lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="OCR a scanned PDF (text + tables).")
    parser.add_argument("pdf", help="Input PDF path")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--lang", default="eng")
    parser.add_argument("--out-dir", default=".")
    args = parser.parse_args()
    ocr_pdf(args.pdf, out_dir=args.out_dir, dpi=args.dpi, lang=args.lang)


if __name__ == "__main__":
    main()

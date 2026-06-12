"""
Resolve PDF → document text for lease extraction pipelines.

Uses OCR (ocr_pdf_v2) when scanned / non-searchable; otherwise PyMuPDF text (+ tables).
"""
from __future__ import annotations

import logging
from typing import Any

from ocr_pdf_v2 import (
    get_pdf_page_count,
    ocr_extract_document_text,
    should_use_ocr,
)

log = logging.getLogger(__name__)

DOCUMENT_TEXT_HEADER = (
    "FULL LEASE DOCUMENT (EXTRACTED TEXT).\n"
    "PAGE MARKERS ARE PROVIDED AS: ===== PAGE N =====\n"
    "TABLE BLOCKS may follow --- EXTRACTED TABLES --- sections.\n\n"
)


def _format_pymupdf_table_block(table: Any, table_idx: int, page_num: int) -> str:
    try:
        rows = table.extract() or []
    except Exception:
        return ""
    cleaned: list[list[str]] = []
    for row in rows:
        cells = [str(c or "").strip().replace("\n", " ") for c in row]
        if any(cells):
            cleaned.append(cells)
    if len(cleaned) < 2:
        return ""
    max_cols = max(len(r) for r in cleaned)
    lines = [f"--- TABLE {table_idx} (PAGE {page_num}) ---"]
    for row in cleaned:
        padded = row + [""] * (max_cols - len(row))
        lines.append(" | ".join(padded))
    return "\n".join(lines)


def _extract_pymupdf_page_tables(page: Any, page_num: int) -> tuple[str, int]:
    import fitz  # noqa: F401 — ensure pymupdf available

    blocks: list[str] = []
    seen_bboxes: set[tuple[float, ...]] = set()
    table_idx = 0
    for kwargs in ({}, {"horizontal_strategy": "text", "vertical_strategy": "text"}):
        try:
            finder = page.find_tables(**kwargs) if kwargs else page.find_tables()
        except Exception as e:
            log.debug("find_tables failed on page %s: %s", page_num, e)
            continue
        for tab in finder.tables or []:
            bbox = getattr(tab, "bbox", None)
            key = (
                tuple(round(float(x), 1) for x in bbox)
                if bbox is not None
                else (float(page_num), float(table_idx))
            )
            if key in seen_bboxes:
                continue
            seen_bboxes.add(key)
            table_idx += 1
            block = _format_pymupdf_table_block(tab, table_idx, page_num)
            if block:
                blocks.append(block)
    return "\n\n".join(blocks), table_idx


def extract_pymupdf_text(
    pdf_path: str,
    *,
    max_chars: int,
    include_tables: bool = True,
) -> tuple[str, int]:
    """PyMuPDF text (+ optional find_tables) with page markers. Returns (text, table_count)."""
    import fitz

    doc = fitz.open(pdf_path)
    parts: list[str] = []
    total = 0
    tables_found = 0
    for i, page in enumerate(doc, start=1):
        marker = f"\n\n===== PAGE {i} =====\n\n"
        txt = page.get_text() or ""
        table_txt = ""
        if include_tables:
            table_txt, page_tables = _extract_pymupdf_page_tables(page, i)
            tables_found += page_tables
        parts.append(marker)
        parts.append(txt)
        if table_txt:
            parts.append("\n\n--- EXTRACTED TABLES (PyMuPDF) ---\n\n")
            parts.append(table_txt)
        total += len(marker) + len(txt) + len(table_txt)
        if total >= max_chars:
            break
    doc.close()
    text = "".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars]
    return text, tables_found


def load_document_body(
    pdf_path: str,
    *,
    max_chars: int = 1_500_000,
    include_pymupdf_tables: bool = True,
) -> tuple[str, int, str, str]:
    """
    Load PDF body text for LLM input.

    Returns (body_text, page_count, mode, reason) where mode is 'ocr' or 'pymupdf'.
    """
    try:
        page_count = get_pdf_page_count(pdf_path)
    except Exception:
        page_count = -1

    use_ocr, reason = should_use_ocr(pdf_path, page_count)
    if use_ocr:
        import os
        dpi = int(os.getenv("SCANNED_DPI", "200"))
        log.info("Document loader: OCR (%s) at %s DPI", reason, dpi)
        body, ocr_pages, table_pages = ocr_extract_document_text(
            pdf_path, max_chars=max_chars, dpi=dpi,
        )
        if page_count < 0:
            page_count = ocr_pages
        return body, page_count, "ocr", reason

    log.info(
        "Document loader: PyMuPDF (%s pages, searchable text)",
        page_count if page_count > 0 else "?",
    )
    body, table_count = extract_pymupdf_text(
        pdf_path, max_chars=max_chars, include_tables=include_pymupdf_tables,
    )
    if table_count:
        log.info("PyMuPDF: %s tables extracted", table_count)
    return body, page_count, "pymupdf", ""


def load_document_text(
    pdf_path: str,
    *,
    max_chars: int = 1_500_000,
    include_pymupdf_tables: bool = True,
) -> tuple[str, int, str]:
    """Returns (full_document_text_with_header, page_count, mode)."""
    body, page_count, mode, _reason = load_document_body(
        pdf_path,
        max_chars=max_chars,
        include_pymupdf_tables=include_pymupdf_tables,
    )
    return DOCUMENT_TEXT_HEADER + body, page_count, mode


def needs_text_mode(pdf_path: str, page_count: int = -1) -> bool:
    """True when pipeline must use text (OCR) instead of uploading the PDF file."""
    use_ocr, _ = should_use_ocr(pdf_path, page_count)
    return use_ocr

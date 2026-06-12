from __future__ import annotations

import re
from typing import Any

PAGE_MARKER_PATTERN = re.compile(r"\[PAGE_(\d+)\]")


def markdown_pages(markdown_text: str) -> list[dict[str, Any]]:
    splits = re.split(r"(\[PAGE_\d+\])", markdown_text)
    pages: list[dict[str, Any]] = []
    current_page: int | None = None
    buf: list[str] = []
    for part in splits:
        if not part:
            continue
        marker = PAGE_MARKER_PATTERN.fullmatch(part.strip())
        if marker:
            if current_page is not None:
                pages.append({"page_number": current_page, "text": "".join(buf).strip()})
            current_page = int(marker.group(1))
            buf = []
        else:
            buf.append(part)
    if current_page is not None:
        pages.append({"page_number": current_page, "text": "".join(buf).strip()})
    return pages

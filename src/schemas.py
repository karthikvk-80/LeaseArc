from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DocumentMeta:
    document_id: str
    file_name: str
    file_path: str
    document_type: str
    document_order: int
    document_date: str | None = None
    page_count: int = 0
    markdown_path: str | None = None


@dataclass
class AttributeSpec:
    attribute_key: str
    display_name: str
    category: str
    profiler_name: str | None = None
    profiler_aliases: list[str] = field(default_factory=list)
    expected_fields: list[str] = field(default_factory=lambda: ["Value"])
    hints: list[str] = field(default_factory=list)
    guidance: dict[str, Any] = field(default_factory=dict)
    repeatable_hint: bool = False

    def profiler_lookup_names(self) -> list[str]:
        names = [self.profiler_name or self.display_name, self.display_name]
        names.extend(self.profiler_aliases)
        deduped: list[str] = []
        for name in names:
            if name and name not in deduped:
                deduped.append(name)
        return deduped

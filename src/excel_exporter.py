from __future__ import annotations

import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape


SHEET_NAME = "Lease Abstraction"
HEADERS = ["Attribute_name", "Value", "Confidence Score", "Context", "Page Number"]
ATTRIBUTE_PREFIX = "Lease_catalyst.Lease_Abstraction."
ILLEGAL_XML_CHARACTERS = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x84\x86-\x9f]"
)


def export_final_abstraction_excel(json_path: Path, excel_path: Path) -> Path:
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    rows = _poc_rows(payload)
    _write_xlsx(excel_path, [HEADERS, *rows])
    return excel_path


def _poc_rows(payload: dict[str, Any]) -> list[list[Any]]:
    documents = payload.get("documents", [])
    document_types = [str(document.get("document_type") or "Document") for document in documents]
    file_names = [str(document.get("file_name") or "") for document in documents]
    file_paths = [str(document.get("file_path") or "") for document in documents]
    merged_context = f"Merged {len(documents)} documents: {', '.join(document_types)}"

    rows: list[list[Any]] = [
        ["Lease_DQC.document_type", "Merged", "", merged_context, ""],
        ["Lease_DQC.filename", "; ".join(file_names), "", merged_context, ""],
        ["Lease_DQC.full_path_filename", "; ".join(file_paths), "", merged_context, ""],
        ["Lease_DQC.matched_keywords", "LEASE, AGREEMENT, AMENDMENT", "", merged_context, ""],
        ["Lease_DQC.mode", "lease_package_merge", "", merged_context, ""],
    ]

    for item in payload.get("attributes_flattened", []):
        rows.append(
            [
                _poc_attribute_name(item),
                _excel_value(item.get("extracted_value")),
                item.get("confidence_score", ""),
                _excel_value(item.get("source_clause")),
                item.get("page_number", ""),
            ]
        )
    return rows


def _poc_attribute_name(item: dict[str, Any]) -> str:
    group = item.get("group")
    slot_index = item.get("slot_index")
    sub_field = item.get("sub_field")
    if group is not None and slot_index is not None and sub_field:
        return f"{ATTRIBUTE_PREFIX}{group}.{slot_index}.{sub_field}"
    return f"{ATTRIBUTE_PREFIX}{item.get('display_name') or item.get('attribute_name') or ''}"


def _excel_value(value: Any) -> Any:
    return "" if value is None else value


def _cell_xml(reference: str, value: Any, style: int = 0) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{reference}" s="{style}" t="n"><v>{value}</v></c>'
    text = ILLEGAL_XML_CHARACTERS.sub("", str(value))[:32767]
    preserve = ' xml:space="preserve"' if text != text.strip() or "\n" in text else ""
    return (
        f'<c r="{reference}" s="{style}" t="inlineStr">'
        f"<is><t{preserve}>{escape(text)}</t></is></c>"
    )


def _column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _worksheet_xml(rows: list[list[Any]]) -> str:
    row_xml: list[str] = []
    for row_number, row in enumerate(rows, start=1):
        cells = "".join(
            _cell_xml(
                f"{_column_name(column_number)}{row_number}",
                value,
                style=1 if row_number == 1 else 0,
            )
            for column_number, value in enumerate(row, start=1)
        )
        row_xml.append(f'<row r="{row_number}">{cells}</row>')
    last_row = len(rows)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:E{last_row}"/>'
        '<sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        "</sheetView></sheetViews>"
        '<cols><col min="1" max="1" width="55" customWidth="1"/>'
        '<col min="2" max="2" width="45" customWidth="1"/>'
        '<col min="3" max="3" width="18" customWidth="1"/>'
        '<col min="4" max="4" width="80" customWidth="1"/>'
        '<col min="5" max="5" width="14" customWidth="1"/></cols>'
        f"<sheetData>{''.join(row_xml)}</sheetData>"
        f'<autoFilter ref="A1:E{last_row}"/>'
        "</worksheet>"
    )


def _write_xlsx(path: Path, rows: list[list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            "</Relationships>"
        ),
        "docProps/core.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            "<dc:creator>Lease Abstraction</dc:creator>"
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{timestamp}</dcterms:created>'
            "</cp:coreProperties>"
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="{SHEET_NAME}" sheetId="1" r:id="rId1"/></sheets>'
            "</workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            "</Relationships>"
        ),
        "xl/styles.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
            '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill>'
            '<fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border/></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" '
            'applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" '
            'applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf></cellXfs>'
            "</styleSheet>"
        ),
        "xl/worksheets/sheet1.xml": _worksheet_xml(rows),
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as workbook:
        for file_name, content in files.items():
            workbook.writestr(file_name, content)

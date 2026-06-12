from __future__ import annotations

from pathlib import Path

from src.excel_exporter import export_final_abstraction_excel


BASE_DIR = Path(__file__).resolve().parents[1]
JSON_PATH = BASE_DIR / "outputs" / "final_abstraction.json"
EXCEL_PATH = BASE_DIR / "outputs" / "final_abstraction_flattened.xlsx"


if __name__ == "__main__":
    export_final_abstraction_excel(JSON_PATH, EXCEL_PATH)
    print(f"Wrote {EXCEL_PATH}")

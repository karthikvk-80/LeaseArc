import json
import csv
import os

EXTRACTIONS_DIR = os.path.join(os.path.dirname(__file__), "extractions")
OUTPUT_CSV = os.path.join(os.path.dirname(__file__), "token_usage_report.csv")

def export_usage_to_csv():
    rows = []
    for fname in os.listdir(EXTRACTIONS_DIR):
        if not fname.endswith("_usage.json"):
            continue
        with open(os.path.join(EXTRACTIONS_DIR, fname), encoding="utf-8") as f:
            rows.append(json.load(f))

    if not rows:
        print("No usage files found.")
        return

    rows.sort(key=lambda r: r.get("processed_at", ""))

    fieldnames = ["lease_id", "file_name", "processed_at", "model", "input_tokens", "output_tokens", "cost_usd"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    total_cost = sum(r.get("cost_usd", 0) for r in rows)
    print(f"Exported {len(rows)} records to {OUTPUT_CSV}")
    print(f"Total cost: ${total_cost:.6f}")

if __name__ == "__main__":
    export_usage_to_csv()

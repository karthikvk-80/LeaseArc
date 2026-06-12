# Lease Abstraction Pipeline

This project profiles one or more lease PDFs, extracts lease attributes with
Gemini, writes JSON and Excel artifacts, and persists the results to MongoDB.

MongoDB collections:

- `lease_ind_profiler`: one profiler document per PDF
- `lease_collated_profiler`: one merged profiler document per lease package
- `lease_result`: the final normalized lease result

The default MongoDB database is `lease`.

## Prerequisites

- Python 3.10 or newer
- MongoDB running locally or accessible through a connection URI
- Network access for PDF parsing and Gemini extraction
- A valid Gemini API key

## Setup

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Configure Gemini in `config.py`:

```python
GEMINI_API_KEY = "your-gemini-api-key"
GEMINI_MODEL = "gemini-model-name"
REUSE_EXISTING_MARKDOWN = True
EXTRACTOR_MAX_WORKERS = 4
```

Do not commit real API keys or cloud credentials to source control.

## MongoDB Configuration

The pipeline uses these defaults:

```text
MongoDB URI: mongodb://localhost:27017
Database:    lease
```

Override them with environment variables:

```bash
export MONGODB_DSN="mongodb://localhost:27017"
export MONGODB_DB="lease"
```

You can provide a stable lease ID for reruns:

```bash
export LEASE_ID="6385b240-5292-402a-a499-c37d422628b9"
```

If `LEASE_ID` is not supplied, the pipeline generates a UUID. Mongo writes use
upserts, so rerunning with the same lease ID replaces the corresponding
documents instead of creating duplicates.

Check that MongoDB is available:

```bash
mongosh --quiet --eval 'db.adminCommand({ping: 1})'
```

## Input Files

Place all PDFs belonging to a lease package in:

```text
docs/
```

`run_abstraction.py` processes every PDF directly inside this directory.
Subdirectories are not scanned.

When `REUSE_EXISTING_MARKDOWN = True`, matching Markdown files under
`outputs/markdown/` are reused. Set it to `False` to parse the PDFs again.

## Run the Full Pipeline

From the project root:

```bash
source .venv/bin/activate
python run_abstraction.py
```

The run performs the following operations:

1. Parses each PDF into Markdown.
2. Generates and stores each individual profiler result.
3. Collates all profiler results for the lease package.
4. Extracts attributes using Gemini.
5. Converts repeatable attributes into one result object per item.
6. Stores the final result in MongoDB.

## Generated Outputs

Important files are written under `outputs/`:

```text
outputs/profiler_results/*.json
outputs/merged_profiler_result.json
outputs/final_abstraction.json
outputs/final_abstraction_flattened.xlsx
outputs/lease_result.json
outputs/intermediate/
```

`outputs/lease_result.json` has the same normalized structure stored in the
`lease_result` MongoDB collection.

## Backfill Existing Outputs

To insert existing JSON artifacts without rerunning PDF parsing or extraction:

```bash
python -m tools.backfill_mongodb \
  --lease-id "6385b240-5292-402a-a499-c37d422628b9"
```

The command reads:

```text
outputs/profiler_results/*.json
outputs/merged_profiler_result.json
outputs/final_abstraction.json
```

Use a different output directory when needed:

```bash
python -m tools.backfill_mongodb \
  --lease-id "6385b240-5292-402a-a499-c37d422628b9" \
  --output-dir /path/to/outputs
```

## Verify MongoDB Records

```bash
mongosh --quiet --eval '
const database = db.getSiblingDB("lease");
const leaseId = "6385b240-5292-402a-a499-c37d422628b9";
printjson({
  individualProfiler: database.lease_ind_profiler.countDocuments({leaseId}),
  collatedProfiler: database.lease_collated_profiler.countDocuments({leaseId}),
  leaseResult: database.lease_result.countDocuments({leaseId})
});
'
```

View the final result:

```bash
mongosh --quiet --eval '
const database = db.getSiblingDB("lease");
printjson(database.lease_result.findOne({
  leaseId: "6385b240-5292-402a-a499-c37d422628b9"
}));
'
```

## Run Tests

```bash
python -m unittest discover -s tests -v
```

## Troubleshooting

`ModuleNotFoundError: pymongo`

```bash
python -m pip install -r requirements.txt
```

`MongoServerSelectionError`

- Confirm MongoDB is running.
- Verify `MONGODB_DSN`.
- Check network access and authentication settings.

`GEMINI_API_KEY is not set`

- Set `GEMINI_API_KEY` in `config.py`.
- Ensure the selected Gemini model is available to that API key.

No PDFs processed

- Confirm at least one `.pdf` file exists directly under `docs/`.

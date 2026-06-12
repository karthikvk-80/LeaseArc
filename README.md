# Lease Abstraction Pipeline

This project profiles one or more lease PDFs, extracts lease attributes with
Gemini, writes JSON and Excel artifacts, and persists the results to MongoDB.

MongoDB collections:

- `lease_markdown`: Markdown extracted from each S3 PDF
- `lease_ind_profiler`: one profiler document per PDF
- `lease_collated_profiler`: one merged profiler document per lease package
- `lease_result`: the final normalized lease result

The default MongoDB database is `lease`.

## Prerequisites

- Python 3.10 or newer
- MongoDB running locally or accessible through a connection URI
- AWS credentials with `s3:GetObject` permission for the input PDFs
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

## Configuration

The application reads AWS and MongoDB settings from `config.json`. Use the
provided templates to create local configuration files:

```bash
cp config.json.example config.json
cp config.py.example config.py
```

The `config.json` structure includes:

```json
{
  "defaults": {
    "bedrock_model_id": "openai.gpt-oss-120b-1:0"
  },
  "credentials": {
    "AWS_ACCESS_KEY_ID": "your-access-key",
    "AWS_SECRET_ACCESS_KEY": "your-secret-key",
    "AWS_SESSION_TOKEN": "",
    "AWS_REGION": "ap-south-1",
    "MONGODB_DSN": "mongodb://localhost:27017",
    "MONGODB_DB": "lease"
  }
}
```

Configuration notes:

- `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` are both required when using
  static credentials.
- `AWS_SESSION_TOKEN` is required only for temporary STS credentials.
- `AWS_REGION` must match the region used by S3 and Bedrock.
- The AWS identity needs `s3:GetObject` permission for every input PDF.
- `MONGODB_DSN` defaults to `mongodb://localhost:27017`.
- `MONGODB_DB` defaults to `lease`.
- Environment variables take precedence over values in `config.json`.

Gemini and worker settings are currently provided through environment
variables:

```bash
export GEMINI_API_KEY="your-gemini-api-key"
export GEMINI_MODEL="gemini-model-name"
export EXTRACTOR_MAX_WORKERS="4"
```

AWS and MongoDB values can also be overridden without editing `config.json`:

```bash
export AWS_ACCESS_KEY_ID="your-access-key"
export AWS_SECRET_ACCESS_KEY="your-secret-key"
export AWS_SESSION_TOKEN=""
export AWS_REGION="ap-south-1"
export MONGODB_DSN="mongodb://localhost:27017"
export MONGODB_DB="lease"
```

`config.py`, `config.json`, `.env`, private keys, and common credential files
are excluded by `.gitignore`. Do not force-add files containing real
credentials to source control.

## MongoDB Configuration

The configured MongoDB database contains:

- `lease_markdown`
- `lease_ind_profiler`
- `lease_collated_profiler`
- `lease_result`

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

Inputs may be one or more S3 object paths:

```text
s3://my-lease-bucket/base-lease.pdf
s3://my-lease-bucket/amendment-1.pdf
```

Each object is downloaded to a unique PDF file under `/tmp` by
`run_abstraction.py`. Temporary downloads are deleted after the full pipeline
finishes, including failed runs. Markdown is stored in `lease.lease_markdown`,
and extraction reads it back from MongoDB.

When no S3 paths are supplied, the runner processes every `.pdf` file directly
under `Data/pdf_files`.

## Run the Full Pipeline

From the project root:

```bash
source .venv/bin/activate
python run_abstraction.py s3://my-lease-bucket/base-lease.pdf
```

Run all PDFs in `Data/pdf_files`:

```bash
python run_abstraction.py
```

Run multiple related PDFs:

```bash
python run_abstraction.py \
  s3://my-lease-bucket/base-lease.pdf \
  s3://my-lease-bucket/amendment-1.pdf \
  s3://my-lease-bucket/loi.pdf
```

Provide a specific lease ID:

```bash
python run_abstraction.py \
  --lease-id "761d70de-d8f4-4e9b-bb07-bc17e8126c29" \
  s3://my-lease-bucket/base-lease.pdf
```

The run performs the following operations:

1. Generates one lease ID before processing begins.
2. Downloads each S3 PDF to `/tmp`.
3. Passes the local PDF path list into `main()`.
4. Extracts Markdown from each local PDF.
5. Stores Markdown in `lease_markdown` using the lease ID and file ID.
6. Generates and stores each individual profiler result.
7. Collates all profiler results for the lease package.
8. Reads Markdown from MongoDB and extracts attributes using Gemini.
9. Writes `outputs/lease_result.json`.
10. Writes `outputs/final_abstraction_format.json` and
   `outputs/expected_final_output.json`.
11. Stores that final result in MongoDB only after all JSON writes succeed.
12. Deletes only PDFs downloaded from S3.

## Generated Outputs

Important files are written under `outputs/`:

```text
outputs/profiler_results/*.json
outputs/merged_profiler_result.json
outputs/final_abstraction.json
outputs/final_abstraction_flattened.xlsx
outputs/lease_result.json
outputs/final_abstraction_format.json
outputs/expected_final_output.json
outputs/intermediate/
```

The following files contain the same normalized final payload stored in the
`lease_result` MongoDB collection:

- `outputs/lease_result.json`
- `outputs/final_abstraction_format.json`
- `outputs/expected_final_output.json`

Each final attribute includes `source_filename`, `source_type`, and
`source_s3_path`. Missing values are retained with confidence `0` and level
`low`.

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
  markdown: database.lease_markdown.countDocuments({leaseId}),
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

- Export `GEMINI_API_KEY` before running the command.
- Ensure the selected Gemini model is available to that API key.

S3 download fails

- Confirm every supplied input starts with `s3://`.
- Confirm AWS credentials can read the bucket and object.
- Confirm both access and secret keys are configured.
- For temporary credentials, confirm `AWS_SESSION_TOKEN` is present.
- Confirm the configured AWS region is correct.

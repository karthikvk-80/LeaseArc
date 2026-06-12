"""S3 storage for uploaded lease PDFs (Phase 2 lease persistence).

Used only for leases created via /upload and /upload-package — seeded/demo
leases and pre-Phase-2 JSON-backed leases keep serving PDF bytes from
db["lease_documents"] / backend/extractions/ as before.
"""
import os
from urllib.parse import quote, unquote, urlparse

import boto3

_S3_BUCKET = os.getenv("S3_BUCKET")
_S3_PREFIX = os.getenv("S3_PREFIX", "leasearc")

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            region_name=os.getenv("AWS_REGION"),
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        )
    return _client


def upload_pdf(lease_id: str, file_name: str, data: bytes) -> str:
    """Uploads PDF bytes to S3 and returns the s3:// URI (stored as lease_files.file_path),
    e.g. s3://<bucket>/leasearc/leases/<lease_id>/<file_name, URL-encoded>."""
    key = f"{_S3_PREFIX}/leases/{lease_id}/{file_name}"
    _get_client().put_object(Bucket=_S3_BUCKET, Key=key, Body=data, ContentType="application/pdf")
    return f"s3://{_S3_BUCKET}/{_S3_PREFIX}/leases/{lease_id}/{quote(file_name)}"


def download_pdf(file_path: str) -> bytes:
    """Downloads PDF bytes given a lease_files.file_path — either an s3:// URI (current
    format) or a bare S3 key (legacy rows predating the s3:// format)."""
    if file_path.startswith("s3://"):
        parsed = urlparse(file_path)
        bucket = parsed.netloc
        key = unquote(parsed.path.lstrip("/"))
    else:
        bucket = _S3_BUCKET
        key = file_path
    resp = _get_client().get_object(Bucket=bucket, Key=key)
    return resp["Body"].read()

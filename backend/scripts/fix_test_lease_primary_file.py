"""
One-off repair for the "Test" lease (16f4dfaa-82d9-41a2-a73d-a469ac4a3415).

This lease was uploaded via the folder/package upload flow. During Phase A
persistence, its single lease_files row was stored with the browser's relative
path as file_name ("Test/Krishe Emerald_Partial Surrender Letter.pdf"). During
Phase B (finalize_lease_persistence), the xts engine reported only the basename
("Krishe Emerald_Partial Surrender Letter.pdf"), so the file_name lookup never
matched the existing row — is_primary stayed False, and the "primary doc's
lease_files.id == leases.id" invariant (relied on by load_all_from_pg() /
GET /document/{lease_id}) was never applied.

This script applies that invariant retroactively for this one lease:
  - sets is_primary=True, doc_type='Base', pdf_type='scanned', page_count=<from PDF>
  - rewrites lease_files.id from 9de8d853-04db-459e-a811-62b98ac46212 to the
    lease's own id (16f4dfaa-82d9-41a2-a73d-a469ac4a3415)
  - sets leases.page_count to match

The general bug (file_name matching on full path vs. basename) is already fixed
in app/services/lease_persistence_pg.py for future folder uploads.

Usage:
    cd backend && python -m scripts.fix_test_lease_primary_file
"""
import os
import sys
import uuid

import fitz
from sqlalchemy import update

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from app.db import SessionLocal  # noqa: E402
from app.db_models import Lease, LeaseFile  # noqa: E402
from app.services.s3_storage import download_pdf  # noqa: E402

LEASE_ID = uuid.UUID("16f4dfaa-82d9-41a2-a73d-a469ac4a3415")
OLD_FILE_ID = uuid.UUID("9de8d853-04db-459e-a811-62b98ac46212")
FILE_PATH = (
    "s3://xts-learning-and-training/leasearc/leases/"
    "16f4dfaa-82d9-41a2-a73d-a469ac4a3415/Test/"
    "Krishe%20Emerald_Partial%20Surrender%20Letter.pdf"
)


def main():
    db = SessionLocal()
    try:
        pdf_bytes = download_pdf(FILE_PATH)
        page_count = fitz.open(stream=pdf_bytes, filetype="pdf").page_count
        print(f"page_count: {page_count}")

        row = db.get(LeaseFile, OLD_FILE_ID)
        if row is None:
            print("lease_files row not found — already repaired?")
            return
        row.is_primary = True
        row.doc_type = "Base"
        row.pdf_type = "scanned"
        row.page_count = page_count
        db.flush()

        db.execute(update(LeaseFile).where(LeaseFile.id == OLD_FILE_ID).values(id=LEASE_ID))

        lease = db.get(Lease, LEASE_ID)
        lease.page_count = page_count

        db.commit()
        print("repair committed")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()

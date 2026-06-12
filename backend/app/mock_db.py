# In-memory store — shared across all routers. No real database in v1.
db: dict = {
    "organisations": {},
    "portfolios": {},
    "locations": {},
    "users": {},
    "leases": {},
    "amendments": {},
    "attributes": {},       # keyed by lease_id -> list of attribute dicts
    "cam_statements": {},
    "cam_line_items": {},   # keyed by statement_id -> list of line item dicts
    "communications": {},
    "disputes": {},
    "dispute_letters": {},
    "upload_jobs": {},
    "bulk_batches": {},
    "negotiations": {},
    "lease_clauses": {},       # keyed by clause_id -> clause dict
    "rent_schedules": {},      # keyed by lease_id -> list of period dicts
    "cam_audit_sessions": {},  # keyed by session_id -> session dict
    "tasks": {},
    "obligations": {},
    "renewals": {},
    "payments": {},
    "workflow_rules": {},
    "month_end_checklist": {},
    "payment_schedules": {},       # keyed by schedule_id
    "payment_schedule_lines": {},  # keyed by schedule_id -> list
    "payment_actuals": {},         # keyed by actual_id
    "payment_matches": {},         # keyed by match_id
    "csv_profiles": {},            # keyed by profile_id
    "payment_exports": {},         # keyed by export_id
    "import_history": {},          # keyed by import_id
    # Cap 7 — Compliance
    "compliance_settings": {},          # keyed by org_id
    "ibrs": {},                         # keyed by ibr_id
    "compliance_periods": {},           # keyed by period (YYYY-MM)
    "compliance_waitlist": {},          # keyed by waitlist_id
    "compliance_lease_overrides": {},   # keyed by lease_id
    "compliance_journal_status": {},    # keyed by "{lease_id}_{period}"
    "compliance_gl_mapping": {},        # keyed by org_id
    # Cap 8 — Intelligence
    "landlords": {},               # keyed by landlord_id
    "anomalies": {},               # keyed by anomaly_id
    "intelligence_queries": {},    # keyed by query_id
    "forecast_scenarios": {},      # keyed by scenario_id
    # Org settings
    "org_settings": {},            # keyed by org_id
    # Pre-Leasing Pipeline
    "prospects": {},               # keyed by prospect_id
    "price_quotes": {},            # keyed by quote_id
    "loi_documents": {},           # keyed by loi_id
    "loi_clauses": {},             # keyed by clause_id
    "negotiation_threads": {},     # keyed by thread_id
    # Pre-Leasing — new stages
    "location_intel": {},          # keyed by prospect_id (one per prospect)
    "lease_docs": {},              # keyed by prospect_id (one per prospect)
    "lease_mismatches": {},        # keyed by mismatch_id
    "dd_reports": {},              # keyed by prospect_id (one per prospect)
    "portal_checks": {},           # keyed by check_id
    "lease_signatures": {},        # keyed by signature_id
    "lease_documents": {},         # keyed by lease_id OR documentId -> raw PDF bytes
    "lease_doc_sets": {},          # keyed by lease_id -> [ {documentId, fileName, docType, pdfType, isPrimary} ] (folder uploads)
    "token_usage": {},             # keyed by lease_id -> { extraction, locate_calls, total_cost_usd }
    "field_bboxes": {},            # keyed by lease_id -> { attribute_key -> bbox dict }
    "ocr_pages": {},               # keyed by lease_id -> { page_number -> {words, img_w, img_h} }
    "content_hash_cache": {},      # sha256_hex -> lease_id (skips re-extraction for identical PDF bytes)
    "pg_lease_ids": set(),          # lease_ids persisted to Postgres + S3 (new uploads, Phase 2)
}

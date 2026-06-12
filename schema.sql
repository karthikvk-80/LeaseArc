-- =============================================================================
-- User Management Module — PostgreSQL Schema (V1)
-- =============================================================================
-- Extensions
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "citext";     -- case-insensitive email matching


-- =============================================================================
-- 1. ORGANISATIONS
--    Root anchor for the entire schema. Multi-tenant ready.
-- =============================================================================

CREATE TABLE organisations (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR     NOT NULL,
    plan            VARCHAR     NOT NULL DEFAULT 'starter'
                        CHECK (plan IN ('starter', 'professional', 'enterprise')),
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    max_users       INTEGER     NOT NULL DEFAULT 10,
    max_leases      INTEGER     NOT NULL DEFAULT 100,
    feature_flags   JSONB       NOT NULL DEFAULT '{}',  -- e.g. {"compliance": true, "intelligence": false}
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW()
);


-- =============================================================================
-- 2. USERS
-- =============================================================================

CREATE TABLE users (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                  UUID        NOT NULL REFERENCES organisations(id) ON DELETE RESTRICT,
    first_name              VARCHAR     NOT NULL,
    last_name               VARCHAR     NOT NULL,
    email                   CITEXT      NOT NULL UNIQUE,          -- case-insensitive
    password_hash           TEXT        NOT NULL,                 -- Argon2id hash
    department              VARCHAR,                              -- display-only (e.g. re_director, legal)
    is_active               BOOLEAN     NOT NULL DEFAULT TRUE,
    is_verified             BOOLEAN     NOT NULL DEFAULT FALSE,
    failed_login_attempts   INTEGER     NOT NULL DEFAULT 0,
    locked_until            TIMESTAMP,                            -- NULL = not locked
    last_login_at           TIMESTAMP,
    created_at              TIMESTAMP   NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_users_org_id ON users(org_id);
CREATE INDEX idx_users_email  ON users(email);


-- =============================================================================
-- 3. ROLES
-- =============================================================================

CREATE TABLE roles (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR     NOT NULL,
    description TEXT,
    org_id      UUID        REFERENCES organisations(id) ON DELETE CASCADE,
    -- NULL = system-wide role ('admin', 'user'); set = org-specific custom role
    UNIQUE (name, org_id)
);

CREATE INDEX idx_roles_org_id ON roles(org_id);


-- =============================================================================
-- 4. PERMISSIONS
-- =============================================================================

CREATE TABLE permissions (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    key         VARCHAR     NOT NULL UNIQUE,   -- e.g. 'leases:view_all'
    description TEXT
);


-- =============================================================================
-- 5. ROLE_PERMISSIONS  (join table)
-- =============================================================================

CREATE TABLE role_permissions (
    role_id         UUID    NOT NULL REFERENCES roles(id)       ON DELETE CASCADE,
    permission_id   UUID    NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);


-- =============================================================================
-- 6. USER_ROLES  (join table — org-scoped)
-- =============================================================================

CREATE TABLE user_roles (
    user_id         UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id         UUID        NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
    org_id          UUID        NOT NULL REFERENCES organisations(id) ON DELETE RESTRICT,
    assigned_by     UUID        NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    assigned_at     TIMESTAMP   NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, role_id, org_id)
);

CREATE INDEX idx_user_roles_user_id ON user_roles(user_id);
CREATE INDEX idx_user_roles_org_id  ON user_roles(org_id);


-- =============================================================================
-- 7. LEASES
-- =============================================================================

CREATE TABLE leases (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                  UUID        NOT NULL REFERENCES organisations(id) ON DELETE RESTRICT,
    store_name              VARCHAR,
    store_code              VARCHAR,
    landlord_name           VARCHAR,
    tenant_name             VARCHAR,
    status                  VARCHAR,
    currency                VARCHAR,
    commencement_date       DATE,
    expiry_date             DATE,
    monthly_rent            NUMERIC(15, 2),
    portfolio_id            VARCHAR,
    city                    VARCHAR,
    country                 VARCHAR,
    address                 TEXT,
    pdf_type                VARCHAR     CHECK (pdf_type IN ('typed', 'scanned')),
    page_count              INTEGER,
    has_unreviewed_fields   BOOLEAN     NOT NULL DEFAULT FALSE,
    dismissed_from_history  BOOLEAN     NOT NULL DEFAULT FALSE,
    created_by              UUID        NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    assigned_to             UUID        REFERENCES users(id) ON DELETE SET NULL,  -- controls User-role visibility
    created_at              TIMESTAMP   NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_leases_org_id      ON leases(org_id);
CREATE INDEX idx_leases_created_by  ON leases(created_by);
CREATE INDEX idx_leases_assigned_to ON leases(assigned_to);
CREATE INDEX idx_leases_status      ON leases(status);


-- =============================================================================
-- 8. LEASE_ATTRIBUTES
-- =============================================================================

CREATE TABLE lease_attributes (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    lease_id            UUID        NOT NULL REFERENCES leases(id) ON DELETE CASCADE,
    org_id              UUID        NOT NULL REFERENCES organisations(id) ON DELETE RESTRICT,
    category            VARCHAR     NOT NULL,
    -- e.g. 'Core Lease Terms', 'Financial Obligations',
    --      'CAM and Operating Expenses', 'Restrictive Clauses', 'Critical Dates'
    attribute_key       VARCHAR     NOT NULL,   -- e.g. 'base_rent_monthly'
    attribute_name      VARCHAR     NOT NULL,   -- display label
    data_type           VARCHAR     NOT NULL    CHECK (data_type IN ('text', 'number', 'date', 'percentage')),
    extracted_value     TEXT,                   -- raw AI output
    user_edited_value   TEXT,                   -- user override; NULL = untouched
    confidence_score    INTEGER     CHECK (confidence_score BETWEEN 0 AND 100),
    confidence_level    VARCHAR     CHECK (confidence_level IN ('low', 'medium', 'high')),
    is_verified         BOOLEAN     NOT NULL DEFAULT FALSE,
    verified_by         UUID        REFERENCES users(id) ON DELETE SET NULL,
    verified_at         TIMESTAMP,
    page_number         INTEGER,
    source_clause       TEXT,                   -- verbatim clause from PDF
    translation         TEXT,                   -- English translation if non-English
    bbox                JSONB,                  -- {x1, y1, x2, y2, page}
    bbox_rects          JSONB,                  -- multi-line clause highlight rects
    is_key_field        BOOLEAN     NOT NULL DEFAULT FALSE,
    extra               JSONB                   -- non-schema extractor fields (confidence_reason,
                                                 -- source_document_id, group, slot_index, sub_field)
);

CREATE INDEX idx_lease_attributes_org_id        ON lease_attributes(org_id);
CREATE INDEX idx_lease_attributes_lease_id      ON lease_attributes(lease_id);
CREATE INDEX idx_lease_attributes_category      ON lease_attributes(category);
CREATE INDEX idx_lease_attributes_attribute_key ON lease_attributes(attribute_key);
CREATE INDEX idx_lease_attributes_is_verified   ON lease_attributes(is_verified);


-- =============================================================================
-- 9. LEASE_FILES
-- =============================================================================

CREATE TABLE lease_files (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    lease_id    UUID        NOT NULL REFERENCES leases(id) ON DELETE CASCADE,
    org_id      UUID        NOT NULL REFERENCES organisations(id) ON DELETE RESTRICT,
    file_name   VARCHAR     NOT NULL,
    file_path   TEXT        NOT NULL,   -- S3 key or filesystem path
    pdf_type    VARCHAR     CHECK (pdf_type IN ('typed', 'scanned')),
    page_count  INTEGER,
    is_primary  BOOLEAN     NOT NULL DEFAULT FALSE,
    doc_type    VARCHAR,    -- 'Base' | 'Amendment' | 'Renewal' (folder-upload manifest)
    uploaded_by UUID        NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    uploaded_at TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_lease_files_org_id   ON lease_files(org_id);
CREATE INDEX idx_lease_files_lease_id ON lease_files(lease_id);


-- =============================================================================
-- 10. LEASE_TOKEN_USAGE
-- =============================================================================

CREATE TABLE lease_token_usage (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    lease_id        UUID            NOT NULL REFERENCES leases(id) ON DELETE CASCADE,
    org_id          UUID            NOT NULL REFERENCES organisations(id) ON DELETE RESTRICT,
    file_name       VARCHAR,
    processed_at    TIMESTAMP       NOT NULL DEFAULT NOW(),
    pdf_type        VARCHAR,
    model           TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cost_usd        NUMERIC(10, 6),
    locate_calls    JSONB,          -- array of individual locate call records
    total_cost_usd  NUMERIC(10, 6)
);

CREATE INDEX idx_lease_token_usage_org_id   ON lease_token_usage(org_id);
CREATE INDEX idx_lease_token_usage_lease_id ON lease_token_usage(lease_id);


-- =============================================================================
-- 11. USER_SESSIONS
-- =============================================================================

CREATE TABLE user_sessions (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT        NOT NULL UNIQUE,  -- SHA-256 of refresh token
    device_info TEXT,                         -- user-agent string
    ip_address  TEXT,
    expires_at  TIMESTAMP   NOT NULL,
    revoked_at  TIMESTAMP,                    -- NULL = active
    created_at  TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_user_sessions_user_id    ON user_sessions(user_id);
CREATE INDEX idx_user_sessions_token_hash ON user_sessions(token_hash);
CREATE INDEX idx_user_sessions_expires_at ON user_sessions(expires_at);


-- =============================================================================
-- 12. PASSWORD_RESET_TOKENS
-- =============================================================================

CREATE TABLE password_reset_tokens (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT        NOT NULL UNIQUE,  -- SHA-256 of one-time token
    ip_address  TEXT,
    expires_at  TIMESTAMP   NOT NULL,         -- 1-hour window
    used_at     TIMESTAMP,                    -- NULL = not yet used
    created_at  TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_password_reset_tokens_user_id    ON password_reset_tokens(user_id);
CREATE INDEX idx_password_reset_tokens_token_hash ON password_reset_tokens(token_hash);


-- =============================================================================
-- 13. AUDIT_LOGS
--    Append-only — never UPDATE or DELETE rows in this table.
-- =============================================================================

CREATE TABLE audit_logs (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID        REFERENCES organisations(id) ON DELETE SET NULL,
    actor_id    UUID        REFERENCES users(id) ON DELETE SET NULL,  -- who triggered
    event_type  VARCHAR     NOT NULL,   -- e.g. 'user.login', 'user.login_failed'
    target_id   UUID,                   -- user or resource acted upon
    ip_address  TEXT,
    user_agent  TEXT,
    metadata    JSONB,                  -- flexible per-event context
    created_at  TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_logs_org_id     ON audit_logs(org_id);
CREATE INDEX idx_audit_logs_actor_id   ON audit_logs(actor_id);
CREATE INDEX idx_audit_logs_event_type ON audit_logs(event_type);
CREATE INDEX idx_audit_logs_target_id  ON audit_logs(target_id);
CREATE INDEX idx_audit_logs_created_at ON audit_logs(created_at DESC);


-- =============================================================================
-- TRIGGERS — keep updated_at current automatically
-- =============================================================================

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_leases_updated_at
    BEFORE UPDATE ON leases
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- =============================================================================
-- SEED DATA — Roles
-- =============================================================================

INSERT INTO roles (name, description) VALUES
    ('admin', 'Full access across all modules'),
    ('user',  'Standard access scoped to assigned leases');


-- =============================================================================
-- SEED DATA — Permissions
--    Keyed as  module:action  following the RBAC table in the design doc.
-- =============================================================================

INSERT INTO permissions (key, description) VALUES
    -- Users module
    ('users:create',                    'Create new users'),
    ('users:edit',                      'Edit any user'),
    ('users:deactivate',                'Deactivate users'),
    ('users:view_all',                  'View all users'),

    -- Leases module
    ('leases:upload',                   'Upload a lease'),
    ('leases:view_all',                 'View all leases'),
    ('leases:view_own',                 'View own/assigned leases only'),
    ('leases:edit_any',                 'Edit any lease'),
    ('leases:edit_own',                 'Edit own leases'),
    ('leases:delete',                   'Delete a lease'),
    ('leases:re_enrich',                'Re-run AI enrichment on a lease'),

    -- Lease Attributes
    ('lease_attributes:edit_any',       'Edit any lease attribute'),
    ('lease_attributes:edit_own',       'Edit attributes on own leases'),
    ('lease_attributes:verify_any',     'Verify any lease attribute'),
    ('lease_attributes:verify_own',     'Verify attributes on own leases'),

    -- Amendments
    ('amendments:upload',               'Upload amendments'),
    ('amendments:view_all',             'View all amendments'),
    ('amendments:view_own',             'View amendments on own leases'),

    -- Administration
    ('administration:view_all',         'View all admin data'),
    ('administration:manage_tasks',     'Manage tasks'),
    ('administration:manage_workflow',  'Manage workflow rules'),
    ('administration:manage_alerts',    'Manage alert configuration'),
    ('administration:view_own',         'View own dates and tasks'),
    ('administration:create_comms',     'Create communications'),

    -- CAM
    ('cam:view',                        'View CAM data'),
    ('cam:upload',                      'Upload CAM statements'),
    ('cam:run_audit',                   'Run CAM audit sessions'),

    -- Compliance
    ('compliance:view_reports',         'View compliance reports'),
    ('compliance:manage_settings',      'Manage IBR and GL mapping settings'),
    ('compliance:approve_journals',     'Approve compliance journals'),

    -- Intelligence
    ('intelligence:view_anomalies',     'View AI-detected anomalies'),
    ('intelligence:run_ai_query',       'Run AI queries'),
    ('intelligence:manage_forecasts',   'Manage forecasts'),
    ('intelligence:manage_org_settings','Manage organisation-level AI settings'),
    ('intelligence:view_landlords',     'View landlord intelligence data'),

    -- Negotiations
    ('negotiations:view',               'View negotiations'),
    ('negotiations:draft_send',         'Draft and send letters'),
    ('negotiations:draft_send_own',     'Draft and send letters on own leases'),

    -- Payments
    ('payments:view',                   'View payments'),
    ('payments:import',                 'Import payment data'),
    ('payments:confirm_reject',         'Confirm or reject payment matches'),

    -- Pre-Leasing
    ('preleasing:view_all',             'View all pre-leasing items'),
    ('preleasing:manage_all',           'Manage all: prospects, quotes, LOI, due diligence'),
    ('preleasing:view_own',             'View own pre-leasing items'),
    ('preleasing:manage_own',           'Manage own: prospects, quotes, LOI, due diligence'),

    -- Audit Logs
    ('audit_logs:view_all',             'View all audit log events');


-- =============================================================================
-- SEED DATA — Role → Permission mappings
-- =============================================================================

-- ADMIN gets every permission
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM   roles r, permissions p
WHERE  r.name = 'admin';


-- USER (Standard) gets a scoped subset
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM   roles r
JOIN   permissions p ON p.key IN (
    'leases:upload',
    'leases:view_own',
    'leases:edit_own',
    'lease_attributes:edit_own',
    'lease_attributes:verify_own',
    'amendments:upload',
    'amendments:view_own',
    'administration:view_own',
    'administration:manage_tasks',
    'administration:create_comms',
    'cam:view',
    'cam:upload',
    'compliance:view_reports',
    'intelligence:view_anomalies',
    'intelligence:run_ai_query',
    'intelligence:view_landlords',
    'negotiations:view',
    'negotiations:draft_send_own',
    'payments:view',
    'payments:import',
    'payments:confirm_reject',
    'preleasing:view_own',
    'preleasing:manage_own'
)
WHERE r.name = 'user';


-- =============================================================================
-- ROW-LEVEL SECURITY (RLS)
--    Enforces tenant isolation at the database layer.
--    The application sets app.current_org_id on every connection before querying.
--    Even if application code forgets a WHERE clause, no cross-tenant data leaks.
-- =============================================================================

-- Enable RLS on every tenant-scoped table
ALTER TABLE users               ENABLE ROW LEVEL SECURITY;
ALTER TABLE leases              ENABLE ROW LEVEL SECURITY;
ALTER TABLE lease_attributes    ENABLE ROW LEVEL SECURITY;
ALTER TABLE lease_files         ENABLE ROW LEVEL SECURITY;
ALTER TABLE lease_token_usage   ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_roles          ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs          ENABLE ROW LEVEL SECURITY;

-- RLS policies — one per table, all follow the same pattern
CREATE POLICY tenant_isolation ON users
    USING (org_id = current_setting('app.current_org_id')::UUID);

CREATE POLICY tenant_isolation ON leases
    USING (org_id = current_setting('app.current_org_id')::UUID);

CREATE POLICY tenant_isolation ON lease_attributes
    USING (org_id = current_setting('app.current_org_id')::UUID);

CREATE POLICY tenant_isolation ON lease_files
    USING (org_id = current_setting('app.current_org_id')::UUID);

CREATE POLICY tenant_isolation ON lease_token_usage
    USING (org_id = current_setting('app.current_org_id')::UUID);

CREATE POLICY tenant_isolation ON user_roles
    USING (org_id = current_setting('app.current_org_id')::UUID);

CREATE POLICY tenant_isolation ON audit_logs
    USING (org_id = current_setting('app.current_org_id')::UUID);

-- Superuser / migration role bypasses RLS so seeds and migrations still work
-- Grant this role only to the migration user, never to the app user.
-- ALTER TABLE ... FORCE ROW LEVEL SECURITY  <-- add this to block even table owners

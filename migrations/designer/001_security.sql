-- Agent Designer migration 001: security foundation.
-- Tables that must exist before any Designer route serves traffic:
-- sessions, write-only encrypted credentials, RBAC grants, per-agent
-- access policy, the control-plane audit stream, and revision lifecycle
-- events (append-only; FK to designer_revisions is added in 002).

-- Migration ledger: id = filename stem, checksum = sha256 of applied bytes.
CREATE TABLE IF NOT EXISTS designer_migrations (
    id         TEXT PRIMARY KEY,
    checksum   TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Write-only encrypted upstream credentials. Plaintext never stored;
-- nonce is unique per key (R09: fresh random 96-bit nonce per encryption,
-- uniqueness enforced for a key).
CREATE TABLE IF NOT EXISTS designer_credentials (
    credential_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id TEXT NOT NULL,
    purpose       TEXT NOT NULL,
    kind          TEXT NOT NULL,
    key_id        TEXT NOT NULL,
    nonce         BYTEA NOT NULL,
    ciphertext    BYTEA NOT NULL,
    generation    INTEGER NOT NULL DEFAULT 1,
    scope         TEXT NOT NULL DEFAULT 'user',
    status        TEXT NOT NULL DEFAULT 'active',
    label         TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    rotated_at    TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS designer_credentials_key_nonce_idx
    ON designer_credentials (key_id, nonce);
CREATE INDEX IF NOT EXISTS designer_credentials_owner_idx
    ON designer_credentials (owner_user_id);

-- Designer sessions: only hashed tokens are stored; the raw session and
-- CSRF tokens exist only in cookie/header values handed to the browser.
CREATE TABLE IF NOT EXISTS designer_sessions (
    session_id_hash TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'user',
    auth_mode       TEXT NOT NULL,
    credential_id   UUID REFERENCES designer_credentials(credential_id) ON DELETE CASCADE,
    csrf_token_hash TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    revoked_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS designer_sessions_user_idx ON designer_sessions (user_id);

-- Explicit Designer permission grants (C1). Upstream role mapping provides
-- defaults; rows here extend or restrict for a specific user.
CREATE TABLE IF NOT EXISTS designer_grants (
    user_id    TEXT NOT NULL,
    permission TEXT NOT NULL,
    granted_by TEXT NOT NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, permission)
);

-- Per-agent access policy (C1). agent_id is TEXT here; the FK to
-- designer_agents is added additively in migration 002.
CREATE TABLE IF NOT EXISTS designer_agent_access (
    agent_id    TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    can_use     BOOLEAN NOT NULL DEFAULT TRUE,
    can_edit    BOOLEAN NOT NULL DEFAULT FALSE,
    can_activate BOOLEAN NOT NULL DEFAULT FALSE,
    granted_by  TEXT NOT NULL,
    granted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, user_id)
);

-- Append-only control-plane audit stream (C4). IDs and safe metadata
-- only: never credential values, bearer tokens, passwords, full prompts,
-- or sensitive tool outputs. Separate from run Live events.
CREATE TABLE IF NOT EXISTS designer_audit_events (
    audit_id      BIGSERIAL PRIMARY KEY,
    at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_user_id TEXT NOT NULL,
    event         TEXT NOT NULL,
    subject       JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS designer_audit_events_at_idx ON designer_audit_events (at DESC);

-- Append-only revision lifecycle records (Clarification 3): activation,
-- validation, revocation facts never rewrite the immutable revision row.
-- agent_id/revision_id are TEXT until migration 002 adds the FKs.
CREATE TABLE IF NOT EXISTS designer_revision_events (
    event_id      BIGSERIAL PRIMARY KEY,
    revision_id   TEXT NOT NULL,
    agent_id      TEXT NOT NULL,
    event         TEXT NOT NULL,
    actor_user_id TEXT NOT NULL,
    data          JSONB NOT NULL DEFAULT '{}'::jsonb,
    at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS designer_revision_events_revision_idx
    ON designer_revision_events (revision_id, at);

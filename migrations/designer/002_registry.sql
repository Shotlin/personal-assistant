-- Agent Designer migration 002: agent registry and immutable revisions.
-- designer_agents holds the mutable active pointer; designer_revisions
-- holds immutable graph content (Clarification 3: lifecycle facts never
-- rewrite revision rows).

CREATE TABLE IF NOT EXISTS designer_agents (
    agent_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id      TEXT NOT NULL,
    slug               TEXT NOT NULL,
    display_name       TEXT NOT NULL,
    description        TEXT NOT NULL DEFAULT '',
    enabled            BOOLEAN NOT NULL DEFAULT TRUE,
    archived           BOOLEAN NOT NULL DEFAULT FALSE,
    active_revision_id TEXT,
    row_version        INTEGER NOT NULL DEFAULT 1,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS designer_agents_owner_slug_idx
    ON designer_agents (owner_user_id, slug);

CREATE TABLE IF NOT EXISTS designer_revisions (
    revision_id     TEXT PRIMARY KEY,
    agent_id        UUID NOT NULL REFERENCES designer_agents(agent_id),
    revision_number INTEGER NOT NULL,
    schema_version  INTEGER NOT NULL,
    graph_json      JSONB NOT NULL,
    semantic_hash   TEXT NOT NULL,
    layout_hash     TEXT NOT NULL,
    dependency_lock JSONB NOT NULL DEFAULT '{}'::jsonb,
    validation      JSONB,
    parent_revision_id TEXT,
    created_by      TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (agent_id, revision_number)
);

-- Backfill-safe FKs for tables created in 001 with TEXT columns.
-- agent_id was TEXT in 001; cast it to UUID to match designer_agents.
ALTER TABLE designer_agent_access
    ALTER COLUMN agent_id TYPE uuid
    USING agent_id::uuid;
ALTER TABLE designer_agent_access
    ADD CONSTRAINT designer_agent_access_agent_fk
    FOREIGN KEY (agent_id) REFERENCES designer_agents(agent_id) ON DELETE CASCADE;

ALTER TABLE designer_revision_events
    ADD CONSTRAINT designer_revision_events_revision_fk
    FOREIGN KEY (revision_id) REFERENCES designer_revisions(revision_id);

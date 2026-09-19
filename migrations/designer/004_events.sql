-- Agent Designer migration 004: durable monotonic run events stream (P8, R04, R17, R18).
-- Supports Live view streaming via SSE, Last-Event-ID replay, and event-driven debugging.
CREATE TABLE IF NOT EXISTS designer_run_events (
    event_id        BIGSERIAL PRIMARY KEY,
    run_id          TEXT NOT NULL,
    agent_id        TEXT NOT NULL DEFAULT '',
    revision_id     TEXT NOT NULL DEFAULT '',
    sequence_number INTEGER NOT NULL,
    event_type      TEXT NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS designer_run_events_run_seq_idx
    ON designer_run_events (run_id, sequence_number);

CREATE INDEX IF NOT EXISTS designer_run_events_run_event_id_idx
    ON designer_run_events (run_id, event_id);

CREATE INDEX IF NOT EXISTS designer_run_events_agent_idx
    ON designer_run_events (agent_id, at);

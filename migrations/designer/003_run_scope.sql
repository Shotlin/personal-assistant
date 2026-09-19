-- Agent Designer migration 003: run scoping and agent-aware dedup.
-- Extends the existing run_registry additively; replaces the legacy
-- dedup index only after duplicate checks (R20/R21, plan P5).

ALTER TABLE run_registry ADD COLUMN IF NOT EXISTS agent_id TEXT NOT NULL DEFAULT '';
ALTER TABLE run_registry ADD COLUMN IF NOT EXISTS revision_id TEXT NOT NULL DEFAULT '';
ALTER TABLE run_registry ADD COLUMN IF NOT EXISTS attempt INTEGER NOT NULL DEFAULT 1;

-- Agent-aware uniqueness: the same Open WebUI message may exist once per
-- (user, agent) pair. Legacy rows carry agent_id = '' (Vion pre-Designer),
-- so the legacy behavior is preserved bit-for-bit for old rows.
-- Duplicate check before the swap: with the legacy index in place there
-- cannot be any (user_id, agent_id, user_message_id) collision, but the
-- check is cheap and makes the invariant explicit for the ledger.
DO $$
DECLARE
    collisions INTEGER;
BEGIN
    SELECT COUNT(*) INTO collisions FROM (
        SELECT user_id, agent_id, user_message_id
        FROM run_registry
        GROUP BY user_id, agent_id, user_message_id
        HAVING COUNT(*) > 1
    ) dup;
    IF collisions > 0 THEN
        RAISE EXCEPTION 'run_registry has % duplicate (user, agent, message) identities; reconcile before migrating', collisions;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS run_registry_turn_agent_idx
    ON run_registry (user_id, agent_id, user_message_id);
DROP INDEX IF EXISTS run_registry_turn_idx;

-- Agent config extension: bootstrap metadata (e.g. legacy namespace flag).
ALTER TABLE designer_agents ADD COLUMN IF NOT EXISTS config JSONB NOT NULL DEFAULT '{}'::jsonb;

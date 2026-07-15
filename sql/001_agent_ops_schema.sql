BEGIN;

CREATE SCHEMA IF NOT EXISTS agent_ops;

CREATE TABLE IF NOT EXISTS agent_ops.agent_runs (
    run_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    user_request TEXT NOT NULL,
    status TEXT NOT NULL,
    final_response TEXT,
    turns INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS agent_ops.agent_messages (
    id BIGSERIAL PRIMARY KEY,
    message_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES agent_ops.agent_runs(run_id) ON DELETE CASCADE,
    turn INTEGER NOT NULL,
    role TEXT NOT NULL,
    name TEXT,
    content TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_ops.agent_tool_calls (
    tool_call_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_ops.agent_runs(run_id) ON DELETE CASCADE,
    turn INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    status TEXT NOT NULL,
    arguments JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB NOT NULL DEFAULT '{}'::jsonb,
    error TEXT,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS agent_ops.agent_events (
    id BIGSERIAL PRIMARY KEY,
    event_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES agent_ops.agent_runs(run_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_ops.agent_notifications (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_ops.agent_runs(run_id) ON DELETE CASCADE,
    severity TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_ops.agent_notes (
    id BIGSERIAL PRIMARY KEY,
    note_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES agent_ops.agent_runs(run_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    note TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One deterministic review_id is used per run/turn so retried Temporal
-- activities upsert the same review record instead of duplicating it.
-- Decision fields remain nullable while the recommendation is awaiting review.
CREATE TABLE IF NOT EXISTS agent_ops.agent_review_decisions (
    review_id TEXT PRIMARY KEY,
    decision_id TEXT,
    run_id TEXT NOT NULL REFERENCES agent_ops.agent_runs(run_id) ON DELETE CASCADE,
    reviewer_id TEXT,
    decision TEXT CHECK (
        decision IS NULL OR decision IN ('approve', 'deny', 'request_more_info')
    ),
    rationale TEXT,
    model_recommendation JSONB NOT NULL DEFAULT '{}'::jsonb,
    decided_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS agent_ops.agent_workflow_metrics (
    run_id TEXT PRIMARY KEY REFERENCES agent_ops.agent_runs(run_id) ON DELETE CASCADE,
    workflow_id TEXT NOT NULL,
    workflow_run_id TEXT NOT NULL,
    workflow_type TEXT NOT NULL,
    task_queue TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    latency_ms INTEGER,
    turn_count INTEGER DEFAULT 0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_ops.agent_turn_metrics (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_ops.agent_runs(run_id) ON DELETE CASCADE,
    workflow_id TEXT NOT NULL,
    workflow_run_id TEXT NOT NULL,
    workflow_type TEXT NOT NULL,
    turn INTEGER NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    latency_ms INTEGER NOT NULL,
    model_latency_ms INTEGER NOT NULL DEFAULT 0,
    tool_latency_ms INTEGER NOT NULL DEFAULT 0,
    tool_call_count INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, turn)
);

CREATE TABLE IF NOT EXISTS agent_ops.agent_activity_attempt_metrics (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_ops.agent_runs(run_id) ON DELETE CASCADE,
    workflow_id TEXT NOT NULL,
    workflow_run_id TEXT NOT NULL,
    workflow_type TEXT NOT NULL,
    activity_id TEXT NOT NULL,
    activity_type TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    turn INTEGER,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    latency_ms INTEGER NOT NULL,
    error_type TEXT,
    error_message TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, activity_id, attempt)
);

-- Add deterministic idempotency keys when upgrading an existing demo schema.
ALTER TABLE agent_ops.agent_messages ADD COLUMN IF NOT EXISTS message_id TEXT;
UPDATE agent_ops.agent_messages
SET message_id = run_id || '-legacy-message-' || id
WHERE message_id IS NULL;
ALTER TABLE agent_ops.agent_messages ALTER COLUMN message_id SET NOT NULL;
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM agent_ops.agent_messages
        GROUP BY message_id
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION
            'agent_ops.agent_messages has duplicate message_id values; deduplicate them before rerunning this migration';
    END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_messages_message_id
    ON agent_ops.agent_messages (message_id);

ALTER TABLE agent_ops.agent_events ADD COLUMN IF NOT EXISTS event_id TEXT;
UPDATE agent_ops.agent_events
SET event_id = run_id || '-legacy-event-' || id
WHERE event_id IS NULL;
ALTER TABLE agent_ops.agent_events ALTER COLUMN event_id SET NOT NULL;
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM agent_ops.agent_events
        GROUP BY event_id
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION
            'agent_ops.agent_events has duplicate event_id values; deduplicate them before rerunning this migration';
    END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_events_event_id
    ON agent_ops.agent_events (event_id);

ALTER TABLE agent_ops.agent_notes ADD COLUMN IF NOT EXISTS note_id TEXT;
UPDATE agent_ops.agent_notes
SET note_id = run_id || '-legacy-note-' || id
WHERE note_id IS NULL;
ALTER TABLE agent_ops.agent_notes ALTER COLUMN note_id SET NOT NULL;
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM agent_ops.agent_notes
        GROUP BY note_id
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION
            'agent_ops.agent_notes has duplicate note_id values; deduplicate them before rerunning this migration';
    END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_notes_note_id
    ON agent_ops.agent_notes (note_id);

ALTER TABLE agent_ops.agent_review_decisions
    ADD COLUMN IF NOT EXISTS decision_id TEXT;

CREATE INDEX IF NOT EXISTS idx_agent_runs_user_status_updated
    ON agent_ops.agent_runs (user_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_messages_run_turn_created
    ON agent_ops.agent_messages (run_id, turn, created_at);

CREATE INDEX IF NOT EXISTS idx_agent_events_run_created
    ON agent_ops.agent_events (run_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_notifications_unread
    ON agent_ops.agent_notifications (run_id, created_at DESC)
    WHERE read_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_agent_notes_user_created
    ON agent_ops.agent_notes (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_review_decisions_run_decided
    ON agent_ops.agent_review_decisions (run_id, decided_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_workflow_metrics_type_status
    ON agent_ops.agent_workflow_metrics (workflow_type, status, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_turn_metrics_run_turn
    ON agent_ops.agent_turn_metrics (run_id, turn);

CREATE INDEX IF NOT EXISTS idx_agent_activity_attempts_type_started
    ON agent_ops.agent_activity_attempt_metrics (activity_type, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_activity_attempts_retry
    ON agent_ops.agent_activity_attempt_metrics (workflow_type, activity_type, attempt)
    WHERE attempt > 1;

CREATE OR REPLACE VIEW agent_ops.agent_activity_retry_counts AS
WITH per_activity AS (
    SELECT
        run_id,
        workflow_id,
        workflow_run_id,
        workflow_type,
        activity_type,
        activity_id,
        MAX(attempt) AS max_attempt,
        MAX(completed_at) AS last_attempt_completed_at
    FROM agent_ops.agent_activity_attempt_metrics
    GROUP BY
        run_id,
        workflow_id,
        workflow_run_id,
        workflow_type,
        activity_type,
        activity_id
)
SELECT
    run_id,
    workflow_id,
    workflow_run_id,
    workflow_type,
    activity_type,
    SUM(max_attempt) AS attempt_count,
    CAST(SUM(GREATEST(max_attempt - 1, 0)) AS INTEGER) AS retry_count,
    MAX(last_attempt_completed_at) AS last_attempt_completed_at
FROM per_activity
GROUP BY run_id, workflow_id, workflow_run_id, workflow_type, activity_type;

-- Required before enabling Lakebase Change Data Feed into Unity Catalog.
-- Lakebase CDF is enabled from the Lakebase UI at the schema level.
ALTER TABLE agent_ops.agent_runs REPLICA IDENTITY FULL;
ALTER TABLE agent_ops.agent_messages REPLICA IDENTITY FULL;
ALTER TABLE agent_ops.agent_tool_calls REPLICA IDENTITY FULL;
ALTER TABLE agent_ops.agent_events REPLICA IDENTITY FULL;
ALTER TABLE agent_ops.agent_notifications REPLICA IDENTITY FULL;
ALTER TABLE agent_ops.agent_notes REPLICA IDENTITY FULL;
ALTER TABLE agent_ops.agent_review_decisions REPLICA IDENTITY FULL;
ALTER TABLE agent_ops.agent_workflow_metrics REPLICA IDENTITY FULL;
ALTER TABLE agent_ops.agent_turn_metrics REPLICA IDENTITY FULL;
ALTER TABLE agent_ops.agent_activity_attempt_metrics REPLICA IDENTITY FULL;

COMMIT;

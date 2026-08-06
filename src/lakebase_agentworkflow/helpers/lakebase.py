from __future__ import annotations

import os
import re
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

from lakebase_agentworkflow.helpers.models import (
    ActivityAttemptMetricRecord,
    AgentMessage,
    AgentRunStatus,
    LakebaseEvent,
    LakebaseWriteResult,
    LoanRecommendation,
    MessageRecord,
    RecentContextRequest,
    ReviewDecision,
    ReviewDecisionPayload,
    ReviewDecisionRecord,
    ReviewPendingRecord,
    RunCompletedRecord,
    RunStartedRecord,
    TokenUsage,
    ToolCallRecord,
    ToolName,
    ToolResult,
    ToolResultRecord,
    TurnMetricRecord,
    UnderwritingPolicy,
    WorkflowMetricCompletedRecord,
    WorkflowMetricStartedRecord,
    WorkflowRunView,
)


IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class LakebaseSettings:
    def __init__(self) -> None:
        schema = os.getenv("LAKEBASE_SCHEMA", "agent_ops")
        if not IDENTIFIER_RE.match(schema):
            raise ValueError(f"Unsafe SQL identifier: {schema!r}")
        policy_schema = os.getenv("LAKEBASE_POLICY_SCHEMA", "agent_policy")
        if not IDENTIFIER_RE.match(policy_schema):
            raise ValueError(f"Unsafe SQL identifier: {policy_schema!r}")
        self.mode = os.getenv("LAKEBASE_CONNECTION_MODE", "disabled").strip().lower()
        self.schema = schema
        self.policy_schema = policy_schema
        self.database = os.getenv("LAKEBASE_DATABASE", os.getenv("PGDATABASE", "databricks_postgres"))
        self.pool_size = int(os.getenv("LAKEBASE_POOL_SIZE", "5"))
        self.max_overflow = int(os.getenv("LAKEBASE_MAX_OVERFLOW", "10"))
        self.token_refresh_seconds = int(os.getenv("LAKEBASE_TOKEN_REFRESH_SECONDS", "2400"))


class LakebaseRepository:
    def __init__(self, settings: LakebaseSettings | None = None) -> None:
        self.settings = settings or LakebaseSettings()
        self._lock = threading.Lock()
        self._pool: Any | None = None
        self._pool_refresh_at = 0.0

    def record_run_started(self, record: RunStartedRecord) -> LakebaseWriteResult:
        sql = f"""
            INSERT INTO {self.settings.schema}.agent_runs AS current_run
              (run_id, workflow_id, user_id, user_request, status, metadata, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, now(), now())
            ON CONFLICT (run_id) DO UPDATE SET
              workflow_id = EXCLUDED.workflow_id,
              status = EXCLUDED.status,
              metadata = EXCLUDED.metadata,
              updated_at = now()
            WHERE current_run.status IN ('pending', 'running')
              AND COALESCE(current_run.turns, 0) = 0
        """
        return self._execute(
            sql,
            (
                record.run_id,
                record.workflow_id,
                record.user_id,
                record.user_request,
                AgentRunStatus.RUNNING.value,
                _jsonb(record.metadata),
            ),
        )

    def record_message(self, record: MessageRecord) -> LakebaseWriteResult:
        sql = f"""
            INSERT INTO {self.settings.schema}.agent_messages
              (message_id, run_id, turn, role, name, content, metadata, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (message_id) DO UPDATE SET
              turn = EXCLUDED.turn,
              role = EXCLUDED.role,
              name = EXCLUDED.name,
              content = EXCLUDED.content,
              metadata = EXCLUDED.metadata
        """
        message = record.message
        return self._execute(
            sql,
            (
                record.message_id,
                record.run_id,
                record.turn,
                message.role,
                message.name,
                message.content,
                _jsonb(message.metadata),
            ),
        )

    def record_tool_call(self, record: ToolCallRecord) -> LakebaseWriteResult:
        tool_call = record.tool_call
        sql = f"""
            INSERT INTO {self.settings.schema}.agent_tool_calls AS current_tool
              (tool_call_id, run_id, turn, tool_name, status, arguments, requested_at)
            VALUES (%s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (tool_call_id) DO UPDATE SET
              status = EXCLUDED.status,
              arguments = EXCLUDED.arguments,
              requested_at = EXCLUDED.requested_at
            WHERE current_tool.run_id = EXCLUDED.run_id
              AND current_tool.tool_name = EXCLUDED.tool_name
              AND current_tool.status NOT IN ('succeeded', 'failed')
        """
        return self._execute(
            sql,
            (
                tool_call.id,
                record.run_id,
                record.turn,
                tool_call.name.value,
                "started",
                _jsonb(tool_call.arguments),
            ),
        )

    def record_tool_result(self, record: ToolResultRecord) -> LakebaseWriteResult:
        result = record.result
        sql = f"""
            INSERT INTO {self.settings.schema}.agent_tool_calls AS current_tool
              (tool_call_id, run_id, turn, tool_name, status, result, error, completed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (tool_call_id) DO UPDATE SET
              status = EXCLUDED.status,
              result = EXCLUDED.result,
              error = EXCLUDED.error,
              completed_at = now()
            WHERE current_tool.run_id = EXCLUDED.run_id
              AND current_tool.tool_name = EXCLUDED.tool_name
        """
        write = self._execute(
            sql,
            (
                result.tool_call_id,
                record.run_id,
                record.turn,
                result.name.value,
                "succeeded" if result.success else "failed",
                _jsonb(result.model_dump(mode="json")),
                result.error,
            ),
        )
        if result.name == ToolName.SAVE_NOTE and result.success:
            self._execute(
                f"""
                    INSERT INTO {self.settings.schema}.agent_notes
                      (note_id, run_id, user_id, note, metadata, created_at)
                    VALUES (%s, %s, %s, %s, %s, now())
                    ON CONFLICT (note_id) DO UPDATE SET
                      user_id = EXCLUDED.user_id,
                      note = EXCLUDED.note,
                      metadata = EXCLUDED.metadata
                """,
                (
                    result.tool_call_id,
                    record.run_id,
                    result.data.get("user_id", "unknown"),
                    result.data.get("note", ""),
                    _jsonb(result.data),
                ),
            )
        return write

    def record_review_pending(self, record: ReviewPendingRecord) -> LakebaseWriteResult:
        metadata = {
            "recommendation": record.recommendation.model_dump(mode="json"),
            "review": {
                "status": "pending",
                "round": record.review_round,
            },
            "total_tokens": record.usage.total_tokens,
        }
        write = self._execute(
            f"""
                INSERT INTO {self.settings.schema}.agent_review_decisions AS current_review
                  (review_id, run_id, model_recommendation)
                VALUES (%s, %s, %s)
                ON CONFLICT (review_id) DO UPDATE SET
                  model_recommendation = EXCLUDED.model_recommendation
            """,
            (
                f"{record.run_id}-review-{record.review_round}",
                record.run_id,
                _jsonb(record.recommendation.model_dump(mode="json")),
            ),
        )
        self._execute(
            f"""
                UPDATE {self.settings.schema}.agent_runs
                SET status = %s,
                    turns = %s,
                    input_tokens = %s,
                    output_tokens = %s,
                    total_tokens = %s,
                    metadata = COALESCE(metadata, '{{}}'::jsonb) || %s,
                    updated_at = now(),
                    completed_at = NULL
                WHERE run_id = %s
                  AND status NOT IN ('completed', 'failed', 'cancelled')
                  AND COALESCE(
                        NULLIF(metadata #>> '{{review,round}}', '')::integer,
                        0
                      ) <= %s
                  AND NOT (
                        COALESCE(
                          NULLIF(metadata #>> '{{review,round}}', '')::integer,
                          0
                        ) = %s
                        AND COALESCE(metadata #>> '{{review,status}}', '')
                          IN ('decided', 'more_info_requested')
                      )
            """,
            (
                AgentRunStatus.AWAITING_REVIEW.value,
                record.review_round,
                record.usage.input_tokens,
                record.usage.output_tokens,
                record.usage.total_tokens,
                _jsonb(metadata),
                record.run_id,
                record.review_round,
                record.review_round,
            ),
        )
        self._execute(
            f"""
                UPDATE {self.settings.schema}.agent_workflow_metrics
                SET status = %s,
                    turn_count = %s,
                    metadata = COALESCE(metadata, '{{}}'::jsonb) || %s,
                    updated_at = now()
                WHERE run_id = %s
                  AND status NOT IN ('completed', 'failed', 'cancelled')
                  AND COALESCE(
                        NULLIF(metadata #>> '{{review,round}}', '')::integer,
                        0
                      ) <= %s
                  AND NOT (
                        COALESCE(
                          NULLIF(metadata #>> '{{review,round}}', '')::integer,
                          0
                        ) = %s
                        AND COALESCE(metadata #>> '{{review,status}}', '')
                          IN ('decided', 'more_info_requested')
                      )
            """,
            (
                AgentRunStatus.AWAITING_REVIEW.value,
                record.review_round,
                _jsonb(metadata),
                record.run_id,
                record.review_round,
                record.review_round,
            ),
        )
        return write

    def record_review_decision(self, record: ReviewDecisionRecord) -> LakebaseWriteResult:
        write = self._execute(
            f"""
                INSERT INTO {self.settings.schema}.agent_review_decisions AS current_review
                  (review_id, decision_id, run_id, reviewer_id, decision, rationale,
                   model_recommendation, decided_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (review_id) DO UPDATE SET
                  decision_id = EXCLUDED.decision_id,
                  reviewer_id = EXCLUDED.reviewer_id,
                  decision = EXCLUDED.decision,
                  rationale = EXCLUDED.rationale,
                  model_recommendation = EXCLUDED.model_recommendation,
                  decided_at = EXCLUDED.decided_at
                WHERE current_review.decision_id IS NULL
                   OR current_review.decision_id = EXCLUDED.decision_id
            """,
            (
                record.review_id,
                record.decision_id,
                record.run_id,
                record.reviewer_id,
                record.decision.value,
                record.rationale,
                _jsonb(record.model_recommendation.model_dump(mode="json")),
                record.decided_at,
            ),
        )
        resume = record.decision == ReviewDecision.REQUEST_MORE_INFO
        review_metadata = {
            "recommendation": record.model_recommendation.model_dump(mode="json"),
            "review": {
                "status": "more_info_requested" if resume else "decided",
                "round": record.review_round,
                "review_id": record.review_id,
                "decision_id": record.decision_id,
                "reviewer_id": record.reviewer_id,
                "decision": record.decision.value,
                "rationale": record.rationale,
                "metadata": {},
            },
        }
        next_status = (
            AgentRunStatus.RUNNING.value
            if resume
            else AgentRunStatus.AWAITING_REVIEW.value
        )
        self._execute(
            f"""
                UPDATE {self.settings.schema}.agent_runs
                SET status = %s,
                    metadata = COALESCE(metadata, '{{}}'::jsonb) || %s,
                    updated_at = now()
                WHERE run_id = %s
                  AND status NOT IN ('completed', 'failed', 'cancelled')
                  AND COALESCE(
                        NULLIF(metadata #>> '{{review,round}}', '')::integer,
                        0
                      ) <= %s
            """,
            (
                next_status,
                _jsonb(review_metadata),
                record.run_id,
                record.review_round,
            ),
        )
        self._execute(
            f"""
                UPDATE {self.settings.schema}.agent_workflow_metrics
                SET status = %s,
                    metadata = COALESCE(metadata, '{{}}'::jsonb) || %s,
                    updated_at = now()
                WHERE run_id = %s
                  AND status NOT IN ('completed', 'failed', 'cancelled')
                  AND COALESCE(
                        NULLIF(metadata #>> '{{review,round}}', '')::integer,
                        0
                      ) <= %s
            """,
            (
                next_status,
                _jsonb(review_metadata),
                record.run_id,
                record.review_round,
            ),
        )
        return write

    def record_event(self, event: LakebaseEvent) -> LakebaseWriteResult:
        sql = f"""
            INSERT INTO {self.settings.schema}.agent_events
              (event_id, run_id, event_type, payload, created_at)
            VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (event_id) DO UPDATE SET
              event_type = EXCLUDED.event_type,
              payload = EXCLUDED.payload
        """
        return self._execute(
            sql,
            (event.event_id, event.run_id, event.event_type, _jsonb(event.payload)),
        )

    def record_run_completed(self, record: RunCompletedRecord) -> LakebaseWriteResult:
        sql = f"""
            UPDATE {self.settings.schema}.agent_runs
            SET status = %s,
                final_response = %s,
                turns = %s,
                input_tokens = %s,
                output_tokens = %s,
                total_tokens = %s,
                updated_at = now(),
                completed_at = now()
            WHERE run_id = %s
        """
        return self._execute(
            sql,
            (
                record.status.value,
                record.final_response,
                record.turns,
                record.usage.input_tokens,
                record.usage.output_tokens,
                record.usage.total_tokens,
                record.run_id,
            ),
        )

    def record_workflow_metric_started(
        self,
        record: WorkflowMetricStartedRecord,
    ) -> LakebaseWriteResult:
        sql = f"""
            INSERT INTO {self.settings.schema}.agent_workflow_metrics AS current_metric
              (run_id, workflow_id, workflow_run_id, workflow_type, task_queue,
               status, started_at, metadata, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now(), now())
            ON CONFLICT (run_id) DO UPDATE SET
              workflow_id = EXCLUDED.workflow_id,
              workflow_run_id = EXCLUDED.workflow_run_id,
              workflow_type = EXCLUDED.workflow_type,
              task_queue = EXCLUDED.task_queue,
              status = EXCLUDED.status,
              started_at = EXCLUDED.started_at,
              metadata = EXCLUDED.metadata,
              updated_at = now()
            WHERE current_metric.status IN ('pending', 'running')
              AND COALESCE(current_metric.turn_count, 0) = 0
              AND current_metric.completed_at IS NULL
        """
        return self._execute(
            sql,
            (
                record.run_id,
                record.workflow_id,
                record.workflow_run_id,
                record.workflow_type,
                record.task_queue,
                AgentRunStatus.RUNNING.value,
                record.started_at,
                _jsonb(record.metadata),
            ),
        )

    def record_workflow_metric_completed(
        self,
        record: WorkflowMetricCompletedRecord,
    ) -> LakebaseWriteResult:
        sql = f"""
            INSERT INTO {self.settings.schema}.agent_workflow_metrics AS current_metric
              (run_id, workflow_id, workflow_run_id, workflow_type, status,
               started_at, completed_at, latency_ms, turn_count, metadata,
               created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
            ON CONFLICT (run_id) DO UPDATE SET
              workflow_id = EXCLUDED.workflow_id,
              workflow_run_id = EXCLUDED.workflow_run_id,
              workflow_type = EXCLUDED.workflow_type,
              status = EXCLUDED.status,
              started_at = EXCLUDED.started_at,
              completed_at = EXCLUDED.completed_at,
              latency_ms = EXCLUDED.latency_ms,
              turn_count = EXCLUDED.turn_count,
              metadata = current_metric.metadata || EXCLUDED.metadata,
              updated_at = now()
        """
        return self._execute(
            sql,
            (
                record.run_id,
                record.workflow_id,
                record.workflow_run_id,
                record.workflow_type,
                record.status.value,
                record.started_at,
                record.completed_at,
                record.latency_ms,
                record.turn_count,
                _jsonb(record.metadata),
            ),
        )

    def record_turn_metric(self, record: TurnMetricRecord) -> LakebaseWriteResult:
        sql = f"""
            INSERT INTO {self.settings.schema}.agent_turn_metrics
              (run_id, workflow_id, workflow_run_id, workflow_type, turn, status,
               started_at, completed_at, latency_ms, model_latency_ms,
               tool_latency_ms, tool_call_count, input_tokens, output_tokens,
               total_tokens, metadata, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    now(), now())
            ON CONFLICT (run_id, turn) DO UPDATE SET
              workflow_id = EXCLUDED.workflow_id,
              workflow_run_id = EXCLUDED.workflow_run_id,
              workflow_type = EXCLUDED.workflow_type,
              status = EXCLUDED.status,
              completed_at = EXCLUDED.completed_at,
              latency_ms = EXCLUDED.latency_ms,
              model_latency_ms = EXCLUDED.model_latency_ms,
              tool_latency_ms = EXCLUDED.tool_latency_ms,
              tool_call_count = EXCLUDED.tool_call_count,
              input_tokens = EXCLUDED.input_tokens,
              output_tokens = EXCLUDED.output_tokens,
              total_tokens = EXCLUDED.total_tokens,
              metadata = EXCLUDED.metadata,
              updated_at = now()
        """
        return self._execute(
            sql,
            (
                record.run_id,
                record.workflow_id,
                record.workflow_run_id,
                record.workflow_type,
                record.turn,
                record.status,
                record.started_at,
                record.completed_at,
                record.latency_ms,
                record.model_latency_ms,
                record.tool_latency_ms,
                record.tool_call_count,
                record.usage.input_tokens,
                record.usage.output_tokens,
                record.usage.total_tokens,
                _jsonb(record.metadata),
            ),
        )

    def record_activity_attempt_metric(
        self,
        record: ActivityAttemptMetricRecord,
    ) -> LakebaseWriteResult:
        sql = f"""
            INSERT INTO {self.settings.schema}.agent_activity_attempt_metrics
              (run_id, workflow_id, workflow_run_id, workflow_type, activity_id,
               activity_type, attempt, turn, status, started_at, completed_at,
               latency_ms, error_type, error_message, metadata, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    now())
            ON CONFLICT (run_id, activity_id, attempt) DO UPDATE SET
              workflow_id = EXCLUDED.workflow_id,
              workflow_run_id = EXCLUDED.workflow_run_id,
              workflow_type = EXCLUDED.workflow_type,
              activity_type = EXCLUDED.activity_type,
              turn = EXCLUDED.turn,
              status = EXCLUDED.status,
              completed_at = EXCLUDED.completed_at,
              latency_ms = EXCLUDED.latency_ms,
              error_type = EXCLUDED.error_type,
              error_message = EXCLUDED.error_message,
              metadata = EXCLUDED.metadata
        """
        return self._execute(
            sql,
            (
                record.run_id,
                record.workflow_id,
                record.workflow_run_id,
                record.workflow_type,
                record.activity_id,
                record.activity_type,
                record.attempt,
                record.turn,
                record.status,
                record.started_at,
                record.completed_at,
                record.latency_ms,
                record.error_type,
                record.error_message,
                _jsonb(record.metadata),
            ),
        )

    def load_recent_context(self, request: RecentContextRequest) -> list[str]:
        if self.settings.mode == "disabled":
            return []
        sql = f"""
            SELECT note
            FROM {self.settings.schema}.agent_notes
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT %s
        """
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (request.user_id, request.limit))
                return [row[0] for row in cur.fetchall()]

    def load_underwriting_policy(self, loan_purpose: str) -> UnderwritingPolicy | None:
        if self.settings.mode == "disabled":
            return None
        from psycopg.rows import dict_row

        sql = f"""
            SELECT loan_purpose,
                   min_credit_score,
                   max_dti,
                   decline_below_credit_score,
                   decline_above_dti
            FROM {self.settings.policy_schema}.underwriting_policy_limits
            WHERE loan_purpose IN (%s, 'default')
            ORDER BY CASE WHEN loan_purpose = %s THEN 0 ELSE 1 END
            LIMIT 1
        """
        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, (loan_purpose, loan_purpose))
                row = cur.fetchone()
        return UnderwritingPolicy.model_validate(row) if row else None

    def get_run_metrics(self) -> dict[str, Any]:
        counts_by_status = {status.value: 0 for status in AgentRunStatus}
        empty = {
            "total_runs": 0,
            "completed_runs": 0,
            "success_rate": 0.0,
            "p50_latency_ms": None,
            "p95_latency_ms": None,
            "counts_by_status": counts_by_status,
        }
        if self.settings.mode == "disabled":
            return empty

        from psycopg.rows import dict_row

        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    f"""
                        SELECT COUNT(*) AS total_runs,
                               COUNT(*) FILTER (
                                   WHERE status = %s
                               ) AS completed_runs,
                               COUNT(*) FILTER (
                                   WHERE status IN (%s, %s, %s)
                               ) AS terminal_runs,
                               percentile_cont(0.50) WITHIN GROUP (
                                   ORDER BY latency_ms
                               ) FILTER (
                                   WHERE latency_ms IS NOT NULL
                               ) AS p50_latency_ms,
                               percentile_cont(0.95) WITHIN GROUP (
                                   ORDER BY latency_ms
                               ) FILTER (
                                   WHERE latency_ms IS NOT NULL
                               ) AS p95_latency_ms
                        FROM {self.settings.schema}.agent_workflow_metrics
                    """,
                    (
                        AgentRunStatus.COMPLETED.value,
                        AgentRunStatus.COMPLETED.value,
                        AgentRunStatus.FAILED.value,
                        AgentRunStatus.CANCELLED.value,
                    ),
                )
                summary = cur.fetchone() or {}
                cur.execute(
                    f"""
                        SELECT status, COUNT(*) AS run_count
                        FROM {self.settings.schema}.agent_workflow_metrics
                        GROUP BY status
                    """
                )
                status_rows = list(cur.fetchall())

        for row in status_rows:
            counts_by_status[str(row["status"])] = int(row["run_count"])
        total_runs = int(summary.get("total_runs") or 0)
        completed_runs = int(summary.get("completed_runs") or 0)
        terminal_runs = int(summary.get("terminal_runs") or 0)
        p50 = summary.get("p50_latency_ms")
        p95 = summary.get("p95_latency_ms")
        return {
            "total_runs": total_runs,
            "completed_runs": completed_runs,
            "success_rate": completed_runs / terminal_runs if terminal_runs else 0.0,
            "p50_latency_ms": round(float(p50)) if p50 is not None else None,
            "p95_latency_ms": round(float(p95)) if p95 is not None else None,
            "counts_by_status": counts_by_status,
        }

    def get_retry_metrics(self) -> list[dict[str, Any]]:
        if self.settings.mode == "disabled":
            return []

        from psycopg.rows import dict_row

        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    f"""
                        SELECT activity_type,
                               SUM(attempt_count) AS attempt_count,
                               SUM(retry_count) AS retry_count,
                               MAX(last_attempt_completed_at) AS last_attempt_completed_at
                        FROM {self.settings.schema}.agent_activity_retry_counts
                        GROUP BY activity_type
                        ORDER BY retry_count DESC, attempt_count DESC, activity_type
                    """
                )
                rows = list(cur.fetchall())
        return [
            {
                "activity_type": str(row["activity_type"]),
                "attempt_count": int(row["attempt_count"] or 0),
                "retry_count": int(row["retry_count"] or 0),
                "last_attempt_completed_at": row["last_attempt_completed_at"],
            }
            for row in rows
        ]

    def get_cost_metrics(self, hours: int = 24) -> list[dict[str, Any]]:
        if self.settings.mode == "disabled":
            return []

        from psycopg.rows import dict_row

        lookback_hours = max(1, min(int(hours), 168))
        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    f"""
                        SELECT date_trunc('hour', started_at) AS bucket_start,
                               SUM(input_tokens) AS input_tokens,
                               SUM(output_tokens) AS output_tokens,
                               SUM(total_tokens) AS total_tokens,
                               COUNT(*) AS turn_count
                        FROM {self.settings.schema}.agent_turn_metrics
                        WHERE started_at >= now() - make_interval(hours => %s)
                        GROUP BY bucket_start
                        ORDER BY bucket_start
                    """,
                    (lookback_hours,),
                )
                rows = list(cur.fetchall())
        return [
            {
                "bucket_start": row["bucket_start"],
                "input_tokens": int(row["input_tokens"] or 0),
                "output_tokens": int(row["output_tokens"] or 0),
                "total_tokens": int(row["total_tokens"] or 0),
                "turn_count": int(row["turn_count"] or 0),
            }
            for row in rows
        ]

    def list_runs(self, limit: int = 25) -> list[WorkflowRunView]:
        if self.settings.mode == "disabled":
            return []
        from psycopg.rows import dict_row

        limit = max(1, min(limit, 100))
        sql = f"""
            SELECT run_id,
                   workflow_id,
                   user_id,
                   user_request,
                   status,
                   final_response,
                   metadata,
                   COALESCE(turns, 0) AS turns,
                   COALESCE(input_tokens, 0) AS input_tokens,
                   COALESCE(output_tokens, 0) AS output_tokens,
                   COALESCE(total_tokens, 0) AS total_tokens,
                   created_at,
                   updated_at
            FROM {self.settings.schema}.agent_runs
            ORDER BY updated_at DESC
            LIMIT %s
        """
        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, (limit,))
                run_rows = list(cur.fetchall())
                return [self._run_view(cur, row) for row in run_rows]

    def get_run(self, run_id: str) -> WorkflowRunView | None:
        if self.settings.mode == "disabled":
            return None
        from psycopg.rows import dict_row

        sql = f"""
            SELECT run_id,
                   workflow_id,
                   user_id,
                   user_request,
                   status,
                   final_response,
                   metadata,
                   COALESCE(turns, 0) AS turns,
                   COALESCE(input_tokens, 0) AS input_tokens,
                   COALESCE(output_tokens, 0) AS output_tokens,
                   COALESCE(total_tokens, 0) AS total_tokens,
                   created_at,
                   updated_at
            FROM {self.settings.schema}.agent_runs
            WHERE run_id = %s
        """
        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, (run_id,))
                row = cur.fetchone()
                return self._run_view(cur, row) if row else None

    def _execute(self, sql: str, params: tuple[Any, ...]) -> LakebaseWriteResult:
        if self.settings.mode == "disabled":
            return LakebaseWriteResult(
                written=False,
                skipped=True,
                message="Lakebase writes skipped because LAKEBASE_CONNECTION_MODE=disabled",
            )
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row_count = max(cur.rowcount, 0)
            conn.commit()
            return LakebaseWriteResult(written=True, row_count=row_count)

    def _run_view(self, cur: Any, row: dict[str, Any]) -> WorkflowRunView:
        metadata = _dict(row.get("metadata"))
        recommendation = None
        raw_recommendation = metadata.get("recommendation")
        if isinstance(raw_recommendation, dict):
            try:
                recommendation = LoanRecommendation.model_validate(raw_recommendation)
            except ValueError:
                pass
        review_decision = None
        raw_review = metadata.get("review")
        if isinstance(raw_review, dict) and raw_review.get("decision"):
            try:
                review_decision = ReviewDecisionPayload.model_validate(raw_review)
            except ValueError:
                pass
        return WorkflowRunView(
            run_id=row["run_id"],
            workflow_id=row["workflow_id"],
            user_id=row["user_id"],
            user_request=row["user_request"],
            status=AgentRunStatus(row["status"]),
            final_response=row["final_response"],
            metadata=metadata,
            recommendation=recommendation,
            review_decision=review_decision,
            turns=row["turns"],
            tool_results=self._tool_results_for_run(cur, row["run_id"]),
            usage=TokenUsage(
                input_tokens=row["input_tokens"],
                output_tokens=row["output_tokens"],
                total_tokens=row["total_tokens"],
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            messages=self._messages_for_run(cur, row["run_id"]),
        )

    def _messages_for_run(self, cur: Any, run_id: str) -> list[AgentMessage]:
        cur.execute(
            f"""
                SELECT role, name, content, metadata
                FROM {self.settings.schema}.agent_messages
                WHERE run_id = %s
                ORDER BY turn, created_at, id
            """,
            (run_id,),
        )
        return [
            AgentMessage(
                role=row["role"],
                name=row["name"],
                content=row["content"],
                metadata=_dict(row["metadata"]),
            )
            for row in cur.fetchall()
        ]

    def _tool_results_for_run(self, cur: Any, run_id: str) -> list[ToolResult]:
        cur.execute(
            f"""
                SELECT tool_call_id, tool_name, status, result, error
                FROM {self.settings.schema}.agent_tool_calls
                WHERE run_id = %s
                ORDER BY turn, completed_at NULLS LAST, requested_at, tool_call_id
            """,
            (run_id,),
        )
        results: list[ToolResult] = []
        for row in cur.fetchall():
            try:
                tool_name = ToolName(row["tool_name"])
            except ValueError:
                # Older demos shared this schema with different tool catalogs.
                # Keep those runs readable without pretending the legacy result
                # is part of the current underwriting contract.
                continue
            raw_result = _dict(row["result"])
            if raw_result:
                try:
                    results.append(ToolResult.model_validate(raw_result))
                    continue
                except ValueError:
                    pass
            if row["status"] not in {"succeeded", "failed"} and not row["error"]:
                continue
            results.append(
                ToolResult(
                    tool_call_id=row["tool_call_id"],
                    name=tool_name,
                    success=row["status"] == "succeeded",
                    content=row["error"] or f"{row['tool_name']} {row['status']}",
                    data=raw_result,
                    error=row["error"],
                )
            )
        return results

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        pool = self._get_pool()
        with pool.connection() as conn:
            yield conn

    def _get_pool(self) -> Any:
        with self._lock:
            now = time.monotonic()
            if self._pool is not None and now < self._pool_refresh_at:
                return self._pool
            if self._pool is not None:
                self._pool.close()

            from psycopg_pool import ConnectionPool

            self._pool = ConnectionPool(
                conninfo=self._conninfo(),
                min_size=1,
                max_size=self.settings.pool_size + self.settings.max_overflow,
                timeout=30,
                open=True,
            )
            self._pool_refresh_at = now + self.settings.token_refresh_seconds
            return self._pool

    def _conninfo(self) -> str:
        if self.settings.mode == "url":
            url = os.getenv("LAKEBASE_PG_URL")
            if not url:
                raise RuntimeError("LAKEBASE_PG_URL is required when LAKEBASE_CONNECTION_MODE=url")
            if "sslmode=require" not in url:
                raise RuntimeError("Lakebase connections must include sslmode=require")
            return url

        if self.settings.mode == "endpoint":
            from databricks.sdk import WorkspaceClient
            from psycopg.conninfo import make_conninfo

            endpoint = _required_env("LAKEBASE_ENDPOINT")
            workspace = WorkspaceClient()
            host = _nested_value(
                workspace.postgres.get_endpoint(name=endpoint),
                "status",
                "hosts",
                "host",
            )
            if not host:
                raise RuntimeError("Lakebase endpoint response did not include status.hosts.host")

            username = (
                os.getenv("LAKEBASE_USER")
                or os.getenv("PGUSER")
                or os.getenv("DATABRICKS_CLIENT_ID")
                or _nested_value(
                    workspace.current_user.me(),
                    "user_name",
                )
            )
            if not username:
                raise RuntimeError("Unable to determine Databricks current user; set LAKEBASE_USER")

            token = workspace.postgres.generate_database_credential(endpoint=endpoint).token
            return make_conninfo(
                "",
                host=str(host),
                port=os.getenv("LAKEBASE_PORT", os.getenv("PGPORT", "5432")),
                dbname=self.settings.database,
                user=str(username),
                password=token,
                sslmode=os.getenv("PGSSLMODE", "require"),
            )

        raise RuntimeError(
            "LAKEBASE_CONNECTION_MODE must be disabled, url, or endpoint"
        )


_repository: LakebaseRepository | None = None
_repository_lock = threading.Lock()


def get_repository() -> LakebaseRepository:
    global _repository
    with _repository_lock:
        if _repository is None:
            _repository = LakebaseRepository()
        return _repository


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required for the selected Lakebase connection mode")
    return value


def _nested_value(value: Any, *path: str) -> Any:
    current = value
    for part in path:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
        if current is None:
            return None
    return current


def _jsonb(value: Any) -> Any:
    if os.getenv("LAKEBASE_CONNECTION_MODE", "disabled").strip().lower() == "disabled":
        return value
    from psycopg.types.json import Jsonb

    return Jsonb(value)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}

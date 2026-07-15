# AgentWorkflow Lakebase Architecture Specification

## Objective

Build a Temporal-native loan-underwriting agent where the workflow models the agentic loop and durable human-review gate, while Databricks Lakebase stores operational state for UI polling, audit, notifications, and downstream Unity Catalog analytics.

## Selected Agent Functionality

The reference agent underwrites a personal-loan request. It can:

- Load canned credit and income-verification signals for an applicant.
- Calculate debt-to-income for the requested amount.
- Evaluate credit and DTI against a governed underwriting policy.
- Produce an approve, decline, or refer recommendation and durably await a human decision.
- Save short notes for future runs.

The external data is intentionally fixture-backed. The architectural proof points are Temporal durable orchestration, Lakebase operational persistence, human-in-the-loop review, and the governed Unity Catalog round trip.

## Scope

- Temporal workflow for the agent loop.
- Activities for model calls, tool execution, Lakebase reads, and Lakebase writes.
- UI signaling into Temporal and UI polling from Lakebase.
- Lakebase operational schema for runs, messages, tools, reviews, events, notes, and notifications.
- A durable review signal for approve, deny, and request-more-information actions.
- A Unity Catalog governed policy table synced into Lakebase for low-latency lookup.
- Lakebase Change Data Feed from Lakebase Postgres into Unity Catalog for analytics, GDPR, RLHF, and annotation workflows.

## Non-Goals

- Integrating a production credit bureau, income-verification provider, or loan-origination system.
- Replacing Temporal history with Lakebase. Temporal remains the durable execution source of truth.
- Running analytical joins or aggregations inside Lakebase. Curated analytical data should be prepared in Unity Catalog and synced into Lakebase when needed.

## Architecture Components

| Component | Responsibilities |
| --- | --- |
| User Interface | Starts workflows, sends signals, polls Lakebase or Data API for run state. |
| Notification Service | Reads unread notification rows from Lakebase and dispatches user notifications. |
| Audit View | Reads normalized audit rows from Lakebase and optionally Unity Catalog after sync. |
| Temporal Cloud or Dev Server | Stores workflow histories, schedules workflow and activity tasks, manages retries and timers. |
| AgentWorkflow Worker | Hosts the workflow definition and activities on a task queue. |
| Agent Workflow | Owns deterministic agent state, loop control, signal buffering, and final result. |
| Model Activity | Calls the configured model provider and returns a structured decision. |
| Tool Activity | Executes tools and returns structured tool results. |
| Lakebase Activities | Persist operational state and read recent context from Lakebase. |
| Lakebase | Serverless Postgres operational store for low-latency app reads and writes. |
| Unity Catalog | Analytical destination for operational change history after Lakebase CDF. |

## Temporal Agent Loop

1. Workflow starts with `AgentRequest`.
2. Workflow records `agent.run_started` through a Lakebase activity.
3. Workflow loads recent notes from Lakebase through an activity.
4. Workflow calls `call_model` with the current message list and tool schemas.
5. If the model returns tool calls, the workflow schedules `execute_tool` activities.
6. The workflow records each tool result and message through Lakebase activities.
7. When the model returns a structured recommendation, the workflow records it and waits in `AWAITING_REVIEW`.
8. `submit_review_decision` approves, denies, or adds a request-more-information message and resumes the loop.
9. The workflow records completion state and returns `AgentResult`.

Workflow code must remain deterministic. It must not call model APIs, query Lakebase, make HTTP requests, read files, generate non-deterministic IDs, or inspect wall-clock time except through Temporal-safe APIs.

## Signals And Queries

- `add_user_message(payload)` appends a user message to the running workflow.
- `submit_review_decision(payload)` supplies reviewer identity, decision, and rationale to a run in `AWAITING_REVIEW`.
- `request_cancel()` requests cooperative cancellation.
- `status()` returns a Temporal-side `AgentSnapshot`.

User-facing dashboards should poll Lakebase because it contains indexed, normalized operational state. Temporal queries should be used for control-plane inspection and debugging.

## Lakebase Operational Model

Lakebase stores:

- `agent_runs`: run status, workflow ID, user ID, request, token usage, final response.
- `agent_messages`: user, assistant, tool, and system messages.
- `agent_tool_calls`: structured tool arguments, outputs, and errors.
- `agent_events`: audit events.
- `agent_notifications`: unread and read notification records.
- `agent_notes`: reusable user context.
- `agent_review_decisions`: retry-safe pending and completed human review records with the model recommendation snapshot.

All Lakebase connections must use `sslmode=require`. The EKS web/API Deployment and Temporal Worker Controller-managed worker pods use off-platform Databricks OAuth service principals with `LAKEBASE_CONNECTION_MODE=endpoint`; the SDK resolves `LAKEBASE_ENDPOINT` and generates short-lived database credentials. Local development may use `LAKEBASE_PG_URL`, but only with non-committed secrets.

## Databricks And Unity Catalog Integration

Lakebase is the operational store. Unity Catalog is the analytical and governance layer.

Lakebase Change Data Feed captures `agent_ops` changes in `agentworkflow_lakehouse.agent_ops` as append-only Delta `lb_<table>_history` event tables. It is a Public Preview feature enabled in the Lakebase UI at the schema level. Every operational base table sets `REPLICA IDENTITY FULL` to satisfy CDC requirements.

The opposite direction is concrete: `agentworkflow_lakehouse.governed.underwriting_policy_limits` is a CDF-enabled Delta source, and an Autoscaling synced table materializes it as read-only `agent_policy.underwriting_policy_limits` in Lakebase. The policy activity reads that OLTP copy and uses fixtures only when Lakebase is disabled or unavailable.

## Data Contracts

### AgentRequest

- `run_id`: caller-provided unique run ID.
- `user_id`: user identity.
- `user_request`: initial natural-language request.
- `applicant_id`: applicant fixture or upstream identifier.
- `loan_amount`: positive requested principal.
- `loan_purpose`: normalized policy lookup key.
- `model`: model identifier used by the model activity.
- `max_turns`: bounded loop count.
- `metadata`: additional caller context.

### ModelDecision

- `assistant_message`: assistant text for this turn.
- `final`: true when the workflow should complete.
- `tool_calls`: requested tool calls.
- `usage`: token usage for cost tracking.
- `metadata`: provider-specific details.

### ToolResult

- `tool_call_id`: model-generated or scaffold-generated tool call ID.
- `name`: tool name.
- `success`: whether execution succeeded.
- `content`: human-readable tool result.
- `data`: structured result payload.
- `error`: optional failure message.

### LakebaseRecord

- `run_id`: workflow run identifier.
- `workflow_id`: Temporal workflow ID.
- `turn`: agent loop turn.
- `status`: run or tool status.
- `payload`: JSONB structured data.
- `created_at`: database timestamp.

## Retry And Timeout Policy

- Model activity: 30 second start-to-close, 3 minute schedule-to-close, up to 4 attempts.
- Tool activity: 60 second start-to-close, 2 minute schedule-to-close, up to 3 attempts.
- Lakebase activities: 15 second start-to-close, up to 5 attempts.
- Client-level model retries are disabled. Temporal owns retry behavior.
- Non-retryable configuration or authentication errors should fail fast.

## Observability

- Temporal provides workflow status, history, retry visibility, and activity failure details.
- Lakebase provides app-facing operational views and audit tables.
- Token usage is returned by the model activity and persisted on run completion.
- Tool results and errors are normalized for analysis.
- Unity Catalog receives Lakebase operational change history after Lakebase CDF is enabled.

## Open Questions

- Which identity provider and user IDs should be stored in `user_id`?
- What peak workflow start rate and signal rate should the task queue support?
- What is the maximum expected workflow duration?
- What payload size limits should be enforced before data is written to Lakebase?
- Should the UI poll Postgres directly, use the Lakebase Data API, or use an API service over Lakebase?
- What is the workflow versioning plan once long-running executions exist?
- Are Temporal Search Attributes required beyond visibility and debugging?
- Should high-volume notification dispatch be a separate workflow or only a Lakebase-driven service?
- Which reviewer identity and authorization claims must be retained for compliance?
- What policy-change propagation SLA is required between Unity Catalog and Lakebase?

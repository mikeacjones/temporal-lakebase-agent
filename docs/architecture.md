# Architecture

## Core Decision

**Temporal protects progress; Lakebase closes the governed data loop.**

The loan-underwriting loop is modeled as a Temporal workflow, not as a long-running process hidden inside an activity. The workflow owns the durable control flow:

1. Accept the applicant, requested amount, and loan purpose.
2. Load recent operational context from Lakebase.
3. Call a model activity for the next agent decision.
4. Run credit, income, debt-to-income, and policy tools as separate activities.
5. Persist UI state, audit events, and operational telemetry to separate Lakebase tables.
6. When the model produces a recommendation, durably wait for a human review signal.
7. Complete, deny, or request more information without losing the prior tool trail.

All non-deterministic work is outside the workflow:

- Model APIs run in `call_model`.
- Tool execution runs in `execute_tool`.
- Lakebase reads and writes run in `activities.py`.
- Temporal signals only append deterministic workflow state and are persisted through a Lakebase message activity.

## Runtime Components

| Component | Responsibility |
| --- | --- |
| tmprl-demo.cloud web/API deployment | Serves the React UI and FastAPI endpoints, starts workflows, sends signals, and polls Lakebase for current run state. |
| Temporal Cloud or Dev Server | Stores durable event history and schedules workflow/activity tasks. |
| Temporal Worker Controller | Reconciles the registry-managed `WorkerDeployment`, creating one Kubernetes Deployment per active Worker Deployment Version. The worker hosts `LakebaseAgentWorkflow`, registered as `LoanUnderwritingWorkflow`, plus its activities. |
| Model Activity | Calls the configured model provider with client retries disabled. |
| Tool Activity | Executes credit, income, debt-to-income, policy, and note tools with Temporal-managed retries. |
| Lakebase Activities | Persist UI state, audit events, notes, notifications, and separate operational metrics. |
| Databricks Lakebase | Serverless Postgres operational store for low-latency app reads. |
| Unity Catalog | Analytical destination for synced operational records and downstream GDPR, RLHF, and annotation workflows. |

### Runtime boundary and scaling

Databricks is the Lakebase and Unity Catalog data plane; it is not the worker or web runtime. tmprl-demo.cloud deploys two runtime parts from the repository's Dockerfiles:

1. The web/API Deployment handles browser and API traffic. Adding its replicas increases request capacity without adding Temporal pollers.
2. A Temporal Worker Controller `WorkerDeployment` owns the standalone worker. The controller creates a Kubernetes Deployment for each active Temporal Worker Deployment Version without moving pinned workflows off their compatible version.

Temporal Cloud is the production durable control plane between them. The Worker Controller's `Connection` references the Temporal Cloud API-key secret and injects `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `TEMPORAL_DEPLOYMENT_NAME`, and `TEMPORAL_WORKER_BUILD_ID`; the worker pod template must not set those four values manually. Lakebase remains the shared operational database, while Unity Catalog owns the governed policy source and Delta audit destination. This deployment boundary is independent of the Lakebase-versus-Neon argument: Lakebase is selected for its Databricks-native governed data paths, not because it hosts the application process.

### Cloud crash-recovery experience

The registry's crashable-workspace feature owns the hosted recovery demonstration. It clones the web and worker runtime into a temporary namespace, overrides `TEMPORAL_TASK_QUEUE` with a per-workspace queue, and force-deletes the workspace pods when the presenter selects **Crash Workers**. Kubernetes and Worker Controller recreate the processes, and the replacement worker resumes from Temporal history on the same isolated queue.

No application component shells into another container or receives Kubernetes API credentials. The web/API already starts workflows on `TEMPORAL_TASK_QUEUE`, and the worker polls that same environment-driven value, so the registry can isolate each temporary experience without an application-side control endpoint.

### Off-platform Databricks authentication

Each runtime component authenticates to Databricks with an OAuth machine-to-machine service principal supplied by the registry's project-scoped AWS Secrets Manager integration. In `endpoint` mode, the Databricks SDK uses `DATABRICKS_HOST`, `DATABRICKS_AUTH_TYPE=oauth-m2m`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET`, and `LAKEBASE_ENDPOINT` to resolve the Postgres host and mint short-lived database credentials. `LAKEBASE_USER` can override the Postgres role; otherwise the runtime uses the service-principal client ID. Connections use `sslmode=require`, pooling, and pool recycling before OAuth token expiry.

Use distinct worker and API service principals where operationally practical. Apply least privilege at both layers:

- Databricks/Lakebase: allow the runtime identity to access the intended project, branch, endpoint, and database credential flow.
- Postgres worker role: connect to `databricks_postgres`, use and read `agent_policy`, and use/read/write the required `agent_ops` tables and sequences.
- Postgres API role: connect to `databricks_postgres` and read only the UI-serving and operational-metric objects it exposes.
- Setup/operator identity: separately hold the Unity Catalog and Lakebase permissions needed to manage source/destination schemas, the synced table, and CDF configuration.

Do not put client secrets in container images or source control. tmprl-demo.cloud mirrors the declared project secret into the runtime namespace and rotates it without rebuilding the application image.

## Workflow Signals And Queries

- `add_user_message(payload)` lets the UI add a message to a running execution.
- `submit_review_decision(payload)` records an approve, deny, or request-more-information decision and resumes a run waiting in `AWAITING_REVIEW`.
- `request_cancel()` requests cooperative cancellation.
- `status()` returns the current workflow snapshot for Temporal-side inspection.

The UI should prefer Lakebase for user-facing polling because Lakebase contains the normalized run view. Temporal queries are useful for debugging and control-plane checks.

## Lakebase Data Flow

The worker writes UI-serving state into:

- `agent_runs`: one row per workflow execution.
- `agent_messages`: user, assistant, tool, and system messages.
- `agent_tool_calls`: normalized tool results and failures.
- `agent_notifications`: user-visible notification records.
- `agent_notes`: reusable memory/context for future runs.
- `agent_review_decisions`: deterministic, retry-safe review records with a JSON snapshot of the model recommendation. Reviewer fields are nullable while a recommendation is pending.

The React UI API reads `agent_runs`, `agent_messages`, and `agent_tool_calls` to render the conversation; the live pending recommendation is projected in `agent_runs.metadata`. `agent_review_decisions` is the durable audit row that is inserted pending and completed by reviewer action. Notification and note features read their dedicated tables.

Operational data is kept out of the UI contract:

- `agent_events`: audit events such as run started and run completed.
- `agent_workflow_metrics`: workflow type, Temporal workflow run ID, task queue, status, start/end timestamps, end-to-end latency, and turn count.
- `agent_turn_metrics`: one row per agent turn with total turn latency, model latency, tool latency, tool count, and token usage for that turn.
- `agent_activity_attempt_metrics`: one row per Temporal activity attempt with `activity_type`, `activity_id`, attempt number, status, latency, optional turn, and error fields.
- `agent_activity_retry_counts`: view over activity attempts grouped by run, workflow type, and activity type.

This split keeps the user interface stable even as operational telemetry changes. It also makes retry counts explicit: the retry view computes `MAX(attempt) - 1` for each Temporal activity ID, then sums those retries by `activity_type` instead of inferring them from messages or tool rows.

## Unity Catalog Integration

The demo closes a governed data loop in both directions. These are two distinct Databricks features and deployment steps.

### Unity Catalog to Lakebase: policy serving

The governed source is the Delta table:

`agentworkflow_lakehouse.governed.underwriting_policy_limits`

It has one row per `loan_purpose` and the columns `min_credit_score`, `max_dti`, `decline_below_credit_score`, and `decline_above_dti`. DTI values are ratios (`0.43` is displayed as 43%). Change Data Feed is enabled so a Continuous or Triggered synced table can apply incremental changes.

The Lakebase database is registered in Unity Catalog as `temporal_agentworkflow_lakebase`. The Autoscaling synced-table resource is:

`temporal_agentworkflow_lakebase.agent_policy.underwriting_policy_limits`

That resource materializes the read-only Postgres table `agent_policy.underwriting_policy_limits` in `databricks_postgres`. Keeping governed inputs in `agent_policy` prevents the schema-level Change Data Feed on `agent_ops` from exporting its own sync-managed input. The `policy_lookup` activity reads this local Postgres copy, so the agent gets OLTP latency while policy ownership remains in Unity Catalog. The table must only be queried or indexed in Postgres; `INSERT`, `UPDATE`, and `DELETE` corrupt the managed sync pipeline. For local/offline development, the activity falls back to its matching fixture policy.

Autoscaling synced tables are created with `databricks postgres create-synced-table`. The similarly named DAB `synced_database_tables` resource targets the Provisioned API and must not be used for this project.

### Lakebase to Unity Catalog: decision audit

The worker writes each run and review to `agent_ops.agent_runs` and `agent_ops.agent_review_decisions`, alongside the transcript, tool calls, and operational metrics. Lakebase Change Data Feed is enabled for the `agent_ops` schema from the Lakebase branch UI and appends row-level change events to:

- `agentworkflow_lakehouse.agent_ops.lb_agent_runs_history`
- `agentworkflow_lakehouse.agent_ops.lb_agent_review_decisions_history`
- corresponding `lb_<table_name>_history` tables for the other `agent_ops` base tables

This direction is currently Public Preview. It requires Postgres 17, the `databricks_postgres` database, a destination catalog backed by a non-default managed storage location, and `REPLICA IDENTITY FULL` on every source base table. `sql/001_agent_ops_schema.sql` applies that replica identity to the complete application and metrics schema, including `agent_review_decisions`. The feed records `_pg_change_type`, `_pg_lsn`, transaction, and timestamp metadata; updates produce preimage and postimage events. It is a schema-level, UI-only enablement.

Together, the policy row moves from governed Delta to low-latency Lakebase, drives a durable recommendation and human decision, and the resulting audit record moves from Lakebase back to Unity Catalog for analytics and compliance review.

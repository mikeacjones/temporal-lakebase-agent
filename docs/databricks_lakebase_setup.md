# Databricks Lakebase Setup

Use a Databricks profile selected by the operator. Do not guess or auto-select a profile.

## Discover CLI Commands

```bash
databricks postgres -h
databricks postgres create-project -h
databricks postgres create-catalog -h
```

## Create A Lakebase Project

```bash
databricks postgres create-project <PROJECT_ID> \
  --json '{"spec": {"display_name": "Temporal Lakebase AgentWorkflow"}}' \
  --profile <PROFILE>
```

Verify the production branch and primary endpoint:

```bash
databricks postgres list-branches projects/<PROJECT_ID> --profile <PROFILE>
databricks postgres list-endpoints projects/<PROJECT_ID>/branches/production --profile <PROFILE>
```

## Initialize The Operational Schema

Get the endpoint host and generate a database credential:

```bash
databricks postgres get-endpoint \
  projects/<PROJECT_ID>/branches/production/endpoints/primary \
  --profile <PROFILE>

databricks postgres generate-database-credential \
  projects/<PROJECT_ID>/branches/production/endpoints/primary \
  --profile <PROFILE>
```

Run `sql/001_agent_ops_schema.sql` with `sslmode=require` against the `databricks_postgres` database.

The schema intentionally separates:

- UI state: `agent_runs`, `agent_messages`, `agent_tool_calls`, `agent_notifications`, and `agent_notes`.
- Audit events: `agent_events`.
- Operational metrics: `agent_workflow_metrics`, `agent_turn_metrics`, and `agent_activity_attempt_metrics`.
- Retry summaries: `agent_activity_retry_counts`, grouped by workflow and activity type.

## Configure The tmprl-demo.cloud Runtime

The production web/API and versioned Temporal worker run in the tmprl-demo.cloud Kubernetes environment. The central registry builds the repository's two Dockerfiles, supplies the standard Temporal environment variables, and reconciles the worker through Temporal Worker Controller. Databricks supplies the Lakebase/Unity Catalog data plane; it does not host either application process.

Configure each workload with a Databricks OAuth machine-to-machine service principal and Lakebase endpoint mode:

```bash
DATABRICKS_HOST=https://<WORKSPACE_HOST>
DATABRICKS_AUTH_TYPE=oauth-m2m
DATABRICKS_CLIENT_ID=<SERVICE_PRINCIPAL_CLIENT_ID>
DATABRICKS_CLIENT_SECRET=<SERVICE_PRINCIPAL_CLIENT_SECRET>
LAKEBASE_CONNECTION_MODE=endpoint
LAKEBASE_ENDPOINT=projects/temporal-lakebase-agentworkflow/branches/production/endpoints/primary
LAKEBASE_DATABASE=databricks_postgres
# Optional; defaults to DATABRICKS_CLIENT_ID.
LAKEBASE_USER=<SERVICE_PRINCIPAL_CLIENT_ID>
LAKEBASE_SCHEMA=agent_ops
LAKEBASE_POLICY_SCHEMA=agent_policy
```

The Databricks SDK uses these credentials to resolve the endpoint and generate short-lived Lakebase database credentials. Store the four required values in `tmprl-dem-cld/lakebase-agent/lakebase`; the registry mirrors that JSON secret into both components. Keep `sslmode=require` and recycle pooled connections before token expiry. Separate worker and API identities are a later least-privilege hardening option; the registry manifest currently uses one demo identity to keep onboarding concise. The operator that manages Unity Catalog, synced tables, and CDF should remain a separate privileged identity.

For local development, the same endpoint mode may use an explicitly selected interactive Databricks SDK/CLI credential source. As a manual local fallback, use a full Postgres URL:

```bash
LAKEBASE_CONNECTION_MODE=endpoint
LAKEBASE_ENDPOINT=projects/temporal-lakebase-agentworkflow/branches/production/endpoints/primary
LAKEBASE_DATABASE=databricks_postgres
# Or, for local development only:
LAKEBASE_CONNECTION_MODE=url
LAKEBASE_PG_URL="postgresql://user:password@host:5432/databricks_postgres?sslmode=require"
```

## Lakebase Change Data Feed To Unity Catalog

Lakebase Change Data Feed from Lakebase to Unity Catalog is a Public Preview feature enabled in the Lakebase UI. It is a schema-level setting and appends row-level Postgres change events to Delta `lb_<table>_history` tables.

Before configuring the branch, a workspace administrator must enable **Lakebase Change Data Feed** from the workspace **Previews** page. Empty Postgres source tables are skipped until they contain at least one row, so their Delta history tables are not expected to appear immediately.

Before enabling sync, verify:

- The Lakebase project uses Postgres 17.
- Tables are in `databricks_postgres`.
- `REPLICA IDENTITY FULL` is set on the operational tables.
- The operator has `CAN MANAGE` on the Lakebase project.
- The destination Unity Catalog location supports table creation and uses a non-default managed storage location.

## Lakebase Data API

The UI can read user-facing state through Postgres or the Lakebase Data API. If using the Data API:

1. Enable Data API in the Lakebase project UI.
2. Grant the calling Databricks identities access to the exposed schema and tables.
3. Add row-level security before exposing multi-tenant data.
4. Keep result sets paginated and indexed by `run_id`, `user_id`, `status`, and `created_at`.
5. Do not expose operational metric tables directly to end users unless the caller is authorized to see workflow and activity diagnostics.

## Sizing Notes

- Start with autoscaling 0.5-4 CU for development and demos.
- Increase minimum CU when UI polling or sync workloads need warm cache.
- Keep transactions short because workflow activities will retry on transient failures.
- Size connection limits with synced tables in mind; each Lakebase synced table can use up to 16 connections.

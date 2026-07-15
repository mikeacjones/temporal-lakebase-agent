# Production-Like Setup

This setup uses:

- New Lakebase project: `temporal-lakebase-agentworkflow`
- Lakebase branch: `production`
- Lakebase endpoint: `primary`
- Lakebase database: `databricks_postgres`
- Operational schema: `agent_ops`
- Synced policy schema: `agent_policy`
- Regular Unity Catalog catalog: `agentworkflow_lakehouse`
- Lakebase database registration in UC: `temporal_agentworkflow_lakebase`
- Runtime: an EKS web/API Deployment plus a standalone worker managed by Temporal Worker Controller
- Worker and API Lakebase mode: `endpoint`

## Required Operator Step: Databricks OAuth

Use interactive OAuth only for the human/operator CLI steps in this guide. It is not the production runtime credential. Authenticate with the explicitly selected profile:

```bash
databricks auth login \
  --host https://dbc-33a81e40-df8b.cloud.databricks.com \
  --profile dbc-33a81e40
```

Then verify:

```bash
databricks auth profiles
```

## Create Lakebase Project

Discover the exact command shape first:

```bash
databricks postgres -h --profile dbc-33a81e40
databricks postgres create-project -h --profile dbc-33a81e40
```

Create the project:

```bash
databricks postgres create-project temporal-lakebase-agentworkflow \
  --json '{"spec": {"display_name": "Temporal Lakebase AgentWorkflow"}}' \
  --profile dbc-33a81e40
```

Verify:

```bash
databricks postgres list-branches \
  projects/temporal-lakebase-agentworkflow \
  --profile dbc-33a81e40

databricks postgres list-endpoints \
  projects/temporal-lakebase-agentworkflow/branches/production \
  --profile dbc-33a81e40
```

## Create Destination Unity Catalog Catalog

Discover the exact command shape:

```bash
databricks catalogs create -h --profile dbc-33a81e40
databricks schemas create -h --profile dbc-33a81e40
```

The current environment is provisioned with dedicated AWS/Unity Catalog storage:

- S3 bucket: `temporal-lakebase-agentworkflow-429214323166-us-east-2`
- IAM role: `arn:aws:iam::429214323166:role/DatabricksUnityCatalogTemporalLakebase`
- Storage credential: `temporal_lakebase_agentworkflow_storage`
- External location: `temporal_lakebase_agentworkflow_managed`
- Catalog storage root: `s3://temporal-lakebase-agentworkflow-429214323166-us-east-2/unity-catalog/agentworkflow_lakehouse`

For a replacement environment, create the regular catalog and its audit, governed-input, and pipeline-storage schemas with that non-default managed storage pattern. Lakebase Change Data Feed does not support a destination catalog that uses Default Storage.

```bash
databricks catalogs create agentworkflow_lakehouse \
  --storage-root s3://temporal-lakebase-agentworkflow-429214323166-us-east-2/unity-catalog/agentworkflow_lakehouse \
  --comment "Lakebase CDF destination for Temporal Lakebase AgentWorkflow operational data" \
  --profile dbc-33a81e40

databricks schemas create agent_ops agentworkflow_lakehouse \
  --comment "Lakebase CDF change history for AgentWorkflow operational tables" \
  --profile dbc-33a81e40
databricks schemas create governed agentworkflow_lakehouse \
  --comment "Governed underwriting inputs served into Lakebase" \
  --profile dbc-33a81e40
databricks schemas create sync_metadata agentworkflow_lakehouse \
  --comment "Managed pipeline metadata for Lakebase synced tables" \
  --profile dbc-33a81e40
```

The earlier Default Storage blocker is resolved. Keep the dedicated storage credential, external location, and catalog storage root above; do not recreate this catalog on Default Storage because it would not be eligible as the Lakebase CDF destination.

## Initialize Lakebase Schema

Get endpoint details and generate a database credential:

```bash
databricks postgres get-endpoint \
  projects/temporal-lakebase-agentworkflow/branches/production/endpoints/primary \
  --profile dbc-33a81e40

databricks postgres generate-database-credential \
  projects/temporal-lakebase-agentworkflow/branches/production/endpoints/primary \
  --profile dbc-33a81e40
```

If `psql` is installed, connect with `sslmode=require` and run:

```bash
psql "host=<HOST> user=<USER> dbname=databricks_postgres sslmode=require" \
  -f sql/001_agent_ops_schema.sql
```

If `psql` is not installed, use the bundled Python initializer:

```bash
uv run --extra lakebase python scripts/init_lakebase_schema.py \
  --profile dbc-33a81e40 \
  --project-id temporal-lakebase-agentworkflow \
  --user sam.ingbar@temporal.io
```

The schema sets `REPLICA IDENTITY FULL` on every operational base table, including `agent_review_decisions`, so the schema is ready for Lakebase Change Data Feed. Verify it before enabling the feed:

```sql
SELECT
    n.nspname AS schema_name,
    c.relname AS table_name,
    CASE c.relreplident
        WHEN 'f' THEN 'full'
        WHEN 'd' THEN 'default'
        WHEN 'n' THEN 'nothing'
        WHEN 'i' THEN 'index'
    END AS replica_identity
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE c.relkind = 'r'
  AND n.nspname = 'agent_ops'
ORDER BY c.relname;
```

Every row must report `full`. The policy table is intentionally not in this schema: `agent_policy.underwriting_policy_limits` is managed by the opposite-direction synced-table pipeline and is read-only in Postgres.

## Sync Governed Policy From Unity Catalog Into Lakebase

Run the idempotent setup script with an explicitly chosen Databricks profile:

```bash
scripts/setup_underwriting_policy_sync.sh \
  --profile dbc-33a81e40 \
  --project-id temporal-lakebase-agentworkflow
```

The script performs the complete Autoscaling workflow:

1. Creates and seeds `agentworkflow_lakehouse.governed.underwriting_policy_limits` as a Delta table.
2. Enables Change Data Feed for incremental sync.
3. Registers `databricks_postgres` in Unity Catalog as `temporal_agentworkflow_lakebase` if needed.
4. Creates a Continuous synced-table resource named `temporal_agentworkflow_lakebase.agent_policy.underwriting_policy_limits`.
5. Waits for the operation and prints its status.

Continuous mode is appropriate for the live policy-change demo. Account for up to 16 Lakebase connections consumed by this synced table when sizing the endpoint; use `--scheduling-policy TRIGGERED` when real-time propagation is unnecessary.

The materialized Postgres target is `agent_policy.underwriting_policy_limits`. It uses the following contract:

| Column | Meaning |
| --- | --- |
| `loan_purpose` | Primary key used to choose the applicable policy. |
| `min_credit_score` | Score required to recommend approval without referral. |
| `max_dti` | Maximum approval DTI ratio; `0.43` means 43%. |
| `decline_below_credit_score` | Score below which policy recommends decline. |
| `decline_above_dti` | DTI ratio above which policy recommends decline. |

Check the resource later with:

```bash
databricks postgres get-synced-table \
  "synced_tables/temporal_agentworkflow_lakebase.agent_policy.underwriting_policy_limits" \
  --profile dbc-33a81e40
```

After it is online, query `agent_policy.underwriting_policy_limits` through the Lakebase Postgres endpoint. Only `SELECT`, `CREATE INDEX`, and `DROP TABLE` are supported on a synced target; never issue `INSERT`, `UPDATE`, or `DELETE` against it. Each off-platform EKS service principal that reads policy also needs explicit Postgres access after its matching Lakebase role exists:

```sql
GRANT USAGE ON SCHEMA agent_policy TO "<DATABRICKS_SP_CLIENT_ID>";
GRANT SELECT ON TABLE agent_policy.underwriting_policy_limits TO "<DATABRICKS_SP_CLIENT_ID>";
```

Use the client ID of the Databricks OAuth service principal supplied to the relevant EKS workload. Do not derive it from a Databricks App because no application process is deployed there.

## Enable Lakebase Change Data Feed

Lakebase Change Data Feed from Lakebase to Unity Catalog is currently Public Preview and is enabled in the Lakebase UI:

1. Ask a workspace administrator to enable **Lakebase Change Data Feed** on the workspace **Previews** page.
2. Open `https://dbc-33a81e40-df8b.cloud.databricks.com/lakebase/projects`.
3. Open project `temporal-lakebase-agentworkflow`.
4. Open branch `production`.
5. Open the Change Data Feed tab.
6. Start the feed for schema `agent_ops`.
7. Select destination catalog `agentworkflow_lakehouse` and schema `agent_ops`.
8. After a demo has written run and review rows, verify `lb_agent_runs_history` and `lb_agent_review_decisions_history` appear. CDF does not create a history table for an empty source table, so the remaining `lb_<table_name>_history` tables appear only after their source tables contain rows.

Lakebase CDF requires an Autoscaling project on Postgres 17, operates only on tables in `databricks_postgres`, and is enabled from the branch UI. Rows include `_pg_change_type`, `_pg_lsn`, transaction, and timestamp metadata; updates emit preimage and postimage events.

After a reviewed demo run, show the audit round trip directly from a SQL warehouse:

```bash
databricks experimental aitools tools query \
  --profile dbc-33a81e40 \
  "SELECT run_id, decision_id, reviewer_id, decision, rationale, model_recommendation,
          decided_at, _pg_change_type, _pg_lsn
   FROM agentworkflow_lakehouse.agent_ops.lb_agent_review_decisions_history
   WHERE run_id = '<RUN_ID>'
     AND decided_at IS NOT NULL
     AND _pg_change_type IN ('insert', 'update_postimage')
   ORDER BY _pg_lsn DESC
   LIMIT 20"
```

To demonstrate the other direction, update a threshold in the governed UC source (DTI remains a ratio), wait for the synced-table status to return online, then start another loan run and show that `policy_lookup` returns `policy_source=lakebase_synced_table` with the new threshold. Restore the demo value after the presentation.

## Runtime Environment

### Workflow-version transition

The loan implementation registers the runtime workflow type as `LoanUnderwritingWorkflow`. This intentionally isolates it from histories created by the earlier IP/weather `LakebaseAgentWorkflow` control flow. If that older demo has active executions, keep its previous worker build available until they finish or terminate them deliberately; do not point the loan worker at those legacy histories.

### EKS deployment boundary

Databricks supplies the Lakebase and Unity Catalog data plane only. Package the runtime as two independently scalable EKS parts:

- A web/API Kubernetes Deployment serving the React UI and FastAPI endpoints. Scale its replicas against HTTP demand.
- A standalone worker container reconciled by a Temporal Worker Controller `WorkerDeployment`. The controller creates one versioned Kubernetes Deployment per active Temporal Worker Deployment Version; a `WorkerResourceTemplate` can attach a separate HPA to each version.

Temporal Cloud is the production orchestration service. The Worker Controller `Connection` owns the worker's Temporal endpoint and API-key reference and injects `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `TEMPORAL_DEPLOYMENT_NAME`, and `TEMPORAL_WORKER_BUILD_ID`; do not set those values manually in the worker pod template. Both runtime parts connect to Lakebase, but they are not Databricks Apps. The currently deployed EKS release and verification evidence are recorded in [Setup Status](setup_status.md); keep this section as the reproducible architecture and permission contract rather than treating a past rollout as permanent configuration.

### Off-platform Databricks OAuth

Configure each EKS workload with a Databricks OAuth machine-to-machine service principal and Lakebase endpoint mode:

```bash
DATABRICKS_HOST=https://dbc-33a81e40-df8b.cloud.databricks.com
DATABRICKS_AUTH_TYPE=oauth-m2m
DATABRICKS_CLIENT_ID=<SERVICE_PRINCIPAL_CLIENT_ID>
DATABRICKS_CLIENT_SECRET=<SERVICE_PRINCIPAL_CLIENT_SECRET>
LAKEBASE_CONNECTION_MODE=endpoint
LAKEBASE_ENDPOINT=projects/temporal-lakebase-agentworkflow/branches/production/endpoints/primary
LAKEBASE_DATABASE=databricks_postgres
LAKEBASE_USER=<SERVICE_PRINCIPAL_CLIENT_ID>
LAKEBASE_SCHEMA=agent_ops
LAKEBASE_POLICY_SCHEMA=agent_policy
```

The Databricks SDK uses the service-principal identity and `LAKEBASE_ENDPOINT` to discover the host and generate short-lived OAuth database credentials. Maintain TLS with `sslmode=require`, refresh credentials before expiry, recycle pooled connections, and retry stale connections after scale-to-zero. A static `LAKEBASE_PG_URL` is a local-development fallback, not the EKS production pattern.

Store `DATABRICKS_CLIENT_SECRET`, `TEMPORAL_API_KEY`, and `OPENAI_API_KEY` in Kubernetes Secrets or an external secret manager. Keep non-secret endpoint, schema, and scaling values in normal deployment configuration. Do not store credentials in images, source control, or ConfigMaps.

Prefer separate service principals for the worker and API. At a high level:

- The worker identity needs Databricks permission to use the Lakebase project/endpoint and mint database credentials, plus Postgres connect, `agent_policy` read, and required `agent_ops` DML/sequence privileges.
- The API identity needs the same connection/credential path plus Postgres read privileges for UI and operational metrics; add write permissions only for endpoints that demonstrably write directly to Lakebase.
- The setup/operator identity separately needs Unity Catalog `USE`/`SELECT`/`CREATE` permissions for the governed source and pipeline storage, permissions to register and manage the synced table, and Lakebase project management access for schema/CDF administration.

Network policy and security groups must allow the pods to reach the Databricks workspace APIs over HTTPS and the Lakebase endpoint over TLS, while the worker and API also reach Temporal Cloud. Keep the worker free of public ingress.

For Temporal Cloud, fill:

- `TEMPORAL_ADDRESS`
- `TEMPORAL_NAMESPACE`
- `TEMPORAL_API_KEY`

The checked-in `config/example.env` is pointed at:

```bash
TEMPORAL_ADDRESS=us-west-2.aws.api.temporal.io:7233
TEMPORAL_NAMESPACE=tf-demo.zsvab
TEMPORAL_TLS=true
```

Fill `TEMPORAL_API_KEY` with a key that can access namespace `tf-demo.zsvab`.

For model calls, fill:

- `OPENAI_API_KEY`
- `AGENT_MODEL`

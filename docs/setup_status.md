# Setup Status

Last updated: 2026-07-12

## Completed

### AWS and Unity Catalog storage

- S3 bucket verified in `us-east-2`: `temporal-lakebase-agentworkflow-429214323166-us-east-2`.
- IAM role backing Unity Catalog access: `arn:aws:iam::429214323166:role/DatabricksUnityCatalogTemporalLakebase`.
- Unity Catalog storage credential: `temporal_lakebase_agentworkflow_storage`.
- Unity Catalog external location: `temporal_lakebase_agentworkflow_managed` at `s3://temporal-lakebase-agentworkflow-429214323166-us-east-2/`.
- Managed catalog: `agentworkflow_lakehouse`, with storage root `s3://temporal-lakebase-agentworkflow-429214323166-us-east-2/unity-catalog/agentworkflow_lakehouse`.
- Required schemas verified: `agentworkflow_lakehouse.agent_ops`, `agentworkflow_lakehouse.governed`, and `agentworkflow_lakehouse.sync_metadata`.

### Lakebase operational plane

- Databricks CLI profile `dbc-33a81e40` and interactive operator identity `sam.ingbar@temporal.io` were verified.
- Lakebase Postgres 17 project: `projects/temporal-lakebase-agentworkflow`.
- Production branch: `projects/temporal-lakebase-agentworkflow/branches/production`, state `READY`.
- Primary endpoint: `projects/temporal-lakebase-agentworkflow/branches/production/endpoints/primary`, state `ACTIVE`, host `ep-icy-king-d8l6oqs8.database.us-east-2.cloud.databricks.com`.
- The current `agent_ops` migration is applied in `databricks_postgres`: all 11 expected tables/views exist, including `agent_review_decisions` and the retry-count view; `message_id`, `event_id`, and `note_id` are present.

### Governed policy loop

- Governed Delta source exists with Change Data Feed enabled: `agentworkflow_lakehouse.governed.underwriting_policy_limits`.
- The six demo policy rows are present.
- Lakebase database registration exists: `temporal_agentworkflow_lakebase` maps `databricks_postgres` on the production branch.
- Synced-table resource `temporal_agentworkflow_lakebase.agent_policy.underwriting_policy_limits` is `SYNCED_TABLE_ONLINE_CONTINUOUS_UPDATE` with Unity Catalog provisioning `ACTIVE`.
- Sync pipeline ID: `ab876963-de71-49c4-b312-48debf8a2dc9`; latest verified Delta source version: `2`.

### EKS runtime

- Cluster: `ferocious-electro-pumpkin` in `us-west-2`; Kubernetes `v1.35.4-eks-40737a8` with mixed amd64/arm64 nodes.
- The stale human trust entry on `arn:aws:iam::429214323166:role/AmazonEKSAutoClusterRole-Ingbar` was replaced with the current `SolutionsArchitecture/AWSAdministratorAccess` SSO role. The EKS service trust was preserved, role assumption was verified, and the role's existing cluster-scoped `AmazonEKSClusterAdminPolicy` access entry is used by kubeconfig context `ferocious-electro-pumpkin-ingbar-admin`.
- Existing prerequisites are healthy: cert-manager `v1.20.2`, metrics-server, and Temporal Worker Controller app `v1.7.0` / chart `0.26.0`.
- Immutable, scan-on-push ECR repositories exist for `temporal-lakebase-agentworkflow-web` and `temporal-lakebase-agentworkflow-worker`.
- Current image tag: `20260712-a91e17b3d04a`. Web digest: `sha256:2ce9610cb5c89e77e624b57ab5ead43ef499c6692242830fdb5d714363910ac9`. Worker digest: `sha256:e87beb35838ac2c21d971c49570cb71fae8f5b3b94843c2df7ea3c8bbeeacab2`.
- Helm release `lakebase-agentworkflow`, revision `4`, is deployed in namespace `tf-demo-zsvab` from `deploy/helm/temporal-lakebase-agentworkflow/values.eks.yaml`.
- Web/API: two healthy non-root amd64 replicas behind private `ClusterIP` service `lakebase-agentworkflow-web`; no public load balancer or ingress was created.
- Worker: Temporal Worker Controller `WorkerDeployment/lakebase-agentworkflow-worker` is `Ready` with current build `20260712-a91e17b3d04a-f657`. The per-version HPA targets 70% CPU with 1-10 replicas, and the per-version PDB requires one available replica.
- The runtime Kubernetes Secret is `temporal-lakebase-agentworkflow-secrets`. Values are not stored in source or Helm configuration.

### Off-platform Databricks runtime identity

- Workspace service principal: `lakebase-agentworkflow-eks`, workspace ID `73038686840546`, client/application ID `b9ffbc64-3f01-4de5-b233-17d1d2bff7cd`.
- Lakebase project permission: `CAN_USE` only.
- Matching production-branch Lakebase OAuth/Postgres role: `lakebase-agentworkflow-eks`; it is not a superuser and has no `createdb`, `createrole`, or `bypassrls` attributes.
- Postgres grants: database `CONNECT`; `agent_ops` schema `USAGE`; existing and owner-created tables `SELECT`/`INSERT`/`UPDATE`; sequences `USAGE`; `agent_policy` schema `USAGE`; and `SELECT` only on `agent_policy.underwriting_policy_limits`. No `DELETE`, schema creation, project management, Unity Catalog administration, or synced-table administration was granted.

### Deployment smoke evidence

- Local validation: 19 Python tests, React production build, Helm lint/render, server-side Kubernetes dry run, and amd64 web/worker image builds passed.
- Smoke workflow: `lakebase-agent-37396a21-c6bd-4a0c-8a3a-96ce063609a8`; Temporal run ID `019f582e-115e-715e-b599-72f7e48ebc50`.
- Temporal reports workflow type `LoanUnderwritingWorkflow`, task queue `lakebase-agent-task-queue`, and running status while it waits durably for review.
- Lakebase reports run `37396a21-c6bd-4a0c-8a3a-96ce063609a8` as `awaiting_review` after credit, income, DTI, and policy activities. The policy result is `refer`, uses the continuously synced threshold row, and reports `policy_source=lakebase_synced_table`.
- Operational APIs returned run, retry, latency, and token metrics. The current smoke run recorded 810 scripted tokens and zero activity retries.
- The smoke workflow is intentionally left at the review gate so the deployed UI can demonstrate the human approval/request-more-information path.

## Pending

- Lakebase Change Data Feed from `agent_ops` to `agentworkflow_lakehouse.agent_ops` is not enabled or has not produced any history tables. A read-only catalog check returned no `lb_<table>_history` tables.
- The web service is private. Public access requires a separately reviewed authenticated ingress or load balancer configuration.

## Required Next Steps

1. Open the private service through the documented loopback port forward and submit a review decision for the waiting smoke run when ready to demonstrate the full human-in-the-loop path.
2. Enable Lakebase Change Data Feed for Postgres schema `agent_ops`, targeting `agentworkflow_lakehouse.agent_ops`, then verify `lb_agent_runs_history` and `lb_agent_review_decisions_history` after the review decision is written.
3. Add an authenticated ingress only if access beyond the local port forward is required.

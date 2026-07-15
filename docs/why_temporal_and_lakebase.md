# Why Temporal and Why Lakebase

This demo makes two independent claims. Keep them separate and prove each one on screen.

| Question | Temporal answers it | Lakebase answers it |
| --- | --- | --- |
| What happens when the worker dies halfway through a loan file? | The workflow history preserves completed steps and schedules the exact next activity. | The completed tool evidence remains available in the application-facing Postgres view. |
| How can a recommendation wait hours or days for an underwriter? | A durable signal wait holds execution state without keeping an application process alive. | The pending recommendation is queryable for the review UI and the final decision is an audit row. |
| Where does governed underwriting policy come from? | Temporal records which policy activity ran; it is not the policy system of record. | A managed synced table serves Unity Catalog policy through low-latency Postgres. |
| Where does the reviewed outcome go? | Temporal records the decision in workflow history and resumes deterministically. | With CDF enabled, Lakebase Change Data Feed (Public Preview) writes Postgres changes back to Unity Catalog Delta history for audit and analytics. |

The shortest version is:

> **Temporal protects progress. Lakebase closes the governed data loop.**

## The fair comparison with Neon and other managed Postgres services

Do not position Lakebase as the only capable serverless Postgres. Neon is a credible alternative for the database layer: its official documentation describes autoscaling, scale to zero, database branching, and read replicas. Lakebase also offers autoscaling, scale to zero, branching, and standard Postgres connectivity. Those primitives establish parity for this conversation; they are not the decisive demo claim.

The differentiator is the Databricks-native loop this demo makes visible:

1. Data engineering owns `agentworkflow_lakehouse.governed.underwriting_policy_limits` in Unity Catalog.
2. A managed synced table materializes that policy as read-only `agent_policy.underwriting_policy_limits` in Lakebase for low-latency agent lookup.
3. The agent writes the run, evidence, recommendation, and human decision to ordinary Lakebase Postgres tables.
4. When enabled, the Public Preview Lakebase Change Data Feed captures those operational changes as `lb_<table>_history` Delta tables in Unity Catalog.

An external Postgres service can participate in the same architecture. The comparison is about integration ownership, not possibility: the customer must select, secure, monitor, and operate the Unity Catalog-to-Postgres serving path and the Postgres-to-Delta CDC path. With Lakebase, those two paths are native Databricks capabilities and use the same Lakebase project and Unity Catalog governance plane. The application can still run outside Databricks—in this demo the web/API runs on EKS and the standalone worker is managed there by Temporal Worker Controller; both authenticate to the Databricks data plane with OAuth.

| Capability in this demo | Lakebase | Neon or another external Postgres |
| --- | --- | --- |
| Standard Postgres application backend | Native | Native |
| Autoscaling, idle suspension, and database branching | Native | Available in Neon; capabilities vary by provider |
| Unity Catalog table served as a managed Postgres table | Databricks synced table | Add and operate a chosen replication/reverse-ETL integration |
| Postgres changes captured as Unity Catalog Delta history | Lakebase Change Data Feed (Public Preview) | Add and operate a chosen CDC/ETL integration |
| External Kubernetes application connectivity | Standard Postgres plus an off-platform Databricks OAuth service principal | Standard Postgres plus the selected provider's identity, secrets, and network access |
| Durable retries, crash recovery, and human waits | Temporal, not Lakebase | Temporal, not the database provider |

This framing is intentionally narrow and defensible. If the application has no meaningful Unity Catalog or Databricks data gravity, evaluate Lakebase and external Postgres providers on normal database criteria such as regions, extensions, performance, operations, and cost. If governed lakehouse data must be served into an application and its decisions must flow back to Delta, Lakebase removes two major integration seams.

### Which alternative wins when?

| Product | Strong fit | Why Lakebase wins this particular demo |
| --- | --- | --- |
| Neon | Developer-first serverless Postgres where branching, autoscaling, scale-to-zero, and read replicas are the main requirement | The database primitives are not the differentiator; the managed Unity Catalog input and Delta change-history paths are |
| Aurora Serverless v2 | An AWS-centered application that wants Aurora operations and AWS-native analytical integrations | If Databricks is the governed analytical home, the UC serving path and CDF audit path remain external integration work |
| Supabase | A product team that values the bundled Auth, Storage, Realtime, APIs, and Postgres developer experience | This demo needs Databricks-governed policy and an operational-to-Delta loop more than a bundled backend platform |

This is a platform-fit decision, not a claim that competitors lack scale, security, or data integration options.

## Presenter talk track

### At the worker kill: why Temporal

“The process is gone. Temporal still knows that credit and income completed, so the replacement worker starts with DTI. We did not rebuild workflow state from database flags, and we did not repeat completed calls.”

### At the review panel: why Lakebase

“Temporal is holding the execution, but it is not pretending to be our application database. Lakebase gives the review UI a normal indexed Postgres view of the loan, evidence, and recommendation. The policy on this screen was governed in Unity Catalog and served locally through a synced table.”

### At the Unity Catalog policy row: why not generic Postgres alone

“Neon or another managed Postgres can store this application state. The Lakebase reason is the managed data path: our governed gold policy arrives in Postgres without the agent querying an analytical warehouse or our team operating a reverse-ETL job.”

### At the Unity Catalog audit row: close the loop

“Now the direction reverses. The underwriter’s decision is an ordinary Postgres write, and—with CDF enabled—the Public Preview Lakebase Change Data Feed makes it Delta history in Unity Catalog. Policy came down to the operational path; the evidence and decision went back up for audit. That is the Lakebase proof point.”

### If asked, “Could we still use Neon?”

“Yes. Temporal is database-independent, and Neon is a strong serverless Postgres. The design question is whether you want to own the two data movement paths, their credentials, monitoring, and lineage. For a Databricks-centered customer, Lakebase makes those paths part of the platform instead of a separate integration project.”

## What to show, in order

1. **Temporal UI:** two completed tool activities and the worker-kill gap.
2. **Lakebase query:** the same two persisted tool messages while the worker is absent.
3. **Review UI:** `AWAITING_REVIEW`, recommendation evidence, and human actions.
4. **Unity Catalog source:** the exact policy row and threshold used by `policy_lookup`.
5. **Tool result:** `policy_source=lakebase_synced_table` and the matching policy values.
6. **Unity Catalog destination:** the reviewed row in `lb_agent_review_decisions_history`.

Do not stop after step 3. Steps 4–6 are what turn “we used Postgres” into “Lakebase closes the governed operational loop.”

## Official product references

- Databricks: [Lakebase Postgres overview](https://docs.databricks.com/aws/en/oltp/)
- Databricks: [Serve lakehouse data with synced tables](https://docs.databricks.com/aws/en/oltp/projects/sync-tables)
- Databricks: [Lakebase Change Data Feed](https://docs.databricks.com/aws/en/oltp/projects/lakebase-cdf)
- Databricks: [Lakebase and Unity Catalog integration](https://docs.databricks.com/aws/en/oltp/projects/lakehouse-integrations)
- Databricks: [Connect to Lakebase and authenticate](https://docs.databricks.com/aws/en/oltp/projects/authentication)
- Neon: [Scale to zero](https://neon.com/docs/introduction/scale-to-zero)
- Neon: [Branching workflows](https://neon.com/docs/guides/branching-intro)
- Neon: [Autoscaling and read replicas](https://neon.com/docs/ai/ai-scale-with-neon)
- AWS: [Aurora Serverless v2 auto-pause](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-serverless-v2-auto-pause.html)
- Supabase: [Compute and disk](https://supabase.com/docs/guides/platform/compute-and-disk)
- Supabase: [Platform features](https://supabase.com/docs/guides/getting-started/features)

Lakebase Change Data Feed is currently documented by Databricks as Public Preview. Treat that status as part of production qualification, not as a footnote to hide during the demo.

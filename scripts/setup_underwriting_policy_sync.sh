#!/usr/bin/env bash

set -Eeuo pipefail

usage() {
  cat <<'USAGE'
Create and seed the governed underwriting policy table in Unity Catalog, then
sync it into a Lakebase Autoscaling project as agent_policy.underwriting_policy_limits.

Usage:
  scripts/setup_underwriting_policy_sync.sh --profile <databricks-profile> [options]

Options:
  --profile <name>             Required Databricks CLI profile.
  --project-id <id>            Lakebase project (default: temporal-lakebase-agentworkflow).
  --branch <id>                Lakebase branch (default: production).
  --database <name>            Postgres database (default: databricks_postgres).
  --source-catalog <name>      Regular UC catalog (default: agentworkflow_lakehouse).
  --source-schema <name>       Governed policy schema (default: governed).
  --lakebase-catalog <name>    UC registration for Lakebase (default: temporal_agentworkflow_lakebase).
  --target-schema <name>       Postgres target schema (default: agent_policy).
  --storage-schema <name>      Pipeline metadata schema (default: sync_metadata).
  --scheduling-policy <mode>   CONTINUOUS or TRIGGERED (default: CONTINUOUS).
  -h, --help                   Show this help.
USAGE
}

PROFILE=""
PROJECT_ID="temporal-lakebase-agentworkflow"
BRANCH="production"
POSTGRES_DATABASE="databricks_postgres"
SOURCE_CATALOG="agentworkflow_lakehouse"
SOURCE_SCHEMA="governed"
LAKEBASE_CATALOG="temporal_agentworkflow_lakebase"
TARGET_SCHEMA="agent_policy"
STORAGE_SCHEMA="sync_metadata"
SCHEDULING_POLICY="CONTINUOUS"
TABLE_NAME="underwriting_policy_limits"

while (( $# > 0 )); do
  case "$1" in
    --profile)
      PROFILE="${2:?--profile requires a value}"
      shift 2
      ;;
    --project-id)
      PROJECT_ID="${2:?--project-id requires a value}"
      shift 2
      ;;
    --branch)
      BRANCH="${2:?--branch requires a value}"
      shift 2
      ;;
    --database)
      POSTGRES_DATABASE="${2:?--database requires a value}"
      shift 2
      ;;
    --source-catalog)
      SOURCE_CATALOG="${2:?--source-catalog requires a value}"
      shift 2
      ;;
    --source-schema)
      SOURCE_SCHEMA="${2:?--source-schema requires a value}"
      shift 2
      ;;
    --lakebase-catalog)
      LAKEBASE_CATALOG="${2:?--lakebase-catalog requires a value}"
      shift 2
      ;;
    --target-schema)
      TARGET_SCHEMA="${2:?--target-schema requires a value}"
      shift 2
      ;;
    --storage-schema)
      STORAGE_SCHEMA="${2:?--storage-schema requires a value}"
      shift 2
      ;;
    --scheduling-policy)
      SCHEDULING_POLICY="${2:?--scheduling-policy requires a value}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$PROFILE" ]]; then
  echo "--profile is required; choose one from 'databricks auth profiles'." >&2
  exit 2
fi

if ! command -v databricks >/dev/null 2>&1; then
  echo "Databricks CLI >= 0.294.0 is required." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required to build CLI request JSON safely." >&2
  exit 1
fi

validate_uc_identifier() {
  local label="$1"
  local value="$2"
  if [[ ! "$value" =~ ^[A-Za-z][A-Za-z0-9_]*$ ]]; then
    echo "$label must match [A-Za-z][A-Za-z0-9_]*: $value" >&2
    exit 2
  fi
}

validate_uc_identifier "database" "$POSTGRES_DATABASE"
validate_uc_identifier "source catalog" "$SOURCE_CATALOG"
validate_uc_identifier "source schema" "$SOURCE_SCHEMA"
validate_uc_identifier "Lakebase catalog" "$LAKEBASE_CATALOG"
validate_uc_identifier "target schema" "$TARGET_SCHEMA"
validate_uc_identifier "storage schema" "$STORAGE_SCHEMA"

if [[ ! "$PROJECT_ID" =~ ^[a-z][a-z0-9-]*$ ]]; then
  echo "project id must use lowercase letters, numbers, and hyphens: $PROJECT_ID" >&2
  exit 2
fi
if [[ ! "$BRANCH" =~ ^[a-z][a-z0-9-]*$ ]]; then
  echo "branch must use lowercase letters, numbers, and hyphens: $BRANCH" >&2
  exit 2
fi
case "$SCHEDULING_POLICY" in
  CONTINUOUS|TRIGGERED) ;;
  *)
    echo "scheduling policy must be CONTINUOUS or TRIGGERED" >&2
    exit 2
    ;;
esac

cli_version_output="$(databricks --version)"
cli_version="$(printf '%s' "$cli_version_output" | sed -E 's/.*v([0-9]+\.[0-9]+\.[0-9]+).*/\1/')"
cli_major="${cli_version%%.*}"
cli_minor_patch="${cli_version#*.}"
cli_minor="${cli_minor_patch%%.*}"
if [[ ! "$cli_major" =~ ^[0-9]+$ || ! "$cli_minor" =~ ^[0-9]+$ ]]; then
  echo "Could not parse Databricks CLI version: $cli_version_output" >&2
  exit 1
fi
if (( cli_major == 0 && cli_minor < 294 )); then
  echo "Databricks CLI >= 0.294.0 is required; found $cli_version." >&2
  exit 1
fi

echo "Using $cli_version_output"
databricks postgres create-synced-table -h >/dev/null
databricks current-user me --profile "$PROFILE" >/dev/null

SOURCE_TABLE="$SOURCE_CATALOG.$SOURCE_SCHEMA.$TABLE_NAME"
SYNCED_TABLE_ID="$LAKEBASE_CATALOG.$TARGET_SCHEMA.$TABLE_NAME"
BRANCH_PATH="projects/$PROJECT_ID/branches/$BRANCH"

run_sql() {
  local description="$1"
  local statement="$2"
  echo "$description"
  databricks experimental aitools tools query \
    --profile "$PROFILE" \
    --output json \
    "$statement" >/dev/null
}

run_sql \
  "Creating Unity Catalog policy schema $SOURCE_CATALOG.$SOURCE_SCHEMA..." \
  "CREATE SCHEMA IF NOT EXISTS $SOURCE_CATALOG.$SOURCE_SCHEMA"

run_sql \
  "Creating sync pipeline metadata schema $SOURCE_CATALOG.$STORAGE_SCHEMA..." \
  "CREATE SCHEMA IF NOT EXISTS $SOURCE_CATALOG.$STORAGE_SCHEMA"

run_sql \
  "Creating governed Delta policy table $SOURCE_TABLE..." \
  "CREATE TABLE IF NOT EXISTS $SOURCE_TABLE (
     loan_purpose STRING NOT NULL,
     min_credit_score INT NOT NULL,
     max_dti DECIMAL(5,4) NOT NULL,
     decline_below_credit_score INT NOT NULL,
     decline_above_dti DECIMAL(5,4) NOT NULL
   ) USING DELTA"

run_sql \
  "Enabling Change Data Feed for incremental policy sync..." \
  "ALTER TABLE $SOURCE_TABLE
   SET TBLPROPERTIES (delta.enableChangeDataFeed = true)"

run_sql \
  "Upserting demo underwriting policy rows..." \
  "MERGE INTO $SOURCE_TABLE AS target
   USING (
     SELECT * FROM VALUES
       ('default', 700, CAST(0.4300 AS DECIMAL(5,4)), 620, CAST(0.5500 AS DECIMAL(5,4))),
       ('debt_consolidation', 690, CAST(0.4500 AS DECIMAL(5,4)), 620, CAST(0.5500 AS DECIMAL(5,4))),
       ('home_improvement', 700, CAST(0.4300 AS DECIMAL(5,4)), 620, CAST(0.5500 AS DECIMAL(5,4))),
       ('major_purchase', 710, CAST(0.4000 AS DECIMAL(5,4)), 630, CAST(0.5200 AS DECIMAL(5,4))),
       ('medical', 660, CAST(0.4500 AS DECIMAL(5,4)), 600, CAST(0.5800 AS DECIMAL(5,4))),
       ('other', 690, CAST(0.4200 AS DECIMAL(5,4)), 620, CAST(0.5200 AS DECIMAL(5,4)))
     AS policy(
       loan_purpose,
       min_credit_score,
       max_dti,
       decline_below_credit_score,
       decline_above_dti
     )
   ) AS source
   ON target.loan_purpose = source.loan_purpose
   WHEN MATCHED THEN UPDATE SET
     min_credit_score = source.min_credit_score,
     max_dti = source.max_dti,
     decline_below_credit_score = source.decline_below_credit_score,
     decline_above_dti = source.decline_above_dti
   WHEN NOT MATCHED THEN INSERT (
     loan_purpose,
     min_credit_score,
     max_dti,
     decline_below_credit_score,
     decline_above_dti
   ) VALUES (
     source.loan_purpose,
     source.min_credit_score,
     source.max_dti,
     source.decline_below_credit_score,
     source.decline_above_dti
   )"

if databricks postgres get-catalog \
  "catalogs/$LAKEBASE_CATALOG" \
  --profile "$PROFILE" >/dev/null 2>&1; then
  echo "Lakebase database is already registered as UC catalog $LAKEBASE_CATALOG."
else
  echo "Registering $POSTGRES_DATABASE as Lakebase UC catalog $LAKEBASE_CATALOG..."
  catalog_json="$(python3 -c '
import json
import sys
print(json.dumps({"spec": {"postgres_database": sys.argv[1], "branch": sys.argv[2]}}))
' "$POSTGRES_DATABASE" "$BRANCH_PATH")"
  databricks postgres create-catalog \
    "$LAKEBASE_CATALOG" \
    --json "$catalog_json" \
    --profile "$PROFILE"
fi

if databricks postgres get-synced-table \
  "synced_tables/$SYNCED_TABLE_ID" \
  --profile "$PROFILE" >/dev/null 2>&1; then
  echo "Synced table $SYNCED_TABLE_ID already exists; leaving its pipeline intact."
else
  echo "Creating $SCHEDULING_POLICY UC-to-Lakebase synced table $SYNCED_TABLE_ID..."
  synced_table_json="$(python3 -c '
import json
import sys

source, policy, branch, database, catalog, schema = sys.argv[1:]
print(json.dumps({
    "spec": {
        "source_table_full_name": source,
        "primary_key_columns": ["loan_purpose"],
        "scheduling_policy": policy,
        "branch": branch,
        "postgres_database": database,
        "create_database_objects_if_missing": True,
        "new_pipeline_spec": {
            "storage_catalog": catalog,
            "storage_schema": schema,
        },
    }
}))
' \
    "$SOURCE_TABLE" \
    "$SCHEDULING_POLICY" \
    "$BRANCH_PATH" \
    "$POSTGRES_DATABASE" \
    "$SOURCE_CATALOG" \
    "$STORAGE_SCHEMA")"

  databricks postgres create-synced-table \
    "$SYNCED_TABLE_ID" \
    --json "$synced_table_json" \
    --profile "$PROFILE"
fi

echo
echo "Current synced-table status:"
databricks postgres get-synced-table \
  "synced_tables/$SYNCED_TABLE_ID" \
  --profile "$PROFILE"

echo
echo "UC source:       $SOURCE_TABLE"
echo "Lakebase target: $TARGET_SCHEMA.$TABLE_NAME in $POSTGRES_DATABASE"
echo "The Lakebase target is sync-managed and read-only; do not INSERT, UPDATE, or DELETE it."

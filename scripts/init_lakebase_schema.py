from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import psycopg


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize the Lakebase operational schema.")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--branch", default="production")
    parser.add_argument("--endpoint", default="primary")
    parser.add_argument("--database", default="databricks_postgres")
    parser.add_argument("--user", required=True)
    parser.add_argument("--sql", default="sql/001_agent_ops_schema.sql")
    return parser.parse_args()


def databricks_json(args: list[str]) -> dict:
    result = subprocess.run(
        ["databricks", *args, "-o", "json"],
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


def main() -> None:
    args = parse_args()
    endpoint_path = (
        f"projects/{args.project_id}/branches/{args.branch}/endpoints/{args.endpoint}"
    )
    endpoint = databricks_json(
        ["postgres", "get-endpoint", endpoint_path, "--profile", args.profile]
    )
    credential = databricks_json(
        [
            "postgres",
            "generate-database-credential",
            endpoint_path,
            "--profile",
            args.profile,
        ]
    )

    host = endpoint["status"]["hosts"]["host"]
    token = credential["token"]
    sql = Path(args.sql).read_text()

    with psycopg.connect(
        host=host,
        dbname=args.database,
        user=args.user,
        password=token,
        sslmode="require",
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()

    print(
        json.dumps(
            {
                "project_id": args.project_id,
                "branch": args.branch,
                "endpoint": args.endpoint,
                "host": host,
                "database": args.database,
                "schema_initialized": True,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()


#!/usr/bin/env python3
"""Initialize the PROROK SQLite database in persistent storage.

This script is intentionally safe to run multiple times. It applies
prorok/schema.sql with CREATE IF NOT EXISTS statements and prints a compact
health-check of the resulting database.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Iterable


DEFAULT_PROROK_HOME = "/data/workspace/prorok"
DEFAULT_DB_NAME = "prorok.sqlite3"


class InitError(RuntimeError):
    """Raised when PROROK DB initialization cannot be completed."""


def utc_now_expr() -> str:
    return "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


def resolve_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_schema_path(explicit_schema_path: str | None) -> Path:
    if explicit_schema_path:
        return Path(explicit_schema_path).expanduser().resolve()
    return resolve_repo_root() / "prorok" / "schema.sql"


def resolve_prorok_home(explicit_home: str | None) -> Path:
    if explicit_home:
        return Path(explicit_home).expanduser().resolve()

    env_home = os.getenv("PROROK_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()

    # Railway exposes the persistent volume mount path in this variable when a
    # volume is attached. If available, keep PROROK data under it.
    volume_path = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if volume_path:
        return Path(volume_path).expanduser().resolve() / "prorok"

    data_dir = os.getenv("DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser().resolve() / "workspace" / "prorok"

    return Path(DEFAULT_PROROK_HOME).resolve()


def resolve_db_path(prorok_home: Path, explicit_db_path: str | None) -> Path:
    if explicit_db_path:
        return Path(explicit_db_path).expanduser().resolve()

    env_db = os.getenv("PROROK_DB")
    if env_db:
        return Path(env_db).expanduser().resolve()

    return prorok_home / DEFAULT_DB_NAME


def read_sql(path: Path) -> str:
    if not path.exists():
        raise InitError(f"Schema file not found: {path}")
    return path.read_text(encoding="utf-8")


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_names(conn: sqlite3.Connection, object_type: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = ?
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """,
        (object_type,),
    ).fetchall()
    return [row["name"] for row in rows]


def ensure_required_objects(conn: sqlite3.Connection) -> None:
    required_tables = {
        "meta",
        "events",
        "runs",
        "sources",
        "evidence_items",
        "assessments",
    }
    required_views = {
        "latest_event_state",
        "assessment_history",
        "event_evidence_summary",
        "source_registry",
        "daily_run_summary",
    }

    tables = set(fetch_names(conn, "table"))
    views = set(fetch_names(conn, "view"))

    missing_tables = sorted(required_tables - tables)
    missing_views = sorted(required_views - views)

    if missing_tables or missing_views:
        parts = []
        if missing_tables:
            parts.append(f"missing tables: {', '.join(missing_tables)}")
        if missing_views:
            parts.append(f"missing views: {', '.join(missing_views)}")
        raise InitError("Schema health-check failed: " + "; ".join(parts))


def apply_schema(db_path: Path, schema_sql: str) -> None:
    with connect(db_path) as conn:
        conn.executescript(schema_sql)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
        ensure_required_objects(conn)


def print_healthcheck(db_path: Path, schema_path: Path, prorok_home: Path) -> None:
    with connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = fetch_names(conn, "table")
        views = fetch_names(conn, "view")
        meta_rows = conn.execute("SELECT key, value, updated_at FROM meta ORDER BY key").fetchall()

    print("OK: PROROK DB initialized")
    print(f"PROROK_HOME: {prorok_home}")
    print(f"DB_PATH: {db_path}")
    print(f"SCHEMA_PATH: {schema_path}")
    print(f"DB_EXISTS: {db_path.exists()}")
    print(f"DB_SIZE_BYTES: {db_path.stat().st_size if db_path.exists() else 0}")
    print(f"PRAGMA journal_mode: {journal_mode}")
    print(f"PRAGMA foreign_keys: {foreign_keys}")
    print(f"PRAGMA user_version: {user_version}")
    print("TABLES:")
    for name in tables:
        print(f" - {name}")
    print("VIEWS:")
    for name in views:
        print(f" - {name}")
    print("META:")
    for row in meta_rows:
        value = row["value"]
        if len(value) > 80:
            value = value[:77] + "..."
        print(f" - {row['key']}: {value}")


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize PROROK SQLite database")
    parser.add_argument("--home", help="PROROK data directory. Defaults to PROROK_HOME or Railway volume path.")
    parser.add_argument("--db", help="SQLite database path. Defaults to PROROK_DB or <home>/prorok.sqlite3.")
    parser.add_argument("--schema", help="Path to schema.sql. Defaults to repository prorok/schema.sql.")
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    prorok_home = resolve_prorok_home(args.home)
    db_path = resolve_db_path(prorok_home, args.db)
    schema_path = resolve_schema_path(args.schema)

    try:
        prorok_home.mkdir(parents=True, exist_ok=True)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        schema_sql = read_sql(schema_path)
        apply_schema(db_path, schema_sql)
        print_healthcheck(db_path, schema_path, prorok_home)
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI should print any failure compactly.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

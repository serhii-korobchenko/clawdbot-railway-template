from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "prorok"
    / "prorok_refresh_all_dry_run_quiet.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        "prorok_refresh_all_dry_run_quiet",
        MODULE_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_schema_conn(version: str) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE meta(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE refresh_runs(
            refresh_id INTEGER PRIMARY KEY
        );

        CREATE TABLE refresh_event_results(
            refresh_event_result_id INTEGER PRIMARY KEY
        );

        CREATE TABLE refresh_candidate_evidence(
            candidate_id INTEGER PRIMARY KEY
        );
        """
    )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?)",
        (version,),
    )
    return conn


def test_refresh_schema_accepts_v4(tmp_path: Path) -> None:
    module = load_module()
    conn = make_schema_conn("4")
    try:
        module.require_refresh_schema(conn)
    finally:
        conn.close()


def test_refresh_schema_accepts_v3(tmp_path: Path) -> None:
    module = load_module()
    conn = make_schema_conn("3")
    try:
        module.require_refresh_schema(conn)
    finally:
        conn.close()


def test_refresh_schema_rejects_v2(tmp_path: Path) -> None:
    module = load_module()
    conn = make_schema_conn("2")
    try:
        try:
            module.require_refresh_schema(conn)
        except RuntimeError as exc:
            assert "schema v3 or v4 required" in str(exc)
        else:
            raise AssertionError("schema v2 should be rejected")
    finally:
        conn.close()

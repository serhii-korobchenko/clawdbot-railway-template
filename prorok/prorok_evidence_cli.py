#!/usr/bin/env python3
"""PROROK source/evidence CLI extension.

Dependency-free helper for testing source URL deduplication and event evidence
storage before the daily web-search workflow is connected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DEFAULT_PROROK_HOME = "/data/workspace/prorok"
DEFAULT_DB_NAME = "prorok.sqlite3"
TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "gbraid", "wbraid", "mc_cid", "mc_eid", "igshid", "ref", "ref_src"}


class CliError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_home(explicit_home: str | None) -> Path:
    if explicit_home:
        return Path(explicit_home).expanduser().resolve()
    if os.getenv("PROROK_HOME"):
        return Path(os.environ["PROROK_HOME"]).expanduser().resolve()
    if os.getenv("RAILWAY_VOLUME_MOUNT_PATH"):
        return Path(os.environ["RAILWAY_VOLUME_MOUNT_PATH"]).expanduser().resolve() / "prorok"
    if os.getenv("DATA_DIR"):
        return Path(os.environ["DATA_DIR"]).expanduser().resolve() / "workspace" / "prorok"
    return Path(DEFAULT_PROROK_HOME).resolve()


def resolve_db(args: argparse.Namespace) -> Path:
    if args.db:
        return Path(args.db).expanduser().resolve()
    if os.getenv("PROROK_DB"):
        return Path(os.environ["PROROK_DB"]).expanduser().resolve()
    return resolve_home(args.home) / DEFAULT_DB_NAME


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise CliError(f"PROROK DB not found: {db_path}")
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def fetch_one(conn: sqlite3.Connection, sql: str, params: Sequence[object] = ()) -> sqlite3.Row | None:
    return conn.execute(sql, params).fetchone()


def canonicalize_url(url: str) -> tuple[str, str, str]:
    raw = url.strip()
    if not raw:
        raise CliError("URL cannot be empty")
    parts = urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        raise CliError(f"URL must include scheme and host: {url}")
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    filtered_query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered in TRACKING_QUERY_KEYS:
            continue
        if any(lowered.startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES):
            continue
        filtered_query.append((key, value))
    query = urlencode(sorted(filtered_query), doseq=True)
    canonical = urlunsplit((scheme, netloc, path, query, ""))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    domain = netloc[4:] if netloc.startswith("www.") else netloc
    return canonical, digest, domain


def create_run(conn: sqlite3.Connection, notes: str) -> int:
    cur = conn.execute("INSERT INTO runs(run_type, status, notes) VALUES ('manual_cli', 'running', ?)", (notes,))
    return int(cur.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, new_sources_found: int = 0, errors: str | None = None) -> None:
    conn.execute(
        """
        UPDATE runs
        SET finished_at = ?, status = ?, events_processed = 1, new_sources_found = ?, errors = ?
        WHERE run_id = ?
        """,
        (utc_now(), status, new_sources_found, errors, run_id),
    )


def upsert_source(conn: sqlite3.Connection, args: argparse.Namespace) -> tuple[int, bool, str, str]:
    canonical_url, canonical_hash, domain = canonicalize_url(args.url)
    existed = fetch_one(conn, "SELECT source_id FROM sources WHERE canonical_url_hash = ?", (canonical_hash,)) is not None
    now = utc_now()
    conn.execute(
        """
        INSERT INTO sources(url, canonical_url, canonical_url_hash, title, domain, published_at,
                            first_seen_at, last_seen_at, source_type, raw_metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(canonical_url_hash) DO UPDATE SET
            last_seen_at = excluded.last_seen_at,
            source_type = COALESCE(sources.source_type, excluded.source_type),
            raw_metadata = COALESCE(sources.raw_metadata, excluded.raw_metadata)
        """,
        (
            args.url.strip(), canonical_url, canonical_hash, args.title, domain, args.published_at,
            now, now, args.source_type, json.dumps({"cli": True, "input_url": args.url}, ensure_ascii=False),
        ),
    )
    row = fetch_one(conn, "SELECT source_id FROM sources WHERE canonical_url_hash = ?", (canonical_hash,))
    if row is None:
        raise CliError("Source upsert failed")
    return int(row["source_id"]), not existed, canonical_url, canonical_hash


def cmd_add(args: argparse.Namespace) -> int:
    db_path = resolve_db(args)
    with connect(db_path) as conn:
        run_id = None
        try:
            event = fetch_one(conn, "SELECT event_id FROM events WHERE event_id = ?", (args.event_id,))
            if event is None:
                raise CliError(f"Event not found: {args.event_id}")
            run_id = create_run(conn, "Add PROROK evidence item from CLI")
            source_id, is_new_source, canonical_url, canonical_hash = upsert_source(conn, args)
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO evidence_items(event_id, source_id, run_id, direction, strength,
                                                     summary, relevance, credibility, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (args.event_id, source_id, run_id, args.direction, args.strength, args.summary,
                 args.relevance, args.credibility, utc_now()),
            )
            evidence_added = cur.rowcount == 1
            finish_run(conn, run_id, "completed", 1 if is_new_source else 0)
            conn.commit()
        except Exception as exc:
            if run_id is not None:
                finish_run(conn, run_id, "failed", 0, str(exc))
                conn.commit()
            raise

    print("OK: evidence processed")
    print(f"event_id: {args.event_id}")
    print(f"source_id: {source_id}")
    print(f"new_source: {is_new_source}")
    print(f"evidence_added: {evidence_added}")
    print(f"canonical_url: {canonical_url}")
    print(f"canonical_url_hash: {canonical_hash}")
    return 0


def cmd_list_sources(args: argparse.Namespace) -> int:
    db_path = resolve_db(args)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT source_id, domain, title, url, canonical_url, published_at, first_seen_at,
                   last_seen_at, used_as_evidence_count
            FROM source_registry
            ORDER BY last_seen_at DESC, source_id DESC
            LIMIT ?
            """,
            (args.limit,),
        ).fetchall()
    print(f"PROROK sources: {len(rows)}")
    for row in rows:
        print("")
        print(f"source_id: {row['source_id']} | domain: {row['domain'] or 'n/a'} | used: {row['used_as_evidence_count']}")
        print(f"title: {row['title'] or 'n/a'}")
        print(f"url: {row['url']}")
        if args.show_canonical:
            print(f"canonical_url: {row['canonical_url']}")
    return 0


def cmd_list_evidence(args: argparse.Namespace) -> int:
    db_path = resolve_db(args)
    params: list[object] = [args.event_id]
    direction_sql = ""
    if args.direction != "all":
        direction_sql = " AND ei.direction = ?"
        params.append(args.direction)
    params.append(args.limit)
    with connect(db_path) as conn:
        event = fetch_one(conn, "SELECT event_id, title FROM events WHERE event_id = ?", (args.event_id,))
        if event is None:
            raise CliError(f"Event not found: {args.event_id}")
        rows = conn.execute(
            """
            SELECT ei.evidence_id, ei.direction, ei.strength, ei.summary, ei.relevance, ei.credibility,
                   ei.created_at, s.source_id, s.domain, s.title AS source_title, s.url, s.canonical_url
            FROM evidence_items ei
            JOIN sources s ON s.source_id = ei.source_id
            WHERE ei.event_id = ?
            """ + direction_sql + """
            ORDER BY ei.created_at DESC, ei.evidence_id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    print(f"PROROK evidence: {event['title']}")
    print(f"event_id: {args.event_id}")
    print(f"rows: {len(rows)}")
    for row in rows:
        print("")
        print(f"evidence_id: {row['evidence_id']} | source_id: {row['source_id']}")
        print(f"direction: {row['direction']} | strength: {row['strength'] or 'n/a'}")
        print(f"relevance: {row['relevance']} | credibility: {row['credibility']} | created_at: {row['created_at']}")
        print(f"summary: {row['summary']}")
        print(f"source: {row['source_title'] or row['domain'] or 'n/a'}")
        print(f"url: {row['url']}")
        if args.show_canonical:
            print(f"canonical_url: {row['canonical_url']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PROROK evidence/source CLI")
    parser.add_argument("--home", help="PROROK data directory")
    parser.add_argument("--db", help="SQLite DB path")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add-evidence", help="Add an indicator/counterindicator source to an event")
    add.add_argument("event_id")
    add.add_argument("--url", required=True)
    add.add_argument("--title")
    add.add_argument("--published-at")
    add.add_argument("--source-type", default="web")
    add.add_argument("--direction", required=True, choices=["indicator", "counterindicator", "neutral"])
    add.add_argument("--strength", default="medium", choices=["weak", "medium", "strong"])
    add.add_argument("--summary", required=True)
    add.add_argument("--relevance", type=int, default=50, choices=range(0, 101), metavar="0-100")
    add.add_argument("--credibility", type=int, default=50, choices=range(0, 101), metavar="0-100")
    add.set_defaults(func=cmd_add)

    sources = sub.add_parser("sources", help="List source registry entries")
    sources.add_argument("--limit", type=int, default=50)
    sources.add_argument("--show-canonical", action="store_true")
    sources.set_defaults(func=cmd_list_sources)

    evidence = sub.add_parser("evidence", help="List evidence for an event")
    evidence.add_argument("event_id")
    evidence.add_argument("--direction", default="all", choices=["indicator", "counterindicator", "neutral", "all"])
    evidence.add_argument("--limit", type=int, default=50)
    evidence.add_argument("--show-canonical", action="store_true")
    evidence.set_defaults(func=cmd_list_evidence)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except CliError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except sqlite3.Error as exc:
        print(f"SQLITE_ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

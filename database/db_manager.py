import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List

from config.app_config import DEFAULT_SETTINGS
from database.schema import SCHEMA_SQL, SCHEMA_VERSION


class DatabaseCorruptError(Exception):
    pass


class Database:
    """Thin wrapper. Every thread must call connect() and use its own connection."""

    def __init__(self, path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)  # autocommit; explicit BEGIN
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.DatabaseError as e:
            conn.close()
            raise DatabaseCorruptError(str(e))
        return conn

    def initialize(self) -> None:
        conn = self.connect()
        try:
            conn.executescript(SCHEMA_SQL)
            conn.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
        except sqlite3.DatabaseError as e:
            raise DatabaseCorruptError(str(e))
        finally:
            conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def integrity_problems(conn: sqlite3.Connection) -> List[str]:
    problems: List[str] = []
    try:
        for row in conn.execute("PRAGMA integrity_check"):
            if row[0] != "ok":
                problems.append(str(row[0]))
        try:
            conn.execute("INSERT INTO pages_fts(pages_fts) VALUES('integrity-check')")
        except sqlite3.DatabaseError as e:
            problems.append(f"FTS index: {e}")
        orphan = conn.execute(
            "SELECT COUNT(*) FROM pages WHERE document_id NOT IN (SELECT document_id FROM documents)"
        ).fetchone()[0]
        if orphan:
            problems.append(f"{orphan} orphaned page rows")
        fts_missing = conn.execute(
            "SELECT COUNT(*) FROM pages WHERE page_id NOT IN (SELECT rowid FROM pages_fts)"
        ).fetchone()[0]
        if fts_missing:
            problems.append(f"{fts_missing} pages missing from search index")
    except sqlite3.DatabaseError as e:
        problems.append(str(e))
    return problems


# -- per-library settings ----------------------------------------------------
def get_settings(conn: sqlite3.Connection) -> Dict[str, Any]:
    out = dict(DEFAULT_SETTINGS)
    for row in conn.execute("SELECT key,value FROM settings"):
        try:
            out[row["key"]] = json.loads(row["value"])
        except ValueError:
            pass
    return out


def set_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
    )


def now() -> float:
    return time.time()

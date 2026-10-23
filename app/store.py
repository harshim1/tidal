"""SQLite persistence for readings and webhook de-duplication.

SQLite keeps the hackathon build dependency-free; the schema maps 1:1 onto a
DynamoDB table (pk=device_id, sk=started_at) for the hosted version.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import cfg

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id     TEXT NOT NULL,
    device_name   TEXT,
    source        TEXT NOT NULL,          -- ring_clip | ring_live | upload | demo
    started_at    TEXT NOT NULL,          -- ISO-8601 UTC, start of the analysed footage
    duration_s    REAL,
    resp_bpm      REAL,
    resp_quality  REAL,
    pulse_bpm     REAL,
    pulse_quality REAL,
    status        TEXT,
    status_reason TEXT,
    pauses        TEXT,                   -- JSON [[start_s, end_s], ...]
    motion        REAL,
    windows       TEXT,                   -- JSON per-window rates
    waveform      TEXT,                   -- JSON decimated waveform
    waveform_fs   REAL,
    video         TEXT,                   -- relative path of magnified render
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS readings_time ON readings(started_at);
CREATE TABLE IF NOT EXISTS webhook_seen (
    request_id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL
);
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(cfg.db_path, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init() -> None:
    with _lock, _conn() as c:
        c.executescript(SCHEMA)


def seen_webhook(request_id: str) -> bool:
    """Record a webhook request id; True if it was already processed (duplicate delivery)."""
    with _lock, _conn() as c:
        try:
            c.execute(
                "INSERT INTO webhook_seen(request_id, received_at) VALUES (?, ?)",
                (request_id, datetime.now(UTC).isoformat()),
            )
            return False
        except sqlite3.IntegrityError:
            return True


def add_reading(r: dict[str, Any]) -> int:
    cols = [
        "device_id", "device_name", "source", "started_at", "duration_s", "resp_bpm", "resp_quality",
        "pulse_bpm", "pulse_quality", "status", "status_reason", "pauses", "motion", "windows",
        "waveform", "waveform_fs", "video",
    ]
    vals = []
    for k in cols:
        v = r.get(k)
        if k in ("pauses", "windows", "waveform") and v is not None:
            v = json.dumps(v)
        vals.append(v)
    with _lock, _conn() as c:
        cur = c.execute(
            f"INSERT INTO readings({','.join(cols)}, created_at) VALUES ({','.join('?' * len(cols))}, ?)",
            (*vals, datetime.now(UTC).isoformat()),
        )
        return int(cur.lastrowid)


def _row(row: sqlite3.Row, full: bool = False) -> dict[str, Any]:
    d = dict(row)
    for k in ("pauses", "windows", "waveform"):
        if d.get(k):
            d[k] = json.loads(d[k])
    if not full:
        d.pop("waveform", None)
        d.pop("windows", None)
    return d


def latest(full: bool = True) -> dict[str, Any] | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM readings ORDER BY started_at DESC, id DESC LIMIT 1").fetchone()
    return _row(row, full) if row else None


def get(reading_id: int) -> dict[str, Any] | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM readings WHERE id = ?", (reading_id,)).fetchone()
    return _row(row, True) if row else None


def since(hours: float, full: bool = False) -> list[dict[str, Any]]:
    cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM readings WHERE started_at >= ? ORDER BY started_at ASC", (cutoff,)
        ).fetchall()
    return [_row(r, full) for r in rows]


def clear() -> None:
    with _lock, _conn() as c:
        c.execute("DELETE FROM readings")
        c.execute("DELETE FROM webhook_seen")

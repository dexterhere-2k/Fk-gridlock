"""GridLock — 04: SQLite ledger (one file, per spec 00 §"Store").

Two tables:
  - predictions(id, ts, payload_json, request_json)  — every prediction
    made by the API, so the learning loop can replay them.
  - outcomes(event_id, ts, actual_p50, actual_p90, actual_closure, notes)
    — actual values reported via /api/outcome, joined back to predictions
    for plan-vs-actual analysis.

Backed by stdlib `sqlite3` (no async, no ORM — keeps the dependency surface
small and the demo runnable on the spec's "potato PC" target). The
`Ledger` class is process-safe: the FastAPI server is single-process
under uvicorn --workers 1, so the connection pool is just a single
connection per call.
"""
from __future__ import annotations
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    id           TEXT PRIMARY KEY,
    ts           REAL NOT NULL,
    endpoint     TEXT NOT NULL,
    request_json TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_predictions_ts ON predictions(ts);
CREATE INDEX IF NOT EXISTS idx_predictions_endpoint ON predictions(endpoint);

CREATE TABLE IF NOT EXISTS outcomes (
    event_id     TEXT PRIMARY KEY,
    ts           REAL NOT NULL,
    actual_p50_min     REAL,
    actual_p90_min     REAL,
    actual_closure     INTEGER,  -- 0/1
    actual_officers    INTEGER,
    actual_barricades  INTEGER,
    notes        TEXT,
    payload_json TEXT
);

CREATE TABLE IF NOT EXISTS incidents (
    id           TEXT PRIMARY KEY,
    state        TEXT NOT NULL,
    reported_at  REAL NOT NULL,
    last_transition_at REAL NOT NULL,
    corridor     TEXT,
    cause        TEXT,
    priority     TEXT,
    sla_minutes  INTEGER DEFAULT 60,
    extra_json   TEXT
);
CREATE INDEX IF NOT EXISTS idx_incidents_state ON incidents(state);
"""


class Ledger:
    """Minimal SQLite ledger for predictions + outcomes + incident state."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
        # memo for incident states (so /api/incidents/active works in demo)
        self._incident_states: dict[str, dict] = {}

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(str(self.path))
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # ---- predictions
    def log_prediction(self, endpoint: str, request: dict, payload: dict) -> str:
        pid = str(uuid.uuid4())
        with self._conn() as c:
            c.execute(
                "INSERT INTO predictions (id, ts, endpoint, request_json, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (pid, time.time(), endpoint, json.dumps(request, default=str),
                 json.dumps(payload, default=str)),
            )
        return pid

    def count_predictions(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) AS n FROM predictions").fetchone()["n"]

    # ---- outcomes
    def log_outcome(self, event_id: str, actual: dict, notes: Optional[str] = None) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO outcomes "
                "(event_id, ts, actual_p50_min, actual_p90_min, actual_closure, "
                " actual_officers, actual_barricades, notes, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, time.time(),
                 actual.get("actual_p50_min"),
                 actual.get("actual_p90_min"),
                 int(actual["actual_closure"]) if actual.get("actual_closure") is not None
                 else None,
                 actual.get("actual_officers_deployed"),
                 actual.get("actual_barricades"),
                 notes,
                 json.dumps(actual, default=str)),
            )

    def get_outcomes(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT event_id, ts, actual_p50_min, actual_p90_min, actual_closure, "
                "actual_officers, actual_barricades, notes FROM outcomes "
                "ORDER BY ts"
            ).fetchall()
        return [dict(r) for r in rows]

    def count_outcomes(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) AS n FROM outcomes").fetchone()["n"]

    # ---- incidents (in-memory + persisted)
    def upsert_incident(self, inc: dict) -> None:
        self._incident_states[inc["id"]] = inc
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO incidents "
                "(id, state, reported_at, last_transition_at, corridor, cause, "
                " priority, sla_minutes, extra_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (inc["id"], inc["state"], inc["reported_at"],
                 inc["last_transition_at"], inc.get("corridor"),
                 inc.get("cause"), inc.get("priority"),
                 inc.get("sla_minutes", 60),
                 json.dumps(inc, default=str)),
            )

    def get_incident(self, inc_id: str) -> Optional[dict]:
        if inc_id in self._incident_states:
            return dict(self._incident_states[inc_id])
        with self._conn() as c:
            row = c.execute("SELECT extra_json FROM incidents WHERE id = ?",
                             (inc_id,)).fetchone()
        if row is None:
            return None
        return json.loads(row["extra_json"])

    def list_active_incidents(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT extra_json FROM incidents WHERE state NOT IN ('closed')"
            ).fetchall()
        out = []
        for r in rows:
            try:
                inc = json.loads(r["extra_json"])
                out.append(inc)
            except Exception:
                continue
        return out

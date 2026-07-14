"""SQLite working store.

Three tables, per the build spec:
  insights               — one row per extracted insight (written in Stage 1)
  needs_matrix_snapshots — persona x task_type counts per week (Stage 3)
  weekly_priorities      — ranked priority list + rationale per week (Stage 4)

Stage 1 only writes `insights`; the other two tables are created up front so the
schema is defined in one place and downstream stages have somewhere to write.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .schema import Insight

SCHEMA = """
CREATE TABLE IF NOT EXISTS insights (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id     TEXT NOT NULL,
    persona     TEXT NOT NULL,
    task_type   TEXT NOT NULL,
    description TEXT NOT NULL,
    evidence    TEXT NOT NULL,
    date        TEXT NOT NULL,          -- YYYY-MM-DD (call date if known, else file date)
    ingested_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_insights_call_id ON insights(call_id);
CREATE INDEX IF NOT EXISTS idx_insights_persona_task ON insights(persona, task_type);

CREATE TABLE IF NOT EXISTS needs_matrix_snapshots (
    week      TEXT NOT NULL,
    persona   TEXT NOT NULL,
    task_type TEXT NOT NULL,
    count     INTEGER NOT NULL,
    PRIMARY KEY (week, persona, task_type)
);

CREATE TABLE IF NOT EXISTS weekly_priorities (
    week             TEXT PRIMARY KEY,
    ranked_list_json TEXT NOT NULL,
    rationale        TEXT NOT NULL
);
"""


class Store:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(database_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def delete_insights_for_call(self, call_id: str) -> None:
        """Remove existing rows for a transcript before re-inserting (idempotent re-runs)."""
        self.conn.execute("DELETE FROM insights WHERE call_id = ?", (call_id,))
        self.conn.commit()

    def insert_insights(self, call_id: str, insights: list[Insight], fallback_date: str) -> int:
        """Insert validated insights for one transcript. Returns the row count written.

        `fallback_date` (the file's date) is used when an insight has no call_date.
        """
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (
                call_id,
                ins.persona.strip(),
                ins.task_type,
                ins.description.strip(),
                ins.evidence.strip(),
                (ins.call_date or fallback_date),
                now,
            )
            for ins in insights
        ]
        self.conn.executemany(
            "INSERT INTO insights (call_id, persona, task_type, description, evidence, date, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def count_insights(self) -> int:
        cur = self.conn.execute("SELECT COUNT(*) AS n FROM insights")
        return cur.fetchone()["n"]

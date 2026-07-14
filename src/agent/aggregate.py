"""Stage 3 — deterministic aggregation of the insights table (no LLM).

Pivots insights into a persona × task_type needs matrix and simple rollups the
Insights tab renders. Uses pandas groupby/pivot, per the build spec.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from .schema import TASK_TYPES
from .store import Store

INSIGHT_COLUMNS = ["call_id", "persona", "task_type", "description", "evidence", "date"]


def load_insights_df(
    store: Store,
    date_from: str | None = None,
    date_to: str | None = None,
) -> pd.DataFrame:
    """Load insight rows into a DataFrame (empty DataFrame with columns if none).

    `date_from`/`date_to` are inclusive YYYY-MM-DD bounds; the `date` column is
    stored as YYYY-MM-DD text, so string comparison filters correctly.
    """
    clauses, params = [], []
    if date_from:
        clauses.append("date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("date <= ?")
        params.append(date_to)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = store.conn.execute(
        f"SELECT {', '.join(INSIGHT_COLUMNS)} FROM insights{where}", params
    ).fetchall()
    if not rows:
        return pd.DataFrame(columns=INSIGHT_COLUMNS)
    return pd.DataFrame([dict(r) for r in rows], columns=INSIGHT_COLUMNS)


def needs_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """persona (rows) × task_type (cols) counts, with all task_type columns present.

    Returns an empty DataFrame (with the task_type columns) when there are no insights.
    """
    cols = list(TASK_TYPES)
    if df.empty:
        return pd.DataFrame(columns=cols)
    pivot = pd.pivot_table(
        df, index="persona", columns="task_type", values="call_id",
        aggfunc="count", fill_value=0,
    )
    # Ensure every task_type column exists and column order is stable.
    for c in cols:
        if c not in pivot.columns:
            pivot[c] = 0
    pivot = pivot[cols]
    pivot["total"] = pivot.sum(axis=1)
    return pivot.sort_values("total", ascending=False)


def counts_by_task_type(df: pd.DataFrame) -> dict[str, int]:
    counts = {t: 0 for t in TASK_TYPES}
    if not df.empty:
        counts.update(df["task_type"].value_counts().to_dict())
    return counts


def counts_by_month(df: pd.DataFrame) -> pd.DataFrame:
    """Insight counts per calendar month (columns: month, count), chronologically."""
    if df.empty:
        return pd.DataFrame(columns=["month", "count"])
    months = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m")
    out = months.value_counts().sort_index().rename_axis("month").reset_index(name="count")
    return out


def _current_week() -> str:
    """ISO year-week label for 'this run', e.g. 2026-W29."""
    iso = datetime.now().isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def write_matrix_snapshot(store: Store, df: pd.DataFrame, week: str | None = None) -> str:
    """Write one row per (week, persona, task_type) into needs_matrix_snapshots.

    Idempotent for a given week — replaces that week's rows. Returns the week label.
    """
    week = week or _current_week()
    store.conn.execute("DELETE FROM needs_matrix_snapshots WHERE week = ?", (week,))
    if not df.empty:
        grouped = df.groupby(["persona", "task_type"]).size().reset_index(name="count")
        store.conn.executemany(
            "INSERT INTO needs_matrix_snapshots (week, persona, task_type, count) "
            "VALUES (?, ?, ?, ?)",
            [(week, r["persona"], r["task_type"], int(r["count"])) for _, r in grouped.iterrows()],
        )
    store.conn.commit()
    return week

"""Service layer — all UI-agnostic logic behind the Streamlit app.

Each function is a self-contained operation the UI (or, later, a FastAPI route)
can call. Read operations (overview/matrix/insights) need no LLM; the write and
Q&A operations (ingest/query/priorities) do.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import aggregate
from .config import Config, load_config
from .loader import load_transcripts
from .manifest import Manifest
from .pipeline import make_client, process_transcript
from .schema import PriorityRanking
from .store import Store


# --------------------------------------------------------------------------- #
# Read operations (no LLM)                                                     #
# --------------------------------------------------------------------------- #

def date_bounds(config: Config | None = None) -> tuple[str, str] | None:
    """Full (earliest, latest) insight date as YYYY-MM-DD, ignoring any filter.

    Used to seed the Dashboard's date-range picker with the whole data span so the
    picker bounds don't shrink when a narrower range is selected. None if no insights.
    """
    config = config or load_config()
    with Store(config.database_path) as store:
        row = store.conn.execute(
            "SELECT MIN(date) AS lo, MAX(date) AS hi FROM insights"
        ).fetchone()
    if not row or not row["lo"]:
        return None
    return (row["lo"], row["hi"])


def overview_stats(
    config: Config | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """Headline analytics for the Dashboard, optionally scoped to a date range.

    `transcripts_ok` is the number of distinct transcripts represented in the
    insights DB (respecting `date_from`/`date_to`), so it's always consistent with
    the insights shown and does not depend on manifest.json existing.
    `transcripts_failed` is a best-effort count from the manifest (extraction
    failures aren't stored in the DB); it reads 0 when the manifest is absent.
    """
    config = config or load_config()
    manifest = Manifest.load(config.manifest_path)
    n_failed = sum(1 for e in manifest.processed.values() if e.get("status") == "failed")

    with Store(config.database_path) as store:
        df = aggregate.load_insights_df(store, date_from=date_from, date_to=date_to)

    transcripts_ok = int(df["call_id"].nunique()) if not df.empty else 0

    date_range = None
    if not df.empty:
        dates = pd.to_datetime(df["date"], errors="coerce").dropna()
        if not dates.empty:
            date_range = (dates.min().date().isoformat(), dates.max().date().isoformat())

    return {
        "transcripts_ok": transcripts_ok,
        "transcripts_failed": n_failed,
        "total_insights": int(len(df)),
        "n_personas": int(df["persona"].nunique()) if not df.empty else 0,
        "date_range": date_range,
        "by_task_type": aggregate.counts_by_task_type(df),
        "by_month": aggregate.counts_by_month(df),
    }


def needs_matrix(
    config: Config | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    write_snapshot: bool = False,
) -> pd.DataFrame:
    """persona × task_type matrix, optionally scoped to a date range.

    Optionally persist a weekly snapshot (of the filtered rows).
    """
    config = config or load_config()
    with Store(config.database_path) as store:
        df = aggregate.load_insights_df(store, date_from=date_from, date_to=date_to)
        matrix = aggregate.needs_matrix(df)
        if write_snapshot and not df.empty:
            aggregate.write_matrix_snapshot(store, df)
    return matrix


def list_insights(
    task_type: str | None = None,
    persona: str | None = None,
    search: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    config: Config | None = None,
) -> pd.DataFrame:
    """Filtered insight rows for the table view.

    `date_from`/`date_to` are inclusive YYYY-MM-DD bounds; the `date` column is
    stored as YYYY-MM-DD text, which sorts and compares correctly as strings.
    """
    config = config or load_config()
    clauses, params = [], []
    if task_type:
        clauses.append("task_type = ?")
        params.append(task_type)
    if persona:
        clauses.append("persona = ?")
        params.append(persona)
    if date_from:
        clauses.append("date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("date <= ?")
        params.append(date_to)
    if search:
        clauses.append("(description LIKE ? OR evidence LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with Store(config.database_path) as store:
        rows = store.conn.execute(
            f"SELECT date, persona, task_type, description, evidence, call_id "
            f"FROM insights {where} ORDER BY date DESC, id DESC",
            params,
        ).fetchall()
    cols = ["date", "persona", "task_type", "description", "evidence", "call_id"]
    return pd.DataFrame([dict(r) for r in rows], columns=cols)


def distinct_personas(config: Config | None = None) -> list[str]:
    config = config or load_config()
    with Store(config.database_path) as store:
        rows = store.conn.execute(
            "SELECT DISTINCT persona FROM insights ORDER BY persona"
        ).fetchall()
    return [r["persona"] for r in rows]


# --------------------------------------------------------------------------- #
# Write / LLM operations                                                      #
# --------------------------------------------------------------------------- #

def ingest_uploaded_file(filename: str, content: bytes, config: Config | None = None) -> dict:
    """Save an uploaded transcript into the transcripts folder and ingest it.

    Returns {call_id, status, num_insights, error, saved_path}.
    """
    config = config or load_config()
    config.ensure_data_dirs()
    config.transcripts_dir.mkdir(parents=True, exist_ok=True)

    dest = config.transcripts_dir / Path(filename).name
    dest.write_bytes(content)

    transcripts = load_transcripts(config.transcripts_dir, config.transcript_extensions,
                                   file_list=[dest])
    if not transcripts:
        return {"call_id": dest.stem, "status": "failed", "num_insights": 0,
                "error": "File saved but no readable text was extracted "
                         "(unsupported/empty file?).", "saved_path": str(dest)}

    client = make_client()
    manifest = Manifest.load(config.manifest_path)
    with Store(config.database_path) as store:
        result = process_transcript(client, transcripts[0], store, manifest, config)
    result["saved_path"] = str(dest)
    return result


def answer_question(question: str, config: Config | None = None) -> dict:
    """Answer one question over ALL extracted insights (stateless, single call)."""
    config = config or load_config()
    with Store(config.database_path) as store:
        rows = store.conn.execute(
            "SELECT call_id, persona, task_type, description, evidence, date "
            "FROM insights ORDER BY date"
        ).fetchall()

    if not rows:
        return {"answer": "There are no insights yet — ingest some transcripts first.",
                "insights_used": 0, "transcripts_used": 0}

    context = "\n".join(
        f"- [{r['task_type']}] {r['persona']} ({r['date']}, call={r['call_id']}): "
        f"{r['description']} | evidence: \"{r['evidence']}\""
        for r in rows
    )
    transcripts_used = len({r["call_id"] for r in rows})

    system = (
        "You answer questions about customer product feedback using ONLY the "
        "list of extracted insights provided. Each insight has a task_type, the customer "
        "persona, a date, the source call id, a description, and an evidence quote. Base "
        "your answer strictly on these insights; if they don't contain the answer, say so. "
        "Be concise and, where useful, cite counts, personas, or dates."
    )
    user = f"Question: {question}\n\nInsights:\n{context}"

    client = make_client()
    response = client.messages.create(
        model=config.extraction_model,
        max_tokens=config.extraction_max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    answer = "".join(b.text for b in response.content if b.type == "text") or "(no answer returned)"
    return {"answer": answer, "insights_used": len(rows), "transcripts_used": transcripts_used}


def top_priorities(refresh: bool = False, config: Config | None = None) -> dict:
    """Ranked 'what to work on next' (Stage 4). Cached per ISO week in weekly_priorities.

    Returns {priorities: [...], summary, week, cached}.
    """
    config = config or load_config()
    week = aggregate._current_week()

    with Store(config.database_path) as store:
        if not refresh:
            row = store.conn.execute(
                "SELECT ranked_list_json, rationale FROM weekly_priorities WHERE week = ?",
                (week,),
            ).fetchone()
            if row:
                return {"priorities": json.loads(row["ranked_list_json"]),
                        "summary": row["rationale"], "week": week, "cached": True}

        df = aggregate.load_insights_df(store)
        if df.empty:
            return {"priorities": [], "summary": "No insights yet — ingest transcripts first.",
                    "week": week, "cached": False}
        matrix = aggregate.needs_matrix(df)
        aggregate.write_matrix_snapshot(store, df, week)

    # Build a compact prompt: the matrix counts + a sample of the underlying insights.
    matrix_text = matrix.to_string()
    sample = df.groupby("task_type").head(25)
    sample_text = "\n".join(
        f"- [{r.task_type}] {r.persona}: {r.description}" for r in sample.itertuples()
    )
    system = (
        "You are a product analyst. Given a persona × task_type needs matrix "
        "and a sample of the underlying customer insights, produce a ranked list of the top "
        "~5 priorities engineering/product should work on next. Rank by how often a theme "
        "recurs and how many personas it affects. Ground each rationale in the data."
    )
    user = (
        f"Needs matrix (counts):\n{matrix_text}\n\n"
        f"Sample insights:\n{sample_text}\n\n"
        "Return the ranked priorities."
    )

    client = make_client()
    response = client.messages.parse(
        model=config.extraction_model,
        max_tokens=config.extraction_max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_format=PriorityRanking,
    )
    ranking = response.parsed_output
    if not isinstance(ranking, PriorityRanking):
        return {"priorities": [], "summary": "Could not generate priorities this run.",
                "week": week, "cached": False}

    priorities = [p.model_dump() for p in ranking.priorities]
    with Store(config.database_path) as store:
        store.conn.execute(
            "INSERT OR REPLACE INTO weekly_priorities (week, ranked_list_json, rationale) "
            "VALUES (?, ?, ?)",
            (week, json.dumps(priorities), ranking.summary),
        )
        store.conn.commit()

    return {"priorities": priorities, "summary": ranking.summary, "week": week, "cached": False}

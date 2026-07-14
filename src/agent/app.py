"""Customer Feedback Insights — Streamlit UI.

Three tabs: Insights (analytics + needs matrix + top priorities), Query (ask one
question over the extracted insights), and Upload New Transcript (add a file and
ingest it).

Run it:
    export ANTHROPIC_API_KEY="sk-ant-..."
    PYTHONPATH=src venv/bin/streamlit run src/agent/app.py
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from agent import service
from agent.config import load_config
from agent.extract import FatalExtractionError
from agent.schema import TASK_TYPES, task_type_label

st.set_page_config(page_title="Customer Feedback Insights", page_icon="📊", layout="wide")


def _fatal_help(e: Exception) -> None:
    """Render a friendly message for missing-key / quota / bad-model errors."""
    st.error("The LLM request failed in a way retrying won't fix.")
    st.caption(str(e))
    st.info(
        "Check that `ANTHROPIC_API_KEY` is set and that `extraction_model` in config.yaml is "
        "a valid model for your key (run `python -m agent.list_models`). A 429 error means "
        "you've hit a rate limit — wait a minute and retry."
    )


st.title("Customer Feedback Insights")

# Make the tab labels larger than Streamlit's default.
st.markdown(
    """
    <style>
    .stTabs [data-baseweb="tab-list"] { gap: 12px; }
    .stTabs [data-baseweb="tab"] {
        font-size: 1.25rem;
        font-weight: 600;
        padding: 12px 24px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

try:
    config = load_config()
except Exception as e:  # noqa: BLE001
    st.error(f"Could not load configuration: {e}")
    st.stop()

dashboard_tab, insights_tab, query_tab, upload_tab = st.tabs(
    ["Dashboard", "Insights", "Query", "Upload New Transcript"]
)

# --------------------------------------------------------------------------- #
# Dashboard tab — metrics, charts, needs matrix (queryable by date range)     #
# --------------------------------------------------------------------------- #
with dashboard_tab:
    bounds = service.date_bounds(config)
    if bounds is None:
        st.info("No insights yet. Add a transcript in the **Upload New Transcript** tab, "
                "or run `python -m agent.ingest`.")
    else:
        lo, hi = date.fromisoformat(bounds[0]), date.fromisoformat(bounds[1])
        picked = st.date_input(
            "Date Range", value=(lo, hi), min_value=lo, max_value=hi,
            help="Filter every metric, chart, and the needs matrix below.",
        )
        # date_input returns one date mid-selection, a (start, end) tuple once complete.
        date_from = date_to = None
        if isinstance(picked, (tuple, list)) and len(picked) == 2:
            date_from, date_to = picked[0].isoformat(), picked[1].isoformat()
        elif isinstance(picked, date):
            date_from = date_to = picked.isoformat()

        stats = service.overview_stats(config, date_from=date_from, date_to=date_to)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Transcripts Processed", stats["transcripts_ok"])
        c2.metric("Insights Extracted", stats["total_insights"])
        c3.metric("Distinct Personas", stats["n_personas"])
        dr = stats["date_range"]
        c4.metric("Date Range", f"{dr[0]} → {dr[1]}" if dr else "—")
        if stats["transcripts_failed"]:
            st.caption(f"⚠️ {stats['transcripts_failed']} transcript(s) failed extraction "
                       "(see the manifest).")

        if stats["total_insights"] == 0:
            st.info("No insights in the selected date range.")
        else:
            left, right = st.columns(2)
            with left:
                st.subheader("Insights By Task Type")
                bt = {task_type_label(t): n for t, n in stats["by_task_type"].items()}
                st.bar_chart({"count": bt})
            with right:
                st.subheader("Insights Over Time")
                month_df = stats["by_month"]
                if not month_df.empty:
                    st.bar_chart(month_df.set_index("month")["count"])

            st.subheader("Needs Matrix — Persona × Task Type")
            st.caption("Count of insights per customer persona and task type.")
            matrix = service.needs_matrix(config, date_from=date_from, date_to=date_to)
            matrix = matrix.rename(columns={**{t: task_type_label(t) for t in TASK_TYPES},
                                            "total": "Total"})
            matrix.index.name = "Persona"
            st.dataframe(matrix, use_container_width=True)

# --------------------------------------------------------------------------- #
# Insights tab — weekly priorities + the full insights table                  #
# --------------------------------------------------------------------------- #
with insights_tab:
    stats = service.overview_stats(config)

    # --- Weekly insights: top priorities, shown first ------------------------- #
    st.subheader("Weekly Insights — What To Work On Next")
    if stats["total_insights"] == 0:
        st.info("No insights yet. Add a transcript in the **Upload New Transcript** tab, "
                "or run `python -m agent.ingest`.")
    else:
        pcol1, _ = st.columns([1, 5])
        refresh = pcol1.button("Regenerate", help="Re-run the ranking (one LLM call).")
        try:
            result = service.top_priorities(refresh=refresh, config=config)
            if result["priorities"]:
                st.caption(f"Week {result['week']}"
                           f"{' · cached' if result['cached'] else ''}")
                st.markdown(result["summary"])
                for i, p in enumerate(result["priorities"], start=1):
                    with st.expander(f"{i}. [{task_type_label(p['task_type'])}] {p['title']}"):
                        st.write(p["rationale"])
                        if p.get("affected_personas"):
                            st.caption("Affected: " + ", ".join(p["affected_personas"]))
                        if p.get("example_evidence"):
                            st.caption(f"Evidence: “{p['example_evidence']}”")
            else:
                st.write(result["summary"])
        except (FatalExtractionError, RuntimeError) as e:
            _fatal_help(e)

        st.divider()

        # --- All insights (with filters, incl. date range) ------------------- #
        dr = stats["date_range"]
        st.subheader("All Insights")
        fcol1, fcol2, fcol3, fcol4 = st.columns(4)

        task_options = ["(all)"] + list(TASK_TYPES)
        task_choice = fcol1.selectbox(
            "Task Type", task_options,
            format_func=lambda t: "(all)" if t == "(all)" else task_type_label(t),
        )

        personas = ["(all)"] + service.distinct_personas(config)
        persona_filter = fcol2.selectbox("Persona", personas)

        date_from = date_to = None
        if dr:
            min_d, max_d = date.fromisoformat(dr[0]), date.fromisoformat(dr[1])
            picked = fcol3.date_input(
                "Date Range", value=(min_d, max_d), min_value=min_d, max_value=max_d,
            )
            # date_input returns one date mid-selection, a (start, end) tuple once complete.
            if isinstance(picked, (tuple, list)) and len(picked) == 2:
                date_from, date_to = picked[0].isoformat(), picked[1].isoformat()
            elif isinstance(picked, date):
                date_from = date_to = picked.isoformat()

        search = fcol4.text_input("Search Text")

        df = service.list_insights(
            task_type=None if task_choice == "(all)" else task_choice,
            persona=None if persona_filter == "(all)" else persona_filter,
            search=search or None,
            date_from=date_from,
            date_to=date_to,
            config=config,
        )
        # Proper-cased task_type values and column headers for display.
        if not df.empty:
            df = df.copy()
            df["task_type"] = df["task_type"].map(task_type_label)
        df = df.rename(columns={
            "date": "Date", "persona": "Persona", "task_type": "Task Type",
            "description": "Description", "evidence": "Evidence", "call_id": "Call ID",
        })
        st.dataframe(df, use_container_width=True, hide_index=True)

# --------------------------------------------------------------------------- #
# Upload tab                                                                  #
# --------------------------------------------------------------------------- #
with upload_tab:
    st.subheader("Upload a New Transcript")
    st.caption("The file is saved into the transcripts folder and processed immediately.")
    exts = [e.lstrip(".") for e in config.transcript_extensions]
    uploaded = st.file_uploader(f"Transcript File ({', '.join(exts)})", type=exts)

    if uploaded is not None:
        dest = config.transcripts_dir / uploaded.name
        if dest.exists():
            st.warning(f"A file named **{uploaded.name}** already exists — processing it "
                       "again will replace its insights.")
        if st.button("Process Transcript", type="primary"):
            with st.spinner("Extracting insights…"):
                try:
                    result = service.ingest_uploaded_file(
                        uploaded.name, uploaded.getvalue(), config
                    )
                except (FatalExtractionError, RuntimeError) as e:
                    _fatal_help(e)
                else:
                    if result["status"] == "ok":
                        st.success(f"Processed **{result['call_id']}** — "
                                   f"{result['num_insights']} insight(s) extracted.")
                        st.caption("Switch to the Insights tab to see the update.")
                    else:
                        st.error(f"Extraction failed for {result['call_id']}.")
                        st.caption(result["error"])

# --------------------------------------------------------------------------- #
# Query tab                                                                   #
# --------------------------------------------------------------------------- #
with query_tab:
    st.subheader("Ask a Question About the Feedback")
    st.caption("Answered from the extracted insights across all transcripts. "
               "Each question is independent — no conversation history.")
    question = st.text_input("Your Question",
                             placeholder="e.g. What are the most common data problems?")
    if st.button("Ask", type="primary") and question.strip():
        with st.spinner("Thinking…"):
            try:
                result = service.answer_question(question.strip(), config)
            except (FatalExtractionError, RuntimeError) as e:
                _fatal_help(e)
            else:
                st.markdown(result["answer"])
                st.caption(f"Answered using {result['insights_used']} insight(s) "
                           f"from {result['transcripts_used']} transcript(s).")

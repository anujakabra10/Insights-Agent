# Customer Feedback Insights

Turns customer product-feedback transcripts into structured, searchable product
insights, surfaced through a small web app. It reads a transcript library, uses
**Anthropic's Claude** to extract every distinct insight (customer persona × task
type), stores them in **SQLite**, and presents analytics, a needs matrix,
LLM-ranked priorities, and ad-hoc Q&A through a **Streamlit** UI.

## Architecture

```
  data/*.docx ──┐
   (or upload)  │
                ▼
             loader ──▶ extract  ──▶  SQLite (insights)
          (.docx/.txt)  (Claude,          │
                         structured)       │  aggregate (pandas)
                                           ▼         │
   ┌───────────────────────────────────────────────┘
   │
   ▼
 service.py  ──────────────▶  Streamlit UI (app.py)
 (all logic,                    ├─ Dashboard : analytics · needs matrix (date-range filter)
  UI-agnostic)                  ├─ Insights  : weekly ranked priorities (Claude) · table
                                ├─ Query     : one question → answer over all insights (Claude)
                                └─ Upload    : add a transcript → ingest immediately
```

- **`service.py`** holds all business logic and is UI-agnostic; **`app.py`** is a thin
  Streamlit layer over it. The same functions could sit behind a FastAPI later with no
  rewrite.
- **`pipeline.process_transcript`** is the single ingest step shared by the batch CLI
  (`agent.ingest`) and the UI's Upload tab, so both paths behave identically.
- The three Claude call sites are **extract** (per transcript), **priorities** (rank), and
  **query** (Q&A); everything else — loading, aggregation, storage, analytics — is
  deterministic and needs no API key.

## What we send to the LLM

This pipeline does **not** use function-calling / agentic "tools". Instead it uses
Claude's **structured outputs** (`messages.parse(output_format=...)`): each call passes a
system prompt, the content, and (for the two structured calls) a Pydantic model that
constrains the reply, so the SDK hands us a validated Python object instead of free text.
The schemas live in `schema.py`.

| Call site | Where | Content sent | Response schema (the "contract") |
|---|---|---|---|
| **Extract** | `extract.py` | one transcript's text | `Extraction` → `{ insights: [ Insight ] }` |
| **Priorities** | `service.top_priorities` | the needs matrix + a sample of insights | `PriorityRanking` → `{ priorities: [ Priority ], summary }` |
| **Query** | `service.answer_question` | the question + all insights as context | *(none — free-text answer)* |

Schema fields:

- **`Insight`** — `persona`, `task_type` (enum: `bug` / `feature_request` / `polish` /
  `data_problem`), `description`, `evidence` (quote), `call_date` (optional).
- **`Priority`** — `title`, `task_type`, `rationale`, `affected_personas`, `example_evidence`.

Every call also sets a `system` prompt (the role/rules) and `max_tokens` (from
`extraction_max_tokens` in `config.yaml`). No files, external tools, or web access
are ever passed to the model — extraction and Q&A run purely on the text we provide.

## Project layout

```
config.example.yaml   Config template (copy to config.yaml)
requirements.txt       Python dependencies
data/                  Source transcripts — git-ignored (confidential); not committed
src/agent/
  config.py            Load config.yaml; read ANTHROPIC_API_KEY from the environment
  loader.py            Read transcript files (.docx/.txt/.md); date parsed from filename
  manifest.py          Track processed transcripts so re-runs only do new/changed files
  schema.py            Insight/Extraction/Priority data shapes (structured-output schemas)
  extract.py           One Claude call per transcript, with a stricter-retry fallback
  pipeline.py          Shared per-transcript ingest step (used by CLI + upload)
  store.py             SQLite store (insights / needs_matrix_snapshots / weekly_priorities)
  aggregate.py         pandas pivot into the persona × task-type needs matrix (no LLM)
  service.py           UI-agnostic logic behind every tab (analytics / ingest / query / priorities)
  app.py               Streamlit UI — Dashboard / Insights / Query / Upload tabs
  ingest.py            Batch ingestion CLI
  show.py              Print extracted insights to eyeball quality
output/                Created at runtime: SQLite DB + manifest (git-ignored)
```

## Running the UI

```bash
export ANTHROPIC_API_KEY="..."
PYTHONPATH=src venv/bin/streamlit run src/agent/app.py
```

Four tabs:
- **Dashboard** — headline metrics, charts, and the persona × task-type needs matrix, all
  filterable by a date range.
- **Insights** — the weekly LLM-ranked "what to work on next" priorities, plus a filterable
  insights table.
- **Query** — ask one question; it's answered over all extracted insights (stateless).
- **Upload New Transcript** — add a `.docx/.txt/.md` file; it's saved into the transcripts
  folder and ingested immediately.

## Setup

```bash
# 1. Dependencies (the repo ships with a venv/)
venv/bin/pip install -r requirements.txt

# 2. Config
cp config.example.yaml config.yaml      # then edit paths if needed

# 3. Anthropic API key (not stored in config) — https://console.anthropic.com/settings/keys
export ANTHROPIC_API_KEY="..."
```

## Batch ingestion (CLI)

The UI's Upload tab ingests one file at a time; use the CLI to process the existing
library in bulk (or re-process after edits). Set `ANTHROPIC_API_KEY` first.

```bash
# See what would be processed — no API calls, no cost
PYTHONPATH=src venv/bin/python -m agent.ingest --list

# Process at most 5 transcripts (good first check; keeps cost and rate-limit risk low)
PYTHONPATH=src venv/bin/python -m agent.ingest --limit 5

# Process everything new/changed
PYTHONPATH=src venv/bin/python -m agent.ingest

# List the valid model ids your key can use
PYTHONPATH=src venv/bin/python -m agent.list_models

# Eyeball the extracted insights in the terminal
PYTHONPATH=src venv/bin/python -m agent.show
```

Re-running only processes transcripts that are new or whose contents changed
(tracked in `output/manifest.json`). A transcript that fails extraction twice is
logged as failed and skipped — the run never crashes on one bad file. A terminal
error (bad key / unknown model) stops the run immediately with a clear message.

## Notes

- **Transcript format:** the library is `.docx` (AI meeting notes / Google-Docs
  transcript exports). The loader reads these via `python-docx`, and also handles
  `.txt`/`.md`. Other formats (`.vtt`, PDF) would need an additional reader.
- **Confidentiality:** `data/` (source transcripts) and `output/` (the SQLite DB) are
  git-ignored and must not be committed — they contain customer feedback. Keep the
  repository free of any real transcript content.
- **Dates:** the call date is parsed from the `YYYY_MM_DD` stamp in each filename
  (falling back to the file's modified time), and can be overridden per-insight when
  the transcript itself states a date.
- **Model:** extraction uses `claude-haiku-4-5` (via the `anthropic` SDK) — Anthropic's
  cheapest model ($1/$5 per 1M input/output tokens), fast, with a 200K-token context that
  easily holds one transcript. Change `extraction_model` in `config.yaml` to a more capable
  (and pricier) model such as `claude-sonnet-5` or `claude-opus-4-8` if extraction quality
  needs it (`python -m agent.list_models` shows what your key can access). If you hit a `429`
  rate limit, ingest in small batches (`--limit 5`); the SDK also retries `429`/`5xx` on its own.

## Deploying

Host it on [Streamlit Community Cloud](https://streamlit.io/cloud) — free, connects
directly to a GitHub repo, and gives a shareable link anyone can open (no local setup).
Add `ANTHROPIC_API_KEY` as a secret in the app's settings; it is never committed to the repo.

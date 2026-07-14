"""Shared ingestion step used by both the CLI (`agent.ingest`) and the UI upload tab.

`process_transcript` runs the extract → store → manifest step for ONE transcript,
so there is a single code path for "turn a transcript into rows in the DB".
"""

from __future__ import annotations

import anthropic

from .config import Config, require_api_key
from .extract import ExtractionError, FatalExtractionError, extract_insights
from .loader import Transcript
from .manifest import Manifest
from .store import Store


def make_client() -> anthropic.Anthropic:
    """Construct the LLM client (validates the API key is present first)."""
    return anthropic.Anthropic(api_key=require_api_key())


def process_transcript(
    client: anthropic.Anthropic,
    transcript: Transcript,
    store: Store,
    manifest: Manifest,
    config: Config,
) -> dict:
    """Extract insights for one transcript and persist them.

    Returns a result dict: {call_id, status: "ok"|"failed", num_insights, error}.
    Re-raises FatalExtractionError (bad key / no access / unknown model) so the
    caller can abort the whole run — those won't fix themselves by continuing.
    """
    try:
        extraction = extract_insights(
            client,
            transcript.text,
            model=config.extraction_model,
            max_tokens=config.extraction_max_tokens,
        )
    except FatalExtractionError:
        raise
    except ExtractionError as e:
        manifest.record_failed(transcript.call_id, transcript.content_sha, str(e))
        manifest.save()
        return {"call_id": transcript.call_id, "status": "failed",
                "num_insights": 0, "error": str(e)}

    # Re-insert cleanly so re-processing an edited transcript doesn't duplicate rows.
    store.delete_insights_for_call(transcript.call_id)
    n = store.insert_insights(transcript.call_id, extraction.insights,
                              fallback_date=transcript.date_hint)
    manifest.record_ok(transcript.call_id, transcript.content_sha, n)
    manifest.save()
    return {"call_id": transcript.call_id, "status": "ok",
            "num_insights": n, "error": None}

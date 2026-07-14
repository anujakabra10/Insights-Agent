"""Stage 1 entry point: ingest new transcripts into the SQLite store.

Run it:
    export ANTHROPIC_API_KEY="sk-ant-..."
    venv/bin/python -m agent.ingest              # process all new/changed transcripts
    venv/bin/python -m agent.ingest --limit 2    # process at most 2 (good for a first check)
    venv/bin/python -m agent.ingest --list       # just show what would be processed, no API calls

Flow, per transcript:
    load -> skip if already done (manifest) -> Claude extract -> validate ->
    write rows to SQLite -> record in manifest. A failed transcript is logged and
    skipped; it never crashes the whole run.
"""

from __future__ import annotations

import argparse
import sys

from .config import load_config
from .extract import FatalExtractionError
from .loader import load_transcripts
from .manifest import Manifest
from .pipeline import make_client, process_transcript
from .store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Customer Feedback Insights — Stage 1 ingestion.")
    parser.add_argument("--config", help="Path to config.yaml (default: ./config.yaml).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most N new transcripts (useful for a first quality check).")
    parser.add_argument("--list", action="store_true",
                        help="List transcripts and whether they'd be processed, then exit (no API calls).")
    parser.add_argument("--force", action="store_true",
                        help="Reprocess even transcripts already marked done in the manifest.")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    config.ensure_data_dirs()

    transcripts = load_transcripts(config.transcripts_dir, config.transcript_extensions)
    manifest = Manifest.load(config.manifest_path)

    if not transcripts:
        print(f"No transcripts found in {config.transcripts_dir} "
              f"(extensions: {', '.join(config.transcript_extensions)}).")
        return 0

    # Decide what to process.
    to_process = [
        t for t in transcripts
        if args.force or not manifest.is_done(t.call_id, t.content_sha)
    ]
    skipped = len(transcripts) - len(to_process)

    print(f"Found {len(transcripts)} transcript(s) in {config.transcripts_dir}.")
    print(f"  {skipped} already processed (unchanged), {len(to_process)} to process.")

    if args.list:
        for t in transcripts:
            status = "SKIP (done)" if manifest.is_done(t.call_id, t.content_sha) else "PROCESS"
            print(f"  [{status}] {t.call_id}")
        return 0

    if args.limit is not None:
        to_process = to_process[: args.limit]
        print(f"  --limit {args.limit}: processing {len(to_process)} this run.")

    if not to_process:
        print("Nothing to do.")
        return 0

    # Only need the API key once we're actually going to extract.
    client = make_client()

    ok_count = 0
    failed_count = 0
    total_insights = 0

    with Store(config.database_path) as store:
        for i, t in enumerate(to_process, start=1):
            print(f"[{i}/{len(to_process)}] {t.call_id} ... ", end="", flush=True)
            try:
                result = process_transcript(client, t, store, manifest, config)
            except FatalExtractionError as e:
                # Bad key / no quota / unknown model — every call will fail the same way.
                # Stop now instead of hammering the API for all remaining transcripts.
                print("ABORTED")
                print()
                print("Stopping the run: the API rejected the request in a way that "
                      "won't fix itself by retrying.")
                print(f"  reason: {e}")
                print("  Check your ANTHROPIC_API_KEY and that `extraction_model` in "
                      "config.yaml is valid (run `python -m agent.list_models`).")
                break

            if result["status"] == "failed":
                failed_count += 1
                print("FAILED (logged, continuing)")
                print(f"      reason: {result['error']}", file=sys.stderr)
                continue

            ok_count += 1
            total_insights += result["num_insights"]
            print(f"{result['num_insights']} insight(s)")

        db_total = store.count_insights()

    print()
    print("Stage 1 complete.")
    print(f"  transcripts ok:     {ok_count}")
    print(f"  transcripts failed: {failed_count}")
    print(f"  insights this run:  {total_insights}")
    print(f"  insights in DB:     {db_total}")
    print(f"  database:           {config.database_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

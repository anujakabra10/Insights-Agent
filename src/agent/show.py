"""Print the extracted insights from the store, for eyeballing extraction quality.

    venv/bin/python -m agent.show                 # all insights, grouped by transcript
    venv/bin/python -m agent.show --call-id foo   # just one transcript
"""

from __future__ import annotations

import argparse

from .config import load_config
from .store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show extracted insights.")
    parser.add_argument("--config", help="Path to config.yaml.")
    parser.add_argument("--call-id", help="Only show insights for this transcript.")
    args = parser.parse_args(argv)

    config = load_config(args.config)

    with Store(config.database_path) as store:
        if args.call_id:
            rows = store.conn.execute(
                "SELECT * FROM insights WHERE call_id = ? ORDER BY id", (args.call_id,)
            ).fetchall()
        else:
            rows = store.conn.execute(
                "SELECT * FROM insights ORDER BY call_id, id"
            ).fetchall()

    if not rows:
        print("No insights in the store yet. Run `python -m agent.ingest` first.")
        return 0

    current = None
    for r in rows:
        if r["call_id"] != current:
            current = r["call_id"]
            print(f"\n=== {current} ===")
        print(f"  [{r['task_type']}] ({r['persona']}, {r['date']})")
        print(f"     {r['description']}")
        print(f"     “{r['evidence']}”")

    print(f"\nTotal: {len(rows)} insight(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

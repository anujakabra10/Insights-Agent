"""Track which transcripts have already been processed.

A small JSON file records, per transcript, whether it was processed OK or failed,
when, how many insights came out, and the content hash. On re-run we skip any
transcript that already succeeded AND whose content is unchanged — so re-running
the pipeline only processes new or edited files.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Manifest:
    path: Path
    processed: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls(path=path, processed=data.get("processed", {}))
        return cls(path=path, processed={})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "processed": self.processed}, f, indent=2, sort_keys=True)

    def is_done(self, call_id: str, content_sha: str) -> bool:
        """True if this transcript was processed successfully and hasn't changed."""
        entry = self.processed.get(call_id)
        return bool(
            entry
            and entry.get("status") == "ok"
            and entry.get("content_sha") == content_sha
        )

    def record_ok(self, call_id: str, content_sha: str, num_insights: int) -> None:
        self.processed[call_id] = {
            "status": "ok",
            "content_sha": content_sha,
            "num_insights": num_insights,
            "processed_at": _now(),
        }

    def record_failed(self, call_id: str, content_sha: str, error: str) -> None:
        self.processed[call_id] = {
            "status": "failed",
            "content_sha": content_sha,
            "error": error,
            "processed_at": _now(),
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

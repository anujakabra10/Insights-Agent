"""Read transcript files from a local folder.

Point this at a synced Google Drive folder (or `data/`). Each file becomes one
`Transcript`. The `call_id` is the filename stem, which is stable across re-runs
so the manifest can track what's already been processed.

Supported formats:
  .docx        — Word / Google-Docs export (e.g. "Notes by Gemini"). Read with python-docx.
  .txt, .md    — plain text.

The call date is taken from a YYYY_MM_DD (or YYYY-MM-DD) stamp in the filename
when present — these files are named e.g. "... - 2026_06_02 14_00 MDT - ...".
That's far more reliable than the file's modified time (which is just when the
file was downloaded). If no date is in the name, we fall back to the mtime.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Matches 2026_06_02 or 2026-06-02 anywhere in the filename.
_DATE_RE = re.compile(r"(20\d{2})[_-](\d{2})[_-](\d{2})")


@dataclass
class Transcript:
    call_id: str          # stable identifier — the filename without extension
    path: Path
    text: str
    date_hint: str        # best available date, YYYY-MM-DD (from filename, else mtime)
    content_sha: str      # sha256 of the extracted text, to detect changed files on re-run


def _read_docx(path: Path) -> str:
    from docx import Document  # imported lazily so plain-text-only setups don't need it

    doc = Document(str(path))
    parts = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
    # Include any table cell text too, in case notes are laid out in tables.
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text and cell.text.strip():
                    parts.append(cell.text.strip())
    return "\n".join(parts).strip()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _extract_text(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        return _read_docx(path)
    return _read_text(path)


def _date_hint(path: Path) -> str:
    m = _DATE_RE.search(path.name)
    if m:
        year, month, day = m.groups()
        try:
            return datetime(int(year), int(month), int(day)).date().isoformat()
        except ValueError:
            pass  # nonsense date in name — fall through to mtime
    return datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()


def load_transcripts(
    transcripts_dir: Path,
    extensions: list[str],
    file_list: list[Path] | None = None,
) -> list[Transcript]:
    """Load transcripts from `transcripts_dir` (or an explicit `file_list`).

    Files are returned sorted by name for deterministic ordering. Empty files and
    files that can't be read are skipped rather than crashing the run.
    """
    if file_list is not None:
        paths = [Path(p) for p in file_list]
    else:
        if not transcripts_dir.exists():
            raise FileNotFoundError(
                f"Transcripts folder not found: {transcripts_dir}\n"
                "Check `transcripts_dir` in config.yaml."
            )
        ext_set = {e.lower() for e in extensions}
        paths = sorted(
            p for p in transcripts_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in ext_set
            and not p.name.startswith("~$")  # skip Word lock/temp files
        )

    transcripts: list[Transcript] = []
    for p in paths:
        try:
            text = _extract_text(p)
        except Exception:  # noqa: BLE001 — one unreadable file shouldn't stop the run
            continue
        if not text:
            continue
        transcripts.append(
            Transcript(
                call_id=p.stem,
                path=p,
                text=text,
                date_hint=_date_hint(p),
                content_sha=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            )
        )
    return transcripts

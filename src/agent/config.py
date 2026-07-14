"""Load and validate the pipeline configuration.

Config lives in a YAML file (see config.example.yaml). Paths in the file are
resolved relative to the project root so the pipeline can be run from anywhere.
The Anthropic API key is read from the ANTHROPIC_API_KEY environment variable,
not from the config file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Project root = two levels up from this file (src/agent/config.py -> project/).
PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


@dataclass
class Config:
    """Resolved configuration. All paths are absolute Path objects."""

    transcripts_dir: Path
    transcript_extensions: list[str]
    database_path: Path
    manifest_path: Path
    extraction_model: str
    extraction_max_tokens: int

    def ensure_data_dirs(self) -> None:
        """Create the parent directories for the DB and manifest if needed."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)


def _resolve(path_str: str) -> Path:
    """Resolve a config path relative to the project root (absolute paths kept as-is)."""
    p = Path(path_str).expanduser()
    return p if p.is_absolute() else (PROJECT_ROOT / p)


def load_config(config_path: Path | str | None = None) -> Config:
    """Read config.yaml and return a validated Config.

    When no explicit path is given and config.yaml is absent, fall back to the
    committed config.example.yaml — config.yaml is git-ignored, so deployed hosts
    (e.g. Streamlit Community Cloud) only have the example, and its defaults work
    out of the box. Raises a clear error only if neither file exists.
    """
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.exists():
        example = PROJECT_ROOT / "config.example.yaml"
        if config_path is None and example.exists():
            path = example
        else:
            raise FileNotFoundError(
                f"Config file not found at {path}.\n"
                "Create it by copying the template:\n"
                "    cp config.example.yaml config.yaml\n"
                "then edit the paths for your machine."
            )

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    required = [
        "transcripts_dir",
        "transcript_extensions",
        "database_path",
        "manifest_path",
        "extraction_model",
        "extraction_max_tokens",
    ]
    missing = [k for k in required if k not in raw]
    if missing:
        raise ValueError(
            f"Config {path} is missing required keys: {', '.join(missing)}.\n"
            "Compare it against config.example.yaml."
        )

    exts = [e if e.startswith(".") else f".{e}" for e in raw["transcript_extensions"]]

    return Config(
        transcripts_dir=_resolve(raw["transcripts_dir"]),
        transcript_extensions=exts,
        database_path=_resolve(raw["database_path"]),
        manifest_path=_resolve(raw["manifest_path"]),
        extraction_model=str(raw["extraction_model"]),
        extraction_max_tokens=int(raw["extraction_max_tokens"]),
    )


def require_api_key() -> str:
    """Return the Anthropic API key from the environment, or raise a clear error.

    Reads ANTHROPIC_API_KEY, the variable the anthropic SDK expects. Get a key at
    https://console.anthropic.com/settings/keys
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set.\n"
            "Get a key at https://console.anthropic.com/settings/keys, then:\n"
            '    export ANTHROPIC_API_KEY="..."'
        )
    return key

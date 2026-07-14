"""List the Claude models your Anthropic API key can access.

    export ANTHROPIC_API_KEY="..."
    venv/bin/python -m agent.list_models

Prints every model the key can see — copy the id you want into `extraction_model`
in config.yaml. For this pipeline the cheapest capable model is `claude-haiku-4-5`.
"""

from __future__ import annotations

import anthropic

from .config import require_api_key


def main() -> int:
    client = anthropic.Anthropic(api_key=require_api_key())

    print("Claude models available to this key:\n")
    for m in client.models.list():
        label = getattr(m, "display_name", "") or ""
        print(f"  {m.id:32s} {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

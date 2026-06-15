"""Load API keys from the repo-root ``.env`` so callers never paste keys.

Both entry points (`cli.py` and `web/server.py`) call :func:`load_env` at import
time. ``ANTHROPIC_API_KEY`` and ``GEMINI_API_KEY`` live in ``.env`` (gitignored);
the LLM SDKs read them from the environment, so this just bridges the file into
``os.environ``. Existing real environment variables are not overridden.
"""
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

# src/onuw/_env.py -> parents[2] is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]


def load_env() -> None:
    """Populate os.environ from the repo-root .env (no-op if absent)."""
    load_dotenv(_REPO_ROOT / ".env")

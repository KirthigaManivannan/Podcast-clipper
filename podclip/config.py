"""Central configuration for PodClip.

The only user-editable setting is ``OPENROUTER_API_KEY`` in ``.env``.  Every
other setting has a sensible default and may optionally be overridden via
environment variables (see README.md):

- ``PRIMARY_MODEL``        preferred OpenRouter model id (e.g. ``qwen/qwen3:free``)
- ``FALLBACK_MODELS``      comma-separated extra models to try before auto-selected ones
- ``WHISPER_MODEL``        faster-whisper model size (default ``base``)
- ``NUM_CLIPS``            number of clips to produce (default 5)
- ``CLIP_MIN_SECONDS``     minimum clip length (default 20)
- ``CLIP_MAX_SECONDS``     maximum clip length (default 45)
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = PROJECT_DIR / "outputs"
CACHE_DIR = PROJECT_DIR / ".cache"
ENV_FILE = PROJECT_DIR / ".env"

load_dotenv(ENV_FILE)

# --- OpenRouter connection -------------------------------------------------
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_HTTP_REFERER = os.getenv("OPENROUTER_HTTP_REFERER", "https://podclip.local")
OPENROUTER_X_TITLE = os.getenv("OPENROUTER_X_TITLE", "podclip")

LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "60.0"))
LLM_CALL_DELAY = float(os.getenv("LLM_CALL_DELAY", "0.5"))  # politeness between calls
MODEL_CACHE_TTL_SECONDS = float(os.getenv("MODEL_CACHE_TTL_SECONDS", str(6 * 60 * 60)))
MIN_CONTEXT_TOKENS = int(os.getenv("MIN_CONTEXT_TOKENS", "32768"))

# --- Whisper ---------------------------------------------------------------
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")

# --- Clipping defaults ------------------------------------------------------
DEFAULT_NUM_CLIPS = 5
DEFAULT_CLIP_MIN_SECONDS = 20.0
DEFAULT_CLIP_MAX_SECONDS = 45.0


class Settings:
    """Runtime settings, clamped to sane ranges so nothing can crash later."""

    def __init__(self, *, num_clips: int | None = None,
                 clip_min: float | None = None, clip_max: float | None = None) -> None:
        self.num_clips = _clamp_int(
            int(os.getenv("NUM_CLIPS", DEFAULT_NUM_CLIPS)) if num_clips is None else num_clips,
            1, 10,
        )
        lo = float(os.getenv("CLIP_MIN_SECONDS", DEFAULT_CLIP_MIN_SECONDS)) if clip_min is None else clip_min
        hi = float(os.getenv("CLIP_MAX_SECONDS", DEFAULT_CLIP_MAX_SECONDS)) if clip_max is None else clip_max
        lo = _clamp_float(lo, 5.0, 120.0)
        hi = _clamp_float(hi, lo, min(lo + 90.0, 120.0))
        self.clip_min_seconds = lo
        self.clip_max_seconds = hi
        self.whisper_model = (os.getenv("WHISPER_MODEL") or "base").strip() or "base"

        raw_primary = (os.getenv("PRIMARY_MODEL") or "").strip()
        self.primary_model: str | None = raw_primary or None
        raw_fallbacks = os.getenv("FALLBACK_MODELS") or ""
        self.fallback_models: list[str] = [
            m.strip() for m in re.split(r"[,\s]+", raw_fallbacks) if m.strip()
        ]


def api_key() -> str | None:
    """Return the OpenRouter API key (raw value, stripped) or None."""
    raw = os.getenv("OPENROUTER_API_KEY")
    return raw.strip() if raw else None


_PLACEHOLDER = re.compile(r"^(your_|changeme|placeholder|<)", re.IGNORECASE)


def key_ok() -> bool:
    """True when an API key is present and does not look like the placeholder."""
    key = api_key()
    return bool(key) and not _PLACEHOLDER.match(key)


def _clamp_float(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _clamp_int(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(value)))

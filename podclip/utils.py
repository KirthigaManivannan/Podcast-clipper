"""Small shared helpers: logging, paths, SRT time formatting, token estimate."""

from __future__ import annotations

import logging
import math
import re
import sys
import unicodedata
from pathlib import Path

from .config import CACHE_DIR, OUTPUTS_DIR

logger = logging.getLogger("podclip")


def setup_logging(level: int = logging.INFO) -> None:
    """Configure the root podclip logger once with a console handler."""
    root = logging.getLogger("podclip")
    if root.handlers:
        root.setLevel(level)
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False


def ensure_dir(path: Path) -> Path:
    """Create a directory (and parents) and return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    return ensure_dir(CACHE_DIR)


def outputs_dir() -> Path:
    return ensure_dir(OUTPUTS_DIR)


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token, adjusted for unicode)."""
    text = unicodedata.normalize("NFC", text or "")
    if not text:
        return 0
    return max(1, int(math.ceil(len(text) / 3.7)))


def seconds_to_srt_time(seconds: float) -> str:
    """Format a float number of seconds as ``HH:MM:SS,mmm`` for SRT."""
    total_ms = max(0, int(round(seconds * 1000)))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def srt_time_to_seconds(value: str) -> float:
    """Parse ``HH:MM:SS,mmm`` (or ``MM:SS``) back into seconds."""
    match = re.match(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})", value.strip())
    if match:
        h, m, s, ms = match.groups()
        return int(h) * 3600 + int(m) * 60 + int(s) + float(ms) / 10 ** len(ms)
    match = re.match(r"(\d+):(\d{2})(?:[,.](\d{1,3}))?", value.strip())
    if match:
        m, s = match.group(1), match.group(2)
        ms = (match.group(3) or "0")
        return int(m) * 60 + int(s) + float(ms) / 10 ** len(ms)
    raise ValueError(f"not an SRT timestamp: {value!r}")


_SRT_LINE_DURATION = 180.0  # ms shown per word, floor 1s, cap 5s


def srt_duration_for(text: str) -> float:
    """Recommended display duration for a caption line (in seconds)."""
    words = max(1, len(text.split()))
    return min(5.0, max(1.2, words * _SRT_LINE_DURATION / 1000.0))


def wrap_text(text: str, max_width: int) -> str:
    """Wrap a caption to ``max_width`` characters for SRT/PIL, honouring words."""
    words = text.split()
    if not words:
        return ""
    lines: list[str] = []
    current = ""
    for word in words:
        if len(word) > max_width:  # split long tokens (brackets, URLs)
            if current:
                lines.append(current)
                current = ""
            while len(word) > max_width:
                lines.append(word[:max_width])
                word = word[max_width:]
            current = word
        elif not current or len(current) + 1 + len(word) <= max_width:
            current = f"{current} {word}".strip()
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return "\n".join(lines)

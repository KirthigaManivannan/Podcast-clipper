"""Moment selection: chunk the transcript, ask the LLM per chunk, merge."""

from __future__ import annotations

import logging
from typing import Callable

from .llm import LLMClient, ask_with_fallback, parse_moment_items
from .prompts import SYSTEM_MOMENT_SELECTION, USER_MOMENT_SELECTION
from .transcribe import Segment, to_lines
from .utils import estimate_tokens, logger

logger = logging.getLogger("podclip.moments")

CHUNK_WINDOW_SECONDS = 600.0  # ~10 minutes
CHUNK_OVERLAP_SECONDS = 30.0
MAX_CHUNK_TOKENS = 5500  # keep every prompt far under ~6k tokens
MOMENTS_PER_CHUNK = 2


def chunk_segments(segments: list[Segment], *, window: float = CHUNK_WINDOW_SECONDS,
                   overlap: float = CHUNK_OVERLAP_SECONDS,
                   max_tokens: int = MAX_CHUNK_TOKENS) -> list[dict]:
    """Split segments into overlapping windows of ~``window`` seconds.

    Each chunk is a dict with ``start``, ``end`` and ``text`` lines. A window
    is closed (and a new one started with ``overlap`` seconds of tail) as soon
    as adding a segment would push it past ``window`` seconds or ``max_tokens``.
    """
    if not segments:
        return []
    chunks: list[dict] = []
    cursor = 0
    n = len(segments)

    while cursor < n:
        win_start = segments[cursor].start
        acc = 0
        j = cursor
        while j < n:
            seg = segments[j]
            tok = estimate_tokens(seg.text)
            if j > cursor and (seg.end - win_start > window or acc + tok > max_tokens):
                break
            acc += tok
            j += 1
        chunk_segs = segments[cursor:j] or [segments[cursor]]
        win_end = chunk_segs[-1].end
        chunks.append({
            "start": win_start,
            "end": win_end,
            "text": "\n".join(to_lines(chunk_segs)) + "\n",
        })
        # next window starts `overlap` before the previous end
        next_start = win_end - overlap
        cursor = j
        while cursor < n and segments[cursor].start < next_start:
            cursor += 1
    return chunks


def build_user_prompt(chunk: dict, min_s: float, max_s: float) -> str:
    return USER_MOMENT_SELECTION.format(
        chunk_start=chunk["start"],
        chunk_end=chunk["end"],
        lines=chunk["text"].rstrip("\n"),
        min_seconds=min_s,
        max_seconds=max_s,
    )


def collect_moments(client: LLMClient, model_ids: list[str], chunks: list[dict],
                    min_s: float, max_s: float,
                    progress: Callable[[str], None] | None = None,
                    models_used: set[str] | None = None) -> list[dict]:
    """Ask the LLM for moments over every chunk; return all validated items.

    A chunk failure is logged and skipped so one bad chunk never kills the run.
    """
    items: list[dict] = []
    used = models_used if models_used is not None else set()
    for i, chunk in enumerate(chunks, 1):
        if progress and len(chunks) > 1:
            progress(f"analysing chunk {i}/{len(chunks)}…")
        try:
            chunk_items, model = ask_with_fallback(
                client, model_ids,
                system=SYSTEM_MOMENT_SELECTION.format(
                    min_seconds=min_s, max_seconds=max_s),
                user=build_user_prompt(chunk, min_s, max_s),
                parse=lambda raw: parse_moment_items(
                    raw, lo=chunk["start"], hi=chunk["end"]),
                progress=progress,
            )
        except Exception as exc:  # noqa: BLE001 - keep going with other chunks
            logger.warning("chunk %d failed: %s", i, exc)
            continue
        used.add(model)
        items.extend(chunk_items)
    return items


def _overlaps(a: dict, b: dict, tol: float = 0.25) -> bool:
    return a["start"] < b["end"] - tol and b["start"] < a["end"] - tol


def merge_candidates(candidates: list[dict], num_clips: int) -> list[dict]:
    """Drop overlapping moments greedily by score; keep the top ``num_clips``."""
    if not candidates:
        return []
    pool = sorted(candidates, key=lambda c: (-c["score"], c["start"]))
    kept: list[dict] = []
    for cand in pool:
        if any(_overlaps(cand, other) for other in kept):
            continue
        kept.append(cand)
        if len(kept) >= num_clips:
            break
    return sorted(kept, key=lambda c: c["start"])

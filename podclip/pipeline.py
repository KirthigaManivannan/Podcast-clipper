"""The end-to-end Pipeline: source -> transcript -> moments -> clips."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import config
from .clipper import get_ffmpeg, make_zip, render_clip
from .config import Settings
from .download import AcquiredAudio, acquire_audio
from .llm import (
    LLMClient,
    ModelSelectionError,
    filter_healthy,
    select_models,
)
from .moments import chunk_segments, collect_moments, merge_candidates
from .transcribe import Segment, Transcriber, cached_transcribe
from .utils import ensure_dir, outputs_dir, logger

logger = logging.getLogger("podclip.pipeline")


class PipelineError(RuntimeError):
    """Friendly, user-facing pipeline error."""


STEPS = [
    "Locating ffmpeg",
    "Acquiring audio",
    "Transcribing (Whisper)",
    "Selecting free models",
    "Analysing moments",
    "Merging moments",
    "Cutting audio + SRT",
    "Rendering videos",
    "Writing summary",
]


class ProgressReporter:
    """Feed ``(step_number, total_steps, label)`` to a callback."""

    def __init__(self, cb: Callable[[int, int, str], None] | None = None,
                 total: int = len(STEPS)) -> None:
        self.cb = cb
        self.total = total
        self.step = 0

    def next(self, label: str) -> None:
        self.step += 1
        if self.cb:
            self.cb(min(self.step, self.total), self.total, label)


# ---------------------------------------------------------------------------
# Audio handle + run dir
# ---------------------------------------------------------------------------

def _run_name_from(acquired: AcquiredAudio) -> str:
    return acquired.run_name_base + "-" + datetime.now().strftime("%Y%m%d-%H%M%S")


def make_run_dir(acquired: AcquiredAudio) -> Path:
    return ensure_dir(outputs_dir() / _run_name_from(acquired))


# ---------------------------------------------------------------------------
# Boundary snapping
# ---------------------------------------------------------------------------

def snap_to_segments(segments: list[Segment], start: float, end: float) -> tuple[float, float]:
    """Snap a moment to lines: start at a sentence start, end at a sentence end.

    - Inside a segment: snap to that segment's start/end.
    - In a gap: snap forward to the next sentence's start (never mid-sentence).
    """
    if not segments:
        return start, end
    contain_start = [s for s in segments if s.start - 1e-6 <= start <= s.end + 1e-6]
    if contain_start:
        s_start = min(s.start for s in contain_start)
    else:
        nxt = [s for s in segments if s.start > start]
        s_start = min(s.start for s in nxt) if nxt else max(s.start for s in segments)

    contain_end = [s for s in segments if s.start - 1e-6 <= end <= s.end + 1e-6]
    if contain_end:
        s_end = max(s.end for s in contain_end)
    else:
        prev = [s for s in segments if s.end < end - 1e-6]
        s_end = max(s.end for s in prev) if prev else min(s.end for s in segments)
    return s_start, s_end


def enforce_length(segments: list[Segment], start: float, end: float,
                   min_d: float, max_d: float, total: float) -> tuple[float, float]:
    """Bring [start, end] inside [min_d, max_d] using only segment boundaries."""
    if not segments:
        return max(0.0, start), min(max(start + max_d, end), total)
    ends = sorted({s.end for s in segments})
    starts = sorted({s.start for s in segments})
    start = max(0.0, min(start, max(0.0, total)))
    end = min(end, total) if end <= total else total

    if end - start > max_d:
        # trim end down to a boundary that keeps [min, max]
        cut_candidates = [ce for ce in ends if min_d - 1e-6 <= ce - start <= max_d + 1e-6]
        if cut_candidates:
            end = max(cut_candidates)
        else:
            end = start + max_d
    if end - start < min_d:
        # extend end
        for ce in ends:
            if ce - start >= min_d - 1e-6:
                end = ce
                break
        else:
            end = min(total, start + min_d)
    if end - start > max_d:
        end = start + max_d
    if start + min_d > total:
        start = max(0.0, total - min_d)
    return start, end


def snap_and_clamp(segments: list[Segment], start: float, end: float,
                   min_d: float, max_d: float, total: float) -> tuple[float, float]:
    """Full pipeline: snap, then clamp to min/max while staying on boundaries."""
    s, e = snap_to_segments(segments, start, end)
    return enforce_length(segments, s, e, min_d, max_d, total)


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def run_pipeline(
    source: str,
    *,
    settings: Settings | None = None,
    llm_client: LLMClient | None = None,
    transcriber: Transcriber | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict:
    """Run the whole pipeline for ``source`` (local file path or URL).

    Returns the summary dict with ``run_dir`` and ``clips``.
    """
    st = settings or Settings()
    prog = ProgressReporter(progress)
    logger.info("podclip run starting for %s", source)

    if llm_client is None and not config.key_ok():
        raise PipelineError(
            "OPENROUTER_API_KEY is missing or still the placeholder. "
            f"Open {config.ENV_FILE.resolve()} and paste your key after "
            "OPENROUTER_API_KEY=, then try again."
        )

    prog.next("Locating ffmpeg")
    try:
        ffmpeg = get_ffmpeg()
    except RuntimeError as exc:
        raise PipelineError(str(exc)) from exc

    prog.next("Acquiring audio")
    try:
        acquired = acquire_audio(source, ffmpeg)
    except RuntimeError as exc:
        raise PipelineError(f"audio acquisition failed: {exc}") from exc
    run_dir = make_run_dir(acquired)
    logger.info("run directory: %s", run_dir)

    # --- transcribe --------------------------------------------------------
    prog.next("Transcribing")
    tr = transcriber or Transcriber(st.whisper_model)
    try:
        segments = cached_transcribe(acquired.wav_path, tr)
    except Exception as exc:  # noqa: BLE001 - wrap Whisper issues
        raise PipelineError(
            f"transcription failed with model {st.whisper_model!r}: {exc}"
        ) from exc
    if not segments:
        raise PipelineError(
            "no speech could be transcribed (empty transcript). The file may be "
            "silent, instrumental-only, or in a language Whisper cannot parse. "
            "Try another source."
        )
    total_dur = max(s.end for s in segments)

    # --- model selection ---------------------------------------------------
    prog.next("Selecting free models")
    client = llm_client
    models_used: set[str] = set()
    candidates: list[str] = []
    if client is None:
        client = LLMClient(config.api_key())
        try:
            candidates = select_models(
                config.api_key() or "",
                primary_model=st.primary_model,
                fallback_models=tuple(st.fallback_models),
            )
            candidates = filter_healthy(candidates=candidates, client=client)
        except ModelSelectionError as exc:
            raise PipelineError(str(exc)) from exc
        if not candidates:
            raise PipelineError(
                "no candidate models passed the health check. Free models on "
                "OpenRouter are often rate-limited; wait a moment and retry."
            )
    else:
        # Injected/mock clients drive their own behaviour; give a placeholder id.
        candidates = ["mock/model"]

    # --- moments -----------------------------------------------------------
    prog.next("Analysing moments")
    chunks = chunk_segments(segments)
    all_items = collect_moments(
        client, candidates, chunks,
        min_s=st.clip_min_seconds, max_s=st.clip_max_seconds,
        models_used=models_used,
    )
    if not all_items:
        raise PipelineError(
            "no valid moments were found in this transcript. The episode may be "
            "too quiet/monotone for good clips, or the models could not parse "
            "the transcript. Try a different episode."
        )
    best = merge_candidates(all_items, st.num_clips)

    # --- clips -------------------------------------------------------------
    clips: list[dict] = []
    for i, m in enumerate(sorted(best, key=lambda x: x["start"]), 1):
        start, end = snap_and_clamp(
            segments, m["start"], m["end"],
            st.clip_min_seconds, st.clip_max_seconds, total_dur,
        )
        if end <= start:
            continue
        files = render_clip(ffmpeg, acquired.wav_path, run_dir, i,
                            start, end, segments)
        clips.append({
            "index": i,
            "start": round(start, 2),
            "end": round(end, 2),
            "duration": round(end - start, 2),
            "score": m["score"],
            "reason": m["reason"],
            "quote": m["quote"],
            "files": sorted(Path(f).name for f in files.values()),
            "has_video": "mp4" in files,
        })

    # --- summary -----------------------------------------------------------
    prog.next("Writing summary")
    summary = {
        "run_name": run_dir.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "input": source,
            "title": acquired.title,
            "wav": str(acquired.wav_path),
            "run_name_base": acquired.run_name_base,
        },
        "settings": {
            "num_clips": st.num_clips,
            "clip_min_seconds": st.clip_min_seconds,
            "clip_max_seconds": st.clip_max_seconds,
            "whisper_model": st.whisper_model,
            "models_used": sorted(models_used) or candidates,
            "total_duration_s": round(total_dur, 2),
        },
        "clips": clips,
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (run_dir / "summary.md").write_text(summary_md(summary), encoding="utf-8")
    zip_path = make_zip(run_dir, run_dir.name)
    summary["zip"] = str(zip_path)
    summary["run_dir"] = str(run_dir)
    logger.info("pipeline complete -> %s", run_dir)
    return summary


def summary_md(summary: dict) -> str:
    """Render the summary dict as a readable markdown file."""
    lines = [
        f"# PodClip — {summary['run_name']}",
        "",
        f"- **Created:** {summary['created_at']}",
        f"- **Source:** {summary['source']['input']}",
        f"- **Title:** {summary['source']['title']}",
        f"- **Total duration:** {summary['settings']['total_duration_s']:.1f}s",
        f"- **Whisper model:** {summary['settings']['whisper_model']}",
        f"- **Models used:** {', '.join(summary['settings']['models_used'])}",
        "",
        "## Clips",
        "",
    ]
    for clip in summary["clips"]:
        lines += [
            f"### {clip['index']}. \"{clip['quote']}\"",
            "",
            f"- **Time:** {clip['start']}s – {clip['end']}s "
            f"({clip['duration']}s)",
            f"- **Score:** {clip['score']}/10",
            f"- **Why:** {clip['reason']}",
            f"- **Files:** {', '.join(clip['files'])}",
            "",
        ]
    return "\n".join(lines).strip() + "\n"

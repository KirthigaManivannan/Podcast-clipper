"""Local transcription via faster-whisper, with JSON caching on disk.

Transcription is the slowest step, so results are cached by content hash of
the normalised WAV. Reruns (same audio, same model) skip it entirely.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from typing import Callable

from .config import WHISPER_COMPUTE_TYPE
from .download import content_hash
from .utils import cache_dir, ensure_dir, logger

logger = logging.getLogger("podclip.transcribe")


@dataclass(frozen=True)
class Segment:
    """One Whisper speech segment."""

    start: float
    end: float
    text: str

    def to_list(self) -> list[float | str]:
        return [round(self.start, 3), round(self.end, 3), self.text]

    @classmethod
    def from_list(cls, row: list) -> "Segment":
        return cls(float(row[0]), float(row[1]), str(row[2]))


def segment_to_seconds(seg: Segment) -> tuple[float, float]:
    return seg.start, seg.end


class Transcriber:
    """Wraps faster-whisper lazily (the model downloads on first use)."""

    def __init__(self, model_name: str = "base", compute_type: str = WHISPER_COMPUTE_TYPE) -> None:
        self.model_name = model_name
        self.compute_type = compute_type
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "faster-whisper is not installed (pip install faster-whisper)."
                ) from exc
            logger.info("loading faster-whisper model %r (first run downloads it)…",
                        self.model_name)
            self._model = WhisperModel(
                self.model_name, device="cpu", compute_type=self.compute_type
            )
        return self._model

    def transcribe(self, wav_path, progress: Callable[[str], None] | None = None) -> list[Segment]:
        model = self._ensure_model()
        if progress:
            progress("transcribing with faster-whisper (this can take a while)…")
        segments, _info = model.transcribe(str(wav_path), beam_size=5, language=None)
        out: list[Segment] = []
        for seg in segments:
            text = " ".join((seg.text or "").split())
            if text:
                out.append(Segment(seg.start, seg.end, text))
        return out


def cached_transcribe(wav_path, transcriber: Transcriber,
                      progress: Callable[[str], None] | None = None) -> list[Segment]:
    """Transcribe with on-disk caching; skipped when a fresh cache exists."""
    cache_file = (
        ensure_dir(cache_dir() / "transcripts")
        / f"{content_hash(wav_path)}-{transcriber.model_name}.json"
    )
    if cache_file.exists():
        logger.info("reusing cached transcript: %s", cache_file.name)
        try:
            rows = json.loads(cache_file.read_text(encoding="utf-8"))
            return [Segment.from_list(r) for r in rows]
        except (OSError, ValueError, IndexError, TypeError) as exc:
            logger.warning("cached transcript unreadable (%s); re-transcribing", exc)

    segments = transcriber.transcribe(wav_path, progress=progress)
    if segments:  # never cache an empty transcript (silent/instrumental file)
        tmp = cache_file.with_suffix(".tmp.json")
        tmp.write_text(
            json.dumps([s.to_list() for s in segments], ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(cache_file)
    logger.info("transcribed %d segments", len(segments))
    return segments


def to_lines(segments: list[Segment]) -> str:
    """Render segments as timestamped lines for the LLM prompt."""
    return "\n".join(
        f"[{s.start:07.1f}-{s.end:07.1f}] {s.text}" for s in segments
    )

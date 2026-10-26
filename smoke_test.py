"""Offline end-to-end smoke test.

Synthesises a 90-second tone with the bundled ffmpeg, injects a fake
transcript + fake LLM, runs the full pipeline, and asserts all clip files
(mp3/srt/mp4) and summaries exist. No network, no API key required.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from podclip.clipper import get_ffmpeg  # noqa: E402
from podclip.config import Settings  # noqa: E402
from podclip.download import AcquiredAudio  # noqa: E402
from podclip.pipeline import run_pipeline  # noqa: E402
from podclip.transcribe import Segment  # noqa: E402


def make_tone() -> Path:
    from podclip.config import PROJECT_DIR
    exe = get_ffmpeg()
    out = (PROJECT_DIR / ".smoke").mkdir(exist_ok=True, parents=True)
    wav = PROJECT_DIR / ".smoke" / "tone.wav"
    cmd = [exe, "-y", "-loglevel", "error",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=90",
           "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)]
    subprocess_run(cmd)
    return wav


def subprocess_run(cmd):
    import subprocess
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"ffmpeg failed: {r.stderr}")
    return r


class FakeTranscriber:
    """Whisper stand-in: a 90-second podcast-ish transcript."""

    def transcribe(self, wav_path, progress=None):
        texts = [
            "This is a great episode about starting a company.",
            "Our first product was a total failure and we learned everything from it.",
            "The founder said that marketing is the only game in town.",
            "We raised money by accident, honestly, by just shipping.",
            "Nobody tells you that the hardest part is saying no to ideas.",
            "Run your experiments quickly, and the surprises will follow.",
            "We reused that insight a hundred times and it always worked.",
            "That moment taught me that teams matter more than ideas.",
            "The simplest advice: talk to users every single week.",
            "If I had to do it again, I would do it in a weekend.",
            "Great ideas hide in the complaints of your own users.",
            "We turned our biggest bug into our most loved feature.",
            "I still remember the day we broke the database in front of investors.",
            "Shipping weekly feels slow until you realize the year is long.",
            "Every successful founder I know started by answering one question.",
            "And that question is: who is this really for?",
            "Keep the clips short and let the quote do the heavy lifting.",
            "That is the whole episode. Thanks for listening.",
        ]
        segments = []
        for i, t in enumerate(texts):
            s = i * 5.0
            e = s + 4.5
            segments.append(Segment(s, e, t))
        return segments

    @property
    def model_name(self):
        return "fake"


class FakeLLM:
    """LLM stand-in: answers every moment prompt with a canned JSON array."""

    def complete(self, model_id, *, system, user, max_tokens=1500, temperature=0.2):
        return (
            '[{"start": 5, "end": 30, "score": 9, "reason": "great quote", '
            '"quote": "Our first product was a total failure and we learned everything from it."},'
            '{"start": 50, "end": 75, "score": 8, "reason": "insight", '
            '"quote": "Nobody tells you that the hardest part is saying no to ideas."},'
            '{"start": 20, "end": 45, "score": 7, "reason": "win", '
            '"quote": "Run your experiments quickly, and the surprises will follow."},'
            '{"start": 70, "end": 85, "score": 6, "reason": "second", '
            '"quote": "Every successful founder I know started by answering one question."}]'
        )


def make_fake_acquire(wav: Path):
    def _fake_acquire(source, ffmpeg_exe):
        return AcquiredAudio(
            wav_path=wav, title="sample podcast", is_url=False,
            source=str(source), download_dir=None,
        )
    return _fake_acquire


def main() -> int:
    print("[smoke] 1/5 generating sample tone (ffmpeg)…")
    wav = make_tone()

    print("[smoke] 2/5 running pipeline with fake transcript + fake LLM…")
    try:
        summary = run_pipeline(
            str(wav),
            settings=Settings(num_clips=5, clip_min=20, clip_max=45),
            llm_client=FakeLLM(),
            transcriber=FakeTranscriber(),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[smoke] FAILED during pipeline: {exc}", file=sys.stderr)
        return 1

    print("[smoke] 3/5 checking outputs…")
    run_dir = Path(summary["run_dir"])
    clips = summary["clips"]
    print(f"        run_dir={run_dir}  clips={len(clips)}")
    if len(clips) < 1:
        print("[smoke] FAILED: no clips produced", file=sys.stderr)
        return 1
    failures: list[str] = []
    for clip in clips:
        for suffix in ("mp3", "srt", "mp4"):
            p = run_dir / f"clip{clip['index']}.{suffix}"
            if not p.exists() or p.stat().st_size == 0:
                failures.append(str(p))
    if failures:
        print("[smoke] FAILED: missing/empty files: " + ", ".join(failures), file=sys.stderr)
        return 1
    for name in ("summary.json", "summary.md", f"{run_dir.name}.zip"):
        if not (run_dir / name).exists():
            failures.append(str(run_dir / name))

    print("[smoke] 4/5 verifying mp4 is a valid video…")
    exe = get_ffmpeg()
    import subprocess
    probe = subprocess.run(
        [exe, "-i", str(run_dir / f"clip1.mp4")], capture_output=True, text=True,
    )
    if "Video:" not in probe.stderr or "Audio:" not in probe.stderr:
        failures.append("clip1.mp4 not a valid media file")

    if failures:
        print("[smoke] FAILED: " + "; ".join(failures), file=sys.stderr)
        return 1

    print("[smoke] 5/5 success!")
    print(f"        outputs in: {run_dir}")
    for clip in clips:
        print(f"        clip{clip['index']}: {clip['start']}s-{clip['end']}s "
              f"score {clip['score']}  video={'yes' if clip['has_video'] else 'no'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

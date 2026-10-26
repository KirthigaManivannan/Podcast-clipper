"""Tests for SRT writing/parsing and time formatting (offline)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from podclip.clipper import parse_srt, write_srt  # noqa: E402
from podclip.transcribe import Segment  # noqa: E402
from podclip.utils import (  # noqa: E402
    seconds_to_srt_time,
    srt_time_to_seconds,
    wrap_text,
    estimate_tokens,
)


def test_seconds_to_srt_time():
    assert seconds_to_srt_time(0) == "00:00:00,000"
    assert seconds_to_srt_time(61.5) == "00:01:01,500"
    assert seconds_to_srt_time(3661.0) == "01:01:01,000"


def test_srt_time_roundtrip():
    assert srt_time_to_seconds("00:00:12,340") == 12.34
    assert srt_time_to_seconds("00:01:01,500") == 61.5
    assert srt_time_to_seconds("01:01:01,500") == 3661.5


def test_wrap_text_keeps_words():
    assert wrap_text("a " * 30, 20).splitlines()[0].endswith("a")

def test_estimate_tokens_nonzero():
    assert estimate_tokens("") == 0
    assert estimate_tokens("word " * 50) > 5


def test_write_srt_rebases_and_clamps(tmp_path):
    segs = [
        Segment(10, 12, "First line."),
        Segment(14.5, 16, "Second line."),   # separate caption (2.5s gap)
        Segment(50, 55, "Outside the clip."),
    ]
    out = tmp_path / "clip.srt"
    written = write_srt(segs, clip_start=10, clip_end=16, path=out)
    assert len(written) == 2
    assert written[0][0] == 0.0
    assert written[0][2] == "First line."
    assert written[1][0] == 4.5        # 14.5 - 10, rebased
    assert written[1][1] == 6.0        # 16 - 10, clamped to clip end

    parsed = parse_srt(out.read_text(encoding="utf-8"))
    assert len(parsed) == 2
    assert parsed[0][1] == 2.0
    assert parsed[1][0] == 4.5


def test_write_srt_merges_close_captions(tmp_path):
    segs = [
        Segment(10, 12.2, "A"),
        Segment(12.4, 15, "B"),
    ]
    out = tmp_path / "clip.srt"
    written = write_srt(segs, clip_start=10, clip_end=15, path=out)
    # gap 0.2s < 0.6s -> merged into a single caption
    assert len(written) == 1
    assert "A" in written[0][2] and "B" in written[0][2]


def test_write_srt_wraps_long_lines(tmp_path):
    long_text = " ".join("word" for _ in range(40))
    segs = [Segment(10, 16, long_text)]
    out = tmp_path / "clip.srt"
    written = write_srt(segs, clip_start=10, clip_end=16, path=out)
    # wrap width 72 chars -> lines merged (gap 6s won't merge) so single caption,
    # but body is wrapped into multiple physical lines
    assert len(written) == 1
    assert "\n" in written[0][2]


def test_parse_srt_tolerates_noise():
    text = ("1\n00:00:00,000 --> 00:00:02,000\nHello\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\nWorld\n\n")
    parsed = parse_srt(text)
    assert len(parsed) == 2
    assert parsed[1][2] == "World"

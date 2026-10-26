"""Tests for moment merging, overlapping, and boundary snapping (offline)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from podclip.moments import merge_candidates  # noqa: E402
from podclip.pipeline import (  # noqa: E402
    snap_and_clamp,
    snap_to_segments,
    enforce_length,
)
from podclip.transcribe import Segment  # noqa: E402


def _segs(*pairs):
    return [Segment(s, e, f"line {i}") for i, (s, e) in enumerate(pairs)]


# --- snapping to boundaries -------------------------------------------------

def test_snap_to_segments_exact_boundaries():
    segs = _segs((0, 5), (5, 10), (10, 15))
    assert snap_to_segments(segs, 1, 9) == (0, 10)


def test_snap_start_in_gap_uses_next_sentence_start():
    segs = _segs((0, 5), (8, 12))
    s, _ = snap_to_segments(segs, 6.5, 10.5)
    assert s == 8.0  # never start mid-gap/silence


def test_snap_end_in_gap_uses_previous_sentence_end():
    segs = _segs((0, 5), (8, 12))
    _, e = snap_to_segments(segs, 2.0, 7.0)
    assert e == 5.0


def test_snap_empty_segments_identity():
    assert snap_to_segments([], 3, 9) == (3, 9)


# --- length enforcement ------------------------------------------------------

def test_enforce_min_extends_to_boundary():
    segs = _segs((0, 5), (5, 10), (10, 15))
    s, e = enforce_length(segs, 0, 6, min_d=8, max_d=10, total=15)
    assert e == 10.0
    assert e - s >= 8


def test_enforce_max_trims_to_boundary():
    segs = _segs((0, 5), (5, 10), (10, 15), (15, 20))
    s, e = enforce_length(segs, 0, 20, min_d=3, max_d=8, total=20)
    assert e - s <= 8
    assert e in (5.0, 10.0)


def test_enforce_max_clamps_when_no_boundary_fits():
    segs = _segs((0, 5), (5, 10), (10, 15))
    s, e = enforce_length(segs, 1, 15, min_d=2, max_d=4, total=15)
    assert e - s <= 4


def test_snap_and_clamp_to_total():
    segs = _segs((0, 5), (5, 10), (10, 15))
    s, e = snap_and_clamp(segs, 1, 14, min_d=20, max_d=20, total=30)
    assert e <= 30


# --- merging / overlaps ------------------------------------------------------

def _cand(start, end, score, quote):
    return {"start": start, "end": end, "score": score, "quote": quote}


def test_merge_overlaps_highest_score_wins():
    cands = [
        _cand(0, 10, 5, "low"),
        _cand(8, 18, 9, "high"),
        _cand(30, 40, 7, "separate"),
    ]
    kept = merge_candidates(cands, 5)
    assert len(kept) == 2
    assert kept[0]["quote"] == "high"
    assert kept[1]["quote"] == "separate"


def test_merge_keeps_both_non_overlapping_sorted():
    cands = [
        _cand(40, 50, 4, "b"),
        _cand(0, 10, 4, "a"),
    ]
    kept = merge_candidates(cands, 5)
    assert kept[0]["quote"] == "a"
    assert kept[1]["quote"] == "b"


def test_merge_identical_dedupe():
    cands = [_cand(0, 10, 5, "dup"), _cand(0, 10, 5, "dup")]
    assert len(merge_candidates(cands, 5)) == 1


def test_merge_respects_num_clips():
    cands = [_cand(0, 10, 1, "a"), _cand(20, 30, 1, "b"),
             _cand(40, 50, 1, "c"), _cand(60, 70, 1, "d")]
    assert len(merge_candidates(cands, 2)) == 2


def test_merge_empty():
    assert merge_candidates([], 5) == []

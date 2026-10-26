"""Tests for robust JSON extraction + moment item validation (offline)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from podclip.llm import extract_json, parse_moment_items  # noqa: E402


def test_parse_clean_array():
    raw = ('[{"start": 10.0, "end": 30.0, "score": 8, '
           '"reason": "hot take", "quote": "Hello world"}]')
    parsed = extract_json(raw)
    assert isinstance(parsed, list)
    assert parsed[0]["start"] == 10.0


def test_parse_with_markdown_fence():
    raw = '```json\n[{"start": 1, "end": 5, "score": 6, "quote": "q"}]\n```'
    parsed = extract_json(raw)
    assert isinstance(parsed, list) and parsed[0]["end"] == 5


def test_parse_with_leading_prose():
    raw = ('Sure! Here are the moments:\n'
           '[{"start": 1.5, "end": 4.5, "score": 7, "quote": "A", "reason": "x"}]\n'
           "Hope that helps!")
    parsed = extract_json(raw)
    assert parsed[0]["quote"] == "A"


def test_parse_skips_thinking_blocks():
    raw = ('<thinking>let me think carefully…</thinking>'
           '[{"start": 0, "end": 2, "score": 9, "quote": "Y"}]')
    parsed = extract_json(raw)
    assert parsed[0]["quote"] == "Y"


def test_parse_object_envelope():
    raw = '{"candidates": [{"start": 0, "end": 3, "score": 5, "quote": "Z"}]}'
    parsed = extract_json(raw)
    assert isinstance(parsed, dict)
    assert parsed["candidates"][0]["quote"] == "Z"


def test_parse_invalid_returns_none():
    assert extract_json("no json here at all") is None
    assert extract_json('{"start": }') is None


def test_parse_picks_first_array():
    raw = ('[{"start": 1, "end": 2, "score": 1, "quote": "first"}]\n'
           '[{"start": 9, "end": 10, "score": 9, "quote": "second"}]')
    judged = extract_json(raw)
    assert judged == [{"start": 1, "end": 2, "score": 1, "quote": "first"}]


def test_parse_moment_items_filters_bad():
    raw = ('[{"start": 0, "end": 10, "score": 8, "quote": "ok"},'
           '{"start": "x", "end": 5, "score": 3, "quote": "bad type"},'
           '{"start": 2, "end": 1, "score": 3, "quote": "inverted"},'
           '{"start": 5, "end": 8, "score": 3, "quote": "nice", "reason": "r"},'
           '{"start": 12, "end": 14, "score": 3, "quote": "out of range"}]')
    items = parse_moment_items(raw, lo=0, hi=10)
    assert len(items) == 2
    assert items[0]["quote"] == "ok"
    assert items[1]["quote"] == "nice"


def test_parse_moment_items_string_numbers():
    raw = '[{"start": "1.0", "end": "2.0", "score": "9", "quote": "q"}]'
    items = parse_moment_items(raw)
    assert items[0]["start"] == 1.0 and items[0]["end"] == 2.0
    assert items[0]["score"] == 9.0


def test_parse_moment_items_score_bounds():
    raw = '[{"start": 0, "end": 5, "score": 42, "quote": "high"},' \
          '{"start": 5, "end": 9, "score": -5, "quote": "low"}]'
    items = parse_moment_items(raw)
    assert items[0]["score"] == 10.0
    assert items[1]["score"] == 1.0


def test_parse_moment_items_no_quote_discarded():
    assert parse_moment_items('[{"start": 0, "end": 1, "score": 5}]') == []

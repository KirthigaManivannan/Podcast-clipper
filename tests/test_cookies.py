"""Tests for cookie resolution (offline, hermetic: no real browser touches)."""

from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from podclip import cookie_jar  # noqa: E402


def _write_cookies(tmp_path: Path, name: str = "cookies.txt") -> Path:
    p = tmp_path / name
    p.write_text("# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tFALSE\t0\tfoo\tbar\n", encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure no cookie env vars leak between tests."""
    monkeypatch.delenv("COOKIES_B64", raising=False)
    monkeypatch.delenv("COOKIES_FILE", raising=False)
    monkeypatch.setattr(cookie_jar, "DEFAULT_CONF", Path(__file__).resolve().parent.parent / ".config" / "cookies.txt")
    yield


def test_cookie_opts_none_when_nothing_configured(monkeypatch):
    monkeypatch.setattr(cookie_jar, "DEFAULT_CONF", Path(str(Path(__file__).parent / "no_such_config")).joinpath("cookies.txt"))
    monkeypatch.delenv("COOKIES_B64", raising=False)
    monkeypatch.delenv("COOKIES_FILE", raising=False)
    # Browser extraction is a last resort and won't run with no sources configured;
    # here it must not be attempted (hermetic test).
    monkeypatch.setattr(cookie_jar, "_probe_browser", lambda b: None)
    assert cookie_jar.cookie_file() is None
    assert cookie_jar.cookie_opts() is None


def test_cookie_file_from_b64(monkeypatch, tmp_path):
    data = "# Netscape HTTP Cookie File\n.a\tTRUE\t/\tFALSE\t0\tx\ty\n"
    monkeypatch.setenv("COOKIES_B64", base64.b64encode(data.encode()).decode())
    path = cookie_jar.cookie_file()
    assert path is not None
    assert path.name == "podclip_cookies.txt"
    assert path.read_text(encoding="utf-8") == data
    assert cookie_jar.cookie_opts() == {"cookiefile": str(path)}


def test_cookie_file_prefers_b64_over_file(monkeypatch, tmp_path):
    other = _write_cookies(tmp_path, "other.txt")
    monkeypatch.setenv("COOKIES_FILE", str(other))
    monkeypatch.setenv("COOKIES_B64", base64.b64encode(b"a\tb").decode())
    path = cookie_jar.cookie_file()
    assert path is not None
    assert "podclip_cookies" in path.name  # b64 wins


def test_cookie_file_from_env_file(monkeypatch, tmp_path):
    p = _write_cookies(tmp_path)
    monkeypatch.setenv("COOKIES_FILE", str(p))
    assert cookie_jar.cookie_file() == p
    assert cookie_jar.cookie_opts() == {"cookiefile": str(p)}


def test_cookie_file_missing_env_file_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("COOKIES_FILE", str(tmp_path / "nope.txt"))
    assert cookie_jar.cookie_file() is None


def test_cookie_file_from_config_dir(monkeypatch, tmp_path):
    conf = tmp_path / ".config" / "cookies.txt"
    conf.parent.mkdir()
    conf.write_text("x", encoding="utf-8")
    monkeypatch.setattr(cookie_jar, "DEFAULT_CONF", conf)
    assert cookie_jar.cookie_file() == conf
    assert cookie_jar.cookie_opts() == {"cookiefile": str(conf)}


def test_cookie_file_browser_last_resort(monkeypatch, tmp_path):
    # No env, no config -> _probe_browser returns a temp file
    fake = tmp_path / "probe.txt"
    fake.write_text("x", encoding="utf-8")
    monkeypatch.setattr(cookie_jar, "_probe_browser", lambda b: str(fake))
    monkeypatch.setattr(cookie_jar, "DEFAULT_CONF", tmp_path / "no_config" / "cookies.txt")
    assert cookie_jar.cookie_file() == fake
    assert cookie_jar.cookie_opts() == {"cookiefile": str(fake)}


def test_cookie_b64_bad_decodes_ignored(monkeypatch):
    monkeypatch.setenv("COOKIES_B64", "!!not-base64!!")
    p = cookie_jar.cookie_file()
    assert p is None or not p.name == "podclip_cookies.txt"

"""Cookie resolution for authenticated downloads.

YouTube bot-blocks datacenter/cloud IPs (the "sign in to confirm you're not a
bot" error is very common on Render, Railway, etc.).  Shipping a real browser's
Netscape-format ``cookies.txt`` to the app lets yt-dlp present a logged-in
session that passes the bot-check.

Resolution priority (highest wins), mirroring ``download.py``'s original env
vars but adding a laptop-friendly automatic fallback and a last-resort browser
extraction:

1. ``COOKIES_B64``   — base64-encoded contents of a cookies.txt (best for
   platforms like Render where uploading a file is awkward).
2. ``COOKIES_FILE``  — explicit path to a cookies.txt.
3. ``.config/cookies.txt`` — the file ``scripts/export_cookies.py`` writes on
   your laptop; picked up with no env vars needed.
4. Browser extraction — via ``yt_dlp.cookies.extract_cookies_from_browser``
   (Chrome/Edge/Firefox), last resort so an ecosystem config file is never
   silently overridden by an explicit cookie source.

The host file for RTD = the plain API: ``cookie_file()`` returns the resolved
path (or None) and ``cookie_opts()`` builds the ``yt_dlp`` opts dict from it.
"""

from __future__ import annotations

import base64
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger("podclip.cookies")

DEFAULT_CONF = Path(__file__).resolve().parent.parent / ".config" / "cookies.txt"

# Browsers yt-dlp can read cookies from directly (must own real local user data).
_SUPPORTED_BROWSERS = (
    "chrome", "chromium", "edge", "brave", "opera", "vivaldi", "firefox", "safari",
)


def _decode_b64(value: str) -> str | None:
    """Decode a base64 cookies payload; return None on user error."""
    try:
        return base64.b64decode(value).decode("utf-8")
    except Exception:  # noqa: BLE001 - bad base64 is a user typo, not a crash
        logger.warning("COOKIES_B64 could not be decoded (not valid base64); ignoring")
        return None


def _probe_browser(browser: str) -> str | None:
    """Try to ask yt-dlp for one browser's cookies (no options). Returns a path on success."""
    try:
        from yt_dlp.cookies import extract_cookies_from_browser
    except ImportError:
        return None
    try:
        mozilla = extract_cookies_from_browser(browser)
        if not mozilla:
            return None
        # Persist to a temp file so the caller can hand a path to yt-dlp.
        fd, path = tempfile.mkstemp(prefix="podclip_browser_cookies_", suffix=".txt")
        os.close(fd)
        mozilla.save(path, ignore_discard=True, ignore_expires=True)
        if os.path.getsize(path) == 0:
            os.remove(path)
            return None
        return path
    except Exception as exc:  # noqa: BLE001 - browser profile may be locked/etc.
        logger.debug("browser cookie extraction (%s) failed: %s", browser, exc)
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except NameError:
            pass
        return None


def cookie_file() -> Path | None:
    """Return the path to a cookies.txt for yt-dlp, or None if none available.

    Priority: ``COOKIES_B64`` → ``COOKIES_FILE`` → ``.config/cookies.txt``
    → a browser extraction (last resort, so an explicit config is never
    overridden).
    """
    raw_b64 = os.getenv("COOKIES_B64", "").strip()
    if raw_b64:
        decoded = _decode_b64(raw_b64)
        if decoded:
            cp = Path(tempfile.gettempdir()) / "podclip_cookies.txt"
            try:
                cp.write_text(decoded, encoding="utf-8")
            except OSError as exc:  # noqa: BLE001 - temp write failure is rare
                logger.warning("could not write decoded COOKIES_B64 to %s: %s", cp, exc)
            else:
                logger.info("using cookies from COOKIES_B64 (%d bytes)", len(decoded))
                return cp
        return None  # invalid b64 -> don't fall through to other sources

    raw_file = os.getenv("COOKIES_FILE", "").strip()
    if raw_file:
        cp = Path(raw_file).expanduser()
        if cp.is_file():
            logger.info("using cookies from COOKIES_FILE: %s", cp)
            return cp
        logger.warning("COOKIES_FILE set but not found: %s", cp)
        return None  # explicit but broken -> error rather than silently use browser

    if DEFAULT_CONF.is_file():
        logger.info("using cookies from %s", DEFAULT_CONF)
        return DEFAULT_CONF

    # Last resort: pull from an installed browser so local runs keep working
    # even with no exported file.  Fallbacks are opt-out by preferring any
    # explicit source above; this only runs when none was configured.
    for browser in _SUPPORTED_BROWSERS:
        hit = _probe_browser(browser)
        if hit:
            logger.info("using cookies extracted from %s (%s)", browser, hit)
            return Path(hit)
    logger.debug("no cookie source found (browsers yielded nothing)")
    return None


def cookie_opts() -> dict | None:
    """Build the ``yt_dlp`` opts dict carrying cookies, or None to stay anonymous."""
    cp = cookie_file()
    if cp is None:
        return None
    if not cp.exists():
        logger.warning("cookie file vanished (shouldn't happen): %s", cp)
        return None
    return {"cookiefile": str(cp)}

#!/usr/bin/env python3
"""Export your browser's cookies to PodClip's config file (and print COOKIES_B64).

Why: YouTube bot-blocks datacenter/cloud IPs ("Sign in to confirm you're not a
bot").  Deployed apps (Render, Railway, …) live on those IPs, so yt-dlp needs a
logged-in browser session to pass.  Run this ONCE on the machine where you
browse YouTube:

    .venv\\Scripts\\python scripts\\export_cookies.py         # Windows
    .venv/bin/python scripts/export_cookies.py                # macOS/Linux

It writes ``.config/cookies.txt`` (Netscape format, git-ignored) which PodClip
picks up automatically on this machine, and prints a ``COOKIES_B64=...`` line
you can paste into Render's environment variables (or set COOKIES_FILE to the
file's path).

Cookies expire: YouTube sessions eventually lapse and the bot-check returns.
To refresh, just re-run this script and update Render's COOKIES_B64.

Usage:
  - BRACE/BROWSER: auto-detect from a list, or pass --browser edge/firefox/...
  - --format        only print the COOKIES_B64 value (for piping into Render)
  - --profile       optional browser profile name
  - --no-config     skip writing .config/cookies.txt (only print b64)
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONF_DIR = PROJECT_DIR / ".config"
CONF_FILE = CONF_DIR / "cookies.txt"

BROWSERS = ("chrome", "edge", "firefox", "brave", "opera", "vivaldi", "chromium", "safari")


def _extract_cookies(browser: str, profile: str | None) -> "object":
    from yt_dlp.cookies import extract_cookies_from_browser

    return extract_cookies_from_browser(browser, profile=profile)


def _first_working_browser(profile: str | None) -> tuple[str, "object"]:
    """Try browsers in order; return the first that yields cookies."""
    last_err: Exception | None = None
    for b in BROWSERS:
        try:
            jar = _extract_cookies(b, profile)
        except Exception as exc:  # noqa: BLE001 - one failing browser is not fatal
            last_err = exc
            continue
        if jar and len(jar) > 0:
            return b, jar
    raise SystemExit(
        "Could not extract cookies from any browser. "
        "Make sure you're logged into YouTube in at least one of these: "
        + ", ".join(BROWSERS)
        + (f"\nLast error: {last_err}" if last_err else "")
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--browser", choices=BROWSERS, help="browser to export from (auto-detected if omitted)")
    ap.add_argument("--profile", help="browser profile name (multi-profile installs)")
    ap.add_argument("--format", action="store_true", help="print only the COOKIES_B64 value (for Render)")
    ap.add_argument("--no-config", action="store_true", help="skip writing .config/cookies.txt")
    ap.add_argument("--output", type=Path, help="write the raw cookies.txt to this path")
    args = ap.parse_args()

    if args.browser:
        jar = _extract_cookies(args.browser, args.profile)
        if jar is None or len(jar) == 0:
            raise SystemExit(f"Browser '{args.browser}' returned no cookies.")
        browser = args.browser
    else:
        browser, jar = _first_working_browser(args.profile)

    # Serialize to Netscape format.  session cookies must be kept (yt-dlp needs
    # them), so use the yt-dlp-compatible saver rather than the default.
    save_path = args.output or CONF_FILE
    if not args.format:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        jar.save(str(save_path), ignore_discard=True, ignore_expires=True)
        print(f"[podclip] Wrote cookies ({len(jar)} cookies) to {save_path}")

    b64 = base64.b64encode(save_path.read_bytes() if not args.format else _b64_from_jar(jar)).decode()
    if args.format:
        print(b64, end="")
    else:
        print(
            "\n[podclip] For Render (or any other host without a browser), set env var:\n"
            f"  COOKIES_B64={b64}\n"
        )
        if args.no_config and not args.output:
            print("[podclip] (--no-config: .config/cookies.txt not written)")
    return 0


def _b64_from_jar(jar) -> str:
    """Render the jar's cookies to a base64 string without writing a file."""
    import io

    from yt_dlp.cookies import YoutubeDLCookieJar

    buffer = io.StringIO()
    YoutubeDLCookieJar.save(buffer, ignore_discard=True, ignore_expires=True)
    return base64.b64encode(buffer.getvalue().encode("utf-8")).decode()


if __name__ == "__main__":
    sys.exit(main())

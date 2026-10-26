"""Audio acquisition: local file or URL -> normalised 16 kHz mono WAV.

Uses yt-dlp for URLs (YouTube or direct links). ffmpeg (bundled via
imageio-ffmpeg, or a system install) does the normalisation.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import CACHE_DIR
from .cookie_jar import cookie_opts
from .utils import ensure_dir, logger

logger = logging.getLogger("podclip.download")

URL_RE = re.compile(r"^https?://", re.IGNORECASE)


class DownloadError(RuntimeError):
    """Raised when audio cannot be acquired or normalised."""


@dataclass
class AcquiredAudio:
    """A normalised WAV ready for transcription, plus provenance info."""

    wav_path: Path
    title: str
    is_url: bool
    source: str
    download_dir: Path | None = field(default=None)

    @property
    def run_name_base(self) -> str:
        stem = Path(self.source).stem if not self.is_url else self.title
        stem = stem or "podcast"
        slug = re.sub(r"[^\w\-]+", "-", stem).strip("-").lower()[:60]
        return slug or "podcast"


def looks_like_url(value: str) -> bool:
    return URL_RE.match(value.strip()) is not None


def resolve_source(value: str, results_dir: Path | None = None) -> tuple[Path, str]:
    """Turn user input into a local path + title, downloading if it is a URL."""
    if not looks_like_url(value):
        path = Path(value).expanduser()
        if not path.exists():
            raise DownloadError(f"file not found: {value}")
        return path, path.stem
    return _download_url(value, results_dir or ensure_dir(CACHE_DIR / "downloads"))


def _download_url(url: str, out_dir: Path) -> tuple[Path, str]:
    logger.info("downloading %s with yt-dlp", url)
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:  # pragma: no cover - dependency install guard
        raise DownloadError(
            "yt-dlp is not installed (pip install yt-dlp); needed for URL downloads."
        ) from exc

    out_dir = ensure_dir(out_dir)
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "ignoreerrors": False,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
    }
    # Optional authenticated download: COOKIES_B64 / COOKIES_FILE, or
    # automatically the laptop's .config/cookies.txt / a browser session.
    # Helps with YouTube's "sign in to confirm you're not a bot" blocks on
    # cloud IPs. See podclip/cookie_jar.py for resolution rules.
    opts_cookies = cookie_opts()
    if opts_cookies:
        opts.update(opts_cookies)
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        infod = info if info is not None else {}
        target = Path(str(infod.get("requested_downloads") or [""])[0]) if infod.get(
            "requested_downloads") else None
        if target is None or not target.exists():
            # fall back to whatever the template produced
            matches = sorted(out_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
            target = matches[0] if matches else None
        if target is None or not target.exists():
            raise DownloadError(f"could not find the downloaded file for {url}")
        title = str(infod.get("title") or target.stem or "podcast")
        return target, title
    except DownloadError:
        raise
    except Exception as exc:  # noqa: BLE001 - report any yt-dlp failure kindly
        raise DownloadError(
            "download failed (check internet / URL on this machine). "
            f"Detail: {exc}"
        ) from exc


def acquire_audio(source: str, ffmpeg_exe: str,
                  progress: Callable[[str], None] | None = None) -> AcquiredAudio:
    """Resolve ``source`` (path or URL) and normalise to 16 kHz mono WAV."""
    import subprocess

    results_dir = ensure_dir(CACHE_DIR / "audio")
    source_str = source.strip()
    is_url = looks_like_url(source_str)

    try:
        local, title = resolve_source(source_str, results_dir)
    except DownloadError as exc:
        raise DownloadError(f"could not acquire audio from {source_str!r}: {exc}") from exc

    stem = re.sub(r"[^\w\-]+", "-", local.stem).strip("-")[:60] or "podcast"
    norm = results_dir / f"{stem}_norm.wav"
    if is_url and norm.exists():
        logger.info("reusing previously-normalised audio: %s", norm.name)
        return AcquiredAudio(
            wav_path=norm, title=title, is_url=is_url, source=source_str,
            download_dir=local.parent if is_url else None,
        )

    if progress:
        progress("normalising audio to 16 kHz mono…")
    stamp = int(time.time())
    tmp = norm.with_suffix(f".{stamp}.wav")
    cmd = [
        ffmpeg_exe, "-y", "-loglevel", "error",
        "-i", str(local), "-ar", "16000", "-ac", "1",
        "-c:a", "pcm_s16le", str(tmp),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        raise DownloadError(
            f"ffmpeg failed to normalise {local.name}. "
            f"Is the file a valid audio/video file? Detail: {exc}"
        ) from exc
    tmp.replace(norm)
    logger.info("normalised -> %s", norm.name)
    return AcquiredAudio(
        wav_path=norm, title=title, is_url=is_url, source=source_str,
        download_dir=local.parent if is_url else None,
    )


def content_hash(path: Path) -> str:
    """Fast stable hash of a file's content (first+last 256 KiB + size)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        h.update(("-" + str(size) + "-").encode())
        step = 1024 * 1024
        fh.seek(0)
        h.update(fh.read(step))
        if size > step * 2:
            fh.seek(size - step)
            h.update(fh.read(step))
    return h.hexdigest()[:16]

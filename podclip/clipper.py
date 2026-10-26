"""Clips: SRT writing, audio cutting, caption rendering, zip packaging.

Captions are burned in with ffmpeg's ``subtitles`` filter when the ffmpeg
build supports it; otherwise Pillow-drawn frames are used; if that fails the
clip still ships as mp3 + SRT (video skipped, never a crash).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Callable

from . import utils
from .transcribe import Segment
from .utils import logger, srt_time_to_seconds, wrap_text

logger = logging.getLogger("podclip.clipper")

VIDEO_SIZE = "720x1280"  # 9:16 vertical
FPS = 25
BG_COLOR_HEX = "0x0d1218"
FRAME_BG = (13, 18, 24)  # dark slate
FRAME_FG = (248, 247, 243)
FRAME_ACCENT = (255, 170, 60)
FONT_CANDIDATES = ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf", "NotoSans-Regular.ttf")
SYSTEM_FONT_DIRS = ("C:/Windows/Fonts", "/Windows/Fonts",
                    "/System/Library/Fonts", "/usr/share/fonts/truetype")

CAPS_CACHE: dict[str, set[str]] = {}
ENC_CACHE: dict[str, set[str]] = {}


# ---------------------------------------------------------------------------
# ffmpeg resolution + capability detection
# ---------------------------------------------------------------------------

def _ffmpeg_tool_list(exe: str, flag: str) -> set[str]:
    """Run `ffmpeg -<flag>` and return the names (handles the flags column).

    - ``-filters`` lines are ``... name   ->   description``
      (three flags: timeline/slice/command support)
    - ``-encoders`` lines are ``flags name  codec      desc``,
      e.g. ``V....D libx264  ...``
    """
    import re as _re
    try:
        out = subprocess.run(
            [exe, "-hide_banner", flag], capture_output=True, text=True, timeout=30,
        ).stdout
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("could not probe ffmpeg %s: %s", flag, exc)
        return set()
    names: set[str] = set()
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or stripped in ("Filters:", "Encoders:"):
            continue
        parts = stripped.split()
        if not parts:
            continue
        candidate: str | None = None
        if _re.fullmatch(r"[TSC. ]{3}", parts[0]):          # "...", "T..", ".S.", "..C"
            candidate = parts[1] if len(parts) > 1 else None
        elif parts[0].startswith(("V", "A", "S", "E")) and _re.fullmatch(
                r"[TSCEVAevqA-Za-z.]+", parts[0]):
            candidate = parts[1] if len(parts) > 1 else None
        if candidate:
            names.add(candidate)
    return names

def _ffmpeg_from_imageio() -> str | None:
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return str(exe)
    except Exception:  # noqa: BLE001
        return None
    return None


def get_ffmpeg() -> str:
    """Bundled (imageio-ffmpeg) binary, else a system ffmpeg. Raises if none."""
    exe = _ffmpeg_from_imageio()
    if exe is None:
        exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError(
            "ffmpeg not found: the bundled imageio-ffmpeg binary is missing and "
            "no system ffmpeg is on PATH. Reinstall imageio-ffmpeg or add ffmpeg "
            "to PATH."
        )
    return exe


def ffmpeg_capabilities(exe: str) -> set[str]:
    """Return the set of available ffmpeg filters (cached per binary)."""
    if exe not in CAPS_CACHE:
        CAPS_CACHE[exe] = _ffmpeg_tool_list(exe, "-filters")
    return CAPS_CACHE[exe]


def ffmpeg_encoders(exe: str) -> set[str]:
    """Return the set of available ffmpeg encoders (cached per binary)."""
    if exe not in ENC_CACHE:
        ENC_CACHE[exe] = _ffmpeg_tool_list(exe, "-encoders")
    return ENC_CACHE[exe]


def supports_subtitles(exe: str) -> bool:
    return "subtitles" in ffmpeg_capabilities(exe)


def pick_video_encoder(exe: str) -> str:
    """libx264 when present, else mpeg4 (anything unix-y enough for mp4)."""
    encs = ffmpeg_encoders(exe)
    if "libx264" in encs:
        return "libx264"
    if "mpeg4" in encs:
        return "mpeg4"
    return "libx264"  # let ffmpeg complain, render falls back


# ---------------------------------------------------------------------------
# SRT parsing + writing
# ---------------------------------------------------------------------------

def _split_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip() == "":
            if current:
                blocks.append("\n".join(current))
                current = []
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def parse_srt(text: str) -> list[tuple[float, float, str]]:
    """Parse SRT text into (start, end, text) tuples."""
    out: list[tuple[float, float, str]] = []
    for blk in _split_blocks(text):
        lines = [ln.strip() for ln in blk.splitlines() if ln.strip()]
        if not lines:
            continue
        timing_line = next((ln for ln in lines if "-->" in ln), None)
        if timing_line is None:
            continue
        try:
            start_raw, end_raw = (p.strip() for p in timing_line.split("-->")[:2])
            body = " ".join(lines[lines.index(timing_line) + 1:])
            out.append((srt_time_to_seconds(start_raw), srt_time_to_seconds(end_raw), body))
        except ValueError:
            continue
    return out


def write_srt(segments: list[Segment], clip_start: float, clip_end: float,
              path: Path, wrap: int = 72) -> list[tuple[float, float, str]]:
    """Write an SRT for the clip with times rebased to t=0.

    Returns the caption list actually written (for Pillow rendering).
    """
    entries: list[tuple[float, float, str]] = []
    for seg in segments:
        if seg.end <= clip_start + 1e-6 or seg.start >= clip_end - 1e-6:
            continue
        s = max(seg.start, clip_start) - clip_start
        e = min(seg.end, clip_end) - clip_start
        text = wrap_text(seg.text.strip(), wrap)
        entries.append((max(0.0, s), e, text))
    # merge captions closer than 0.6s so captions don't flash-frame
    merged: list[tuple[float, float, str]] = []
    for entry in entries:
        if merged and abs(entry[0] - merged[-1][1]) <= 0.6:
            ps, _, ptext = merged[-1]
            merged[-1] = (ps, entry[1], ptext + "\n" + entry[2])
        else:
            merged.append(entry)
    blocks: list[str] = []
    for i, (s, e, text) in enumerate(merged, 1):
        blocks.append(
            f"{i}\n{utils.seconds_to_srt_time(s)} --> {utils.seconds_to_srt_time(e)}\n{text}"
        )
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return merged


# ---------------------------------------------------------------------------
# Audio cutting
# ---------------------------------------------------------------------------

def cut_audio(exe: str, src_path: Path, out_dir: Path, index: int,
              start: float, end: float) -> Path:
    """Cut [start, end] out of the source audio, re-encode to MP3."""
    duration = max(0.1, end - start)
    out_path = out_dir / f"clip{index}.mp3"
    cmd = [
        exe, "-y", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-i", str(src_path),
        "-t", f"{duration:.3f}",
        "-vn", "-c:a", "libmp3lame", "-b:a", "160k",
        str(out_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        raise RuntimeError(f"ffmpeg failed to cut clip{index}.mp3: {exc}") from exc
    return out_path


def _aidur(audio_path: Path) -> float:
    """Duration of a cut clip: parsed from the filename we choose ourselves."""
    name = audio_path.stem  # clip1
    # we wrote clip{index}.mp3 with a known duration slice; recover it via ffprobe-free math
    # Fallback: probe with ffmpeg -i since it's reliable enough.
    try:
        exe = get_ffmpeg()
        out = subprocess.run(
            [exe, "-i", str(audio_path)], capture_output=True, text=True, timeout=30,
        ).stderr
        import re
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", out)
        if m:
            h, mi, s = m.groups()
            return float(h) * 3600 + float(mi) * 60 + float(s)
    except (subprocess.SubprocessError, OSError, re.error):
        pass
    return 45.0


# ---------------------------------------------------------------------------
# Video rendering
# ---------------------------------------------------------------------------

def _subtitles_filter(srt_path: Path, video_size: str, margin_v: int = 120) -> str:
    p = str(srt_path.resolve()).replace("\\", "/").replace("'", "\\'")
    return (
        f"subtitles='{p}':force_style='FontName=Arial,FontSize=26,"
        f"PrimaryColour=&H00FFFFFF,OutlineColour=&H00101010,"
        f"BorderStyle=1,Outline=1.2,Shadow=0,Alignment=2,MarginV={margin_v},Bold=1'"
    )


def render_video_with_subtitles(exe: str, srt_path: Path, audio_path: Path,
                                out_dir: Path, index: int,
                                video_size: str = VIDEO_SIZE,
                                start: float = 0.0, end: float = 0.0) -> Path:
    """Render the captioned mp4 using ffmpeg's ``subtitles`` filter."""
    out_path = out_dir / f"clip{index}.mp4"
    vf = _subtitles_filter(srt_path, video_size)
    cmd = [
        exe, "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c={BG_COLOR_HEX}:s={video_size}:r={FPS}",
        "-i", str(audio_path),
        "-vf", vf,
        "-map", "0:v", "-map", "1:a",
        "-c:v", pick_video_encoder(exe), "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=600)
    return out_path


# --------------------------------------------------------------------------
# Rendering with Pillow-drawn frames (no libass / subtitles filter needed)
# --------------------------------------------------------------------------

def _pick_font(size: int):
    from PIL import ImageFont
    for name in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    for d in SYSTEM_FONT_DIRS:
        for name in FONT_CANDIDATES:
            path = Path(d) / name
            if path.exists():
                try:
                    return ImageFont.truetype(str(path), size)
                except OSError:
                    continue
    return ImageFont.load_default(size=size)  # Pillow >= 10.1 scalable default


def draw_centered(draw, text: str, x: float, y: float, font) -> None:
    lines = text.splitlines()
    line_h = font.getbbox("Hg")[3] + 16
    start_y = y - (len(lines) * line_h) / 2
    for i, ln in enumerate(lines):
        draw.text((x, start_y + i * line_h), ln, font=font, fill=FRAME_FG,
                  anchor="mm", stroke_width=1, stroke_fill=(60, 70, 82))


def render_video_with_pillow(exe: str, srt_path: Path, audio_path: Path,
                             out_dir: Path, index: int,
                             video_size: str = VIDEO_SIZE) -> Path:
    """Render the captioned mp4 from Pillow frames (one per caption)."""
    from PIL import Image, ImageDraw
    entries = parse_srt(srt_path.read_text(encoding="utf-8"))
    if not entries:
        raise RuntimeError("no captions to render (empty SRT)")
    width, height = (int(v) for v in video_size.split("x"))
    audio_dur = _aidur(audio_path)
    font = _pick_font(max(26, int(height * 0.04)))

    tmpdir = Path(tempfile.mkdtemp(prefix=f"podclip_frames_{index}_"))
    paths: list[Path] = []
    try:
        for ci, (s, e, text) in enumerate(entries):
            frame = Image.new("RGB", (width, height), FRAME_BG)
            draw = ImageDraw.Draw(frame)
            draw.rectangle((0, 0, width, 8), fill=FRAME_ACCENT)
            draw_centered(draw, text, width / 2, height / 2, font)
            fp = tmpdir / f"f{ci:03d}.png"
            frame.save(fp)
            paths.append(fp)
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise

    out_path = out_dir / f"clip{index}.mp4"
    try:
        _concat_frames(exe, paths, audio_path, out_path, video_size,
                       audio_dur, fps=FPS)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return out_path


def _concat_frames(exe: str, frame_paths: list[Path], audio_path: Path,
                   out_path: Path, video_size: str, audio_dur: float,
                   fps: int) -> None:
    """Concatenate looped frames with the concat filter + the clip's audio."""
    if not frame_paths:
        raise RuntimeError("no frames to render")
    if len(frame_paths) == 1:
        cmd = [
            exe, "-y", "-loglevel", "error",
            "-loop", "1",
            "-i", str(frame_paths[0]),
            "-i", str(audio_path),
            "-vf", f"scale={video_size}:flags=lanczos,format=yuv420p",
            "-c:v", pick_video_encoder(exe), "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-t", f"{max(0.1, audio_dur):.2f}",
            "-shortest",
            str(out_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
        return

    inputs: list[str] = []
    loop_labels: list[str] = []
    for i, fp in enumerate(frame_paths):
        inputs += ["-loop", "1", "-t", f"{audio_dur:.2f}", "-i", str(fp)]
        loop_labels.append(f"[{i}:v]")
    inputs += ["-i", str(audio_path)]
    filters = "".join(
        f"[{i}:v]scale={video_size}:flags=lanczos,format=yuv420p,trim=0:d={_frame_dur(i, audio_dur, len(frame_paths)):.3f},setpts=PTS-STARTPTS[v{i}];"
        for i in range(len(frame_paths))
    )
    joins = "+".join(f"[v{i}]" for i in range(len(frame_paths)))
    filter_complex = f"{filters}{joins}concat=n={len(frame_paths)}:v=1:a=0[outv]"
    cmd = [
        exe, "-y", "-loglevel", "error",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", f"{len(frame_paths)}:a",
        "-c:v", pick_video_encoder(exe), "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-t", f"{max(0.1, audio_dur):.2f}",
        "-shortest",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=600)


def _frame_dur(frame_idx: int, audio_dur: float, total: int) -> float:
    if total <= 1:
        return audio_dur
    # last frame takes the remainder
    tail_portion = max(0.0, audio_dur - (audio_dur * (frame_idx + 1) / total))
    return max(0.05, audio_dur / total if audio_dur else 1.0)


# ---------------------------------------------------------------------------
# Orchestrated clip render (never crashes)
# ---------------------------------------------------------------------------

# Known-broken ffmpeg builds (per binary) whose subtitles filter segfaults.
_BROKEN_SUBTITLES: set[str] = set()


def render_clip(exe: str, src_path: Path, run_dir: Path, index: int,
                start: float, end: float, segments: list[Segment],
                use_subtitles_filter: bool | None = None) -> dict:
    """Produce clip{index}.mp3/.srt/.mp4 for one moment.

    Rendering order: libass ``subtitles`` filter → Pillow frames → SRT-only.
    A segfaulting subtitles filter is remembered for the whole run so we don't
    keep crashing a subprocess per clip. Never raises for the video part.
    """
    out_dir = run_dir
    audio_path = cut_audio(exe, src_path, out_dir, index, start, end)
    srt_path = out_dir / f"clip{index}.srt"
    write_srt(segments, start, end, srt_path)

    produced = {"mp3": audio_path, "srt": srt_path}
    mp4 = None

    want_subtitles = use_subtitles_filter if use_subtitles_filter is not None \
        else supports_subtitles(exe)
    if exe in _BROKEN_SUBTITLES:
        want_subtitles = False

    if want_subtitles:
        try:
            mp4 = render_video_with_subtitles(
                exe, srt_path, audio_path, out_dir, index, start=start, end=end)
        except subprocess.CalledProcessError as exc:
            # exit code -2 / 4294967274 = 0xC0000005 access violation on Windows
            if exc.returncode in (-2, 0xC0000005 - 2 ** 32, 4294967274):
                _BROKEN_SUBTITLES.add(exe)
                logger.warning(
                    "ffmpeg subtitles filter segfaulted (exit %s) — it is broken "
                    "on this build; switching to Pillow captions for this run.",
                    exc.returncode,
                )
                want_subtitles = False
            else:
                logger.warning("subtitles-filter render failed (%s); using Pillow frames", exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("subtitles-filter render failed (%s); using Pillow frames", exc)

    if mp4 is None:
        try:
            mp4 = render_video_with_pillow(exe, srt_path, audio_path, out_dir, index)
        except Exception as exc2:  # noqa: BLE001 - SRT-only fallback
            logger.warning("Pillow render also failed (%s); shipping SRT-only", exc2)
    if mp4 is not None:
        produced["mp4"] = mp4
    return produced


def make_zip(run_dir: Path, run_name: str) -> Path:
    """Zip every clip file in the run directory."""
    zip_path = run_dir / f"{run_name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(run_dir.iterdir()):
            if f.suffix == ".zip" or f.name.startswith("."):
                continue
            if f.is_file():
                zf.write(f, f.name)
    return zip_path

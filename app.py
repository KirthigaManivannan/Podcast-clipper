"""PodClip Streamlit app: paste a URL or upload a file, hit Make clips.

Ransom-note + podcast theme (coral/teal on warm paper). Backend identical to app.py.
v2: richer podcast backdrop — microphone, bigger vinyl, floating notes.
"""

from __future__ import annotations

import hashlib
import random as _rand
import uuid
from pathlib import Path

import streamlit as st

from podclip import __version__
from podclip.config import CACHE_DIR, Settings, key_ok
from podclip.pipeline import PipelineError, run_pipeline
from podclip.utils import ensure_dir, setup_logging

from html import escape as _esc

setup_logging()

st.set_page_config(page_title="PodClip — Podcast Clipper", page_icon="", layout="centered")

PAPER = "#FAF6EF"
PAPER_2 = "#F2ECDF"
INK = "#1F1B16"
MUTED = "#6F675D"
FAINT = "#A79C8C"
LINE = "#E7DECD"
CORAL = "#FF5A3C"
CORAL_DEEP = "#E54A2E"
TEAL = "#2E9E93"
TEAL_INK = "#1D6E67"
TEAL_SOFT = "#E2F1EE"
CARD = "#FFFDF8"
DANGER = "#C0392B"
SANS = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
SERIF = "Georgia, 'Times New Roman', 'Palatino Linotype', serif"

# ---- Seeded ransom-note logo (deterministic across Streamlit re-renders) ----
_RANSOM_SEED = 20241027
_RANSOM_PAPERS = ["#F3E7CA", "#F9DCD2", "#D7ECE8", "#EFE4F4", "#FBEDCF", "#E5EEDF", "#F7EFD6", "#E5E8E0"]
_RANSOM_FONTS = [
    "Georgia,'Times New Roman',serif",
    "'Courier New',Courier,monospace",
    "'Palatino Linotype',Palatino,serif",
    "'Trebuchet MS',sans-serif",
    "Impact,'Arial Black',sans-serif",
    "'Comic Sans MS','Chalkboard SE',cursive",
    "Georgia,'Times New Roman',serif",
    "'Courier New',Courier,monospace",
]


def _ransom(word: str) -> str:
    """Letter tiles, each with its own font/paper/rotation — fixed seed so the
    scramble never changes between Streamlit re-renders."""
    rng = _rand.Random(_RANSOM_SEED)
    tiles = []
    for ch in word:
        tiles.append(
            f'<span class="tile" style="background:{rng.choice(_RANSOM_PAPERS)};'
            f'font-family:{rng.choice(_RANSOM_FONTS)};'
            f'transform:rotate({rng.uniform(-4, 4):.2f}deg) '
            f'translateY({rng.uniform(-3, 3):.2f}px)">{ch}</span>'
        )
    return '<span class="ransom">' + "".join(tiles) + "</span>"


# ---- Microphone inline SVG (podcast backdrop) ----
_MIC_SVG = (
    '<svg class="mic" viewBox="0 0 120 120" fill="none" aria-hidden="true">'
    '<ellipse cx="60" cy="44" rx="26" ry="34" fill="var(--teal-soft)" '
    'stroke="var(--teal)" stroke-width="3"/>'
    '<rect x="52" y="68" width="16" height="10" rx="3" fill="var(--coral)"/>'
    '<rect x="44" y="76" width="32" height="10" rx="5" fill="var(--coral)"/>'
    '<rect x="48" y="84" width="24" height="8" rx="4" fill="var(--teal)"/>'
    '<path d="M60 76v16" stroke="var(--ink)" stroke-width="3" stroke-linecap="round"/>'
    '<path d="M40 92h40" stroke="var(--ink)" stroke-width="3" stroke-linecap="round"/>'
    '</svg>'
)


_CSS = f"""
<style>
:root {{
  --paper: {PAPER}; --paper-2: {PAPER_2}; --ink: {INK}; --muted: {MUTED};
  --faint: {FAINT}; --line: {LINE}; --coral: {CORAL}; --coral-deep: {CORAL_DEEP};
  --teal: {TEAL}; --teal-ink: {TEAL_INK}; --teal-soft: {TEAL_SOFT};
  --card: {CARD}; --danger: {DANGER};
  --sans: {SANS}; --serif: {SERIF};
  --r-sm: 10px; --r-md: 16px; --r-lg: 22px; --maxw: 900px;
}}
[data-testid="stAppViewContainer"] {{
  background: var(--paper); color: var(--ink); font-family: var(--sans);
  background-image:
    radial-gradient(circle at 88% 4%, rgba(255,90,60,.06), transparent 45%),
    radial-gradient(circle at 4% 70%, rgba(46,158,147,.06), transparent 40%);
}}
[data-testid="stAppViewContainer"]::before {{ /* faint grain */
  content:""; position:fixed; inset:0; z-index:0; pointer-events:none; opacity:.05;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
}}
[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stToolbar"], [data-testid="stStatusWidget"], [data-testid="stDecoration"] {{ display: none; }}
[data-testid="stMainBlockContainer"] {{ max-width: 900px; padding-top: 2.2rem; }}

/* ---- Podcast backdrop (vinyl, mic, waves, notes) ---- */
.deco {{
  position: fixed; inset: 0; overflow: hidden; pointer-events: none; z-index: 0;
}}
.deco .vinyl {{
  position: absolute; top: -160px; right: -140px; width: 560px; height: 560px; opacity: .6;
  border-radius: 50%;
  background: repeating-radial-gradient(circle at 50% 50%,
    rgba(46,158,147,.12) 0 5px, rgba(46,158,147,.03) 5px 11px);
  -webkit-mask: radial-gradient(circle at 50% 50%, transparent 0 6px, #000 7px);
  mask: radial-gradient(circle at 50% 50%, transparent 0 6px, #000 7px);
}}
.deco .vinyl::after {{
  content: ""; position: absolute; inset: 0; margin: auto;
  width: 46px; height: 46px; border-radius: 50%;
  background: rgba(255,90,60,.18); border: 3px solid rgba(255,90,60,.25);
}}
.deco .wave {{
  position: absolute; left: -60px; top: 210px; width: 260px; height: 140px; opacity: .55;
  border: 3px solid rgba(255,90,60,.16); border-radius: 50%;
}}
.deco .wave:nth-child(2) {{ top: 180px; width: 320px; height: 200px; }}
.deco .wave:nth-child(3) {{ top: 150px; width: 380px; height: 260px; }}
.deco .mic {{
  position: absolute; right: 130px; bottom: 90px; width: 120px; height: 120px; opacity: .7;
  filter: drop-shadow(0 6px 12px rgba(31,27,22,.18));
}}
.deco .eq {{
  position: absolute; right: 26px; bottom: 40px; display: flex; align-items: flex-end;
  gap: 6px; height: 70px; opacity: .65;
}}
.deco .eq i {{ width: 10px; border-radius: 5px; background: rgba(255,90,60,.4); }}
.deco .eq i:nth-child(even) {{ background: rgba(46,158,147,.4); }}
.deco .note {{
  position: absolute; font-size: 34px; opacity: .5; animation: floaty 6s ease-in-out infinite;
}}
.deco .note.n1 {{ left: 9%; top: 60%; animation-delay: 0s; }}
.deco .note.n2 {{ left: 20%; top: 28%; animation-delay: 1.5s; font-size: 26px; }}
.deco .note.n3 {{ left: 5%; top: 76%; animation-delay: 3s; font-size: 28px; }}
@keyframes floaty {{
  0%,100% {{ transform: translateY(0) rotate(0deg); }}
  50% {{ transform: translateY(-14px) rotate(6deg); }}
}}
@media (prefers-reduced-motion: reduce) {{ .deco .note {{ animation: none; }} }}

/* ---- Masthead ---- */
.eyebrow {{
  font-size: .76rem; letter-spacing: .2em; text-transform: uppercase; color: var(--teal-ink);
  margin: 0 0 .9rem; font-weight: 600;
}}
.ransom {{
  display: flex; flex-wrap: wrap; gap: .28rem .5rem; margin: .2rem 0 .3rem; line-height: 1.05;
}}
.tile {{
  display: inline-block; padding: .08em .22em .12em;
  font-weight: 800; font-size: 3.3rem; letter-spacing: -.01em; line-height: 1;
  border-radius: 6px; box-shadow: 0 3px 0 rgba(31,27,22,.16), 0 8px 14px rgba(31,27,22,.12);
  border: 1px solid rgba(31,27,22,.12);
}}
.podclip-tag {{ color: var(--muted); margin: .4rem 0 0; font-size: 1.04rem; }}
.podclip-tag b {{ color: var(--ink); font-weight: 600; }}
.mast-rule {{ border: 0; border-top: 2px solid var(--ink); margin: 1.5rem 0 1.4rem; }}

/* ---- Section label ---- */
.section-label {{
  font-size: .76rem; letter-spacing: .2em; text-transform: uppercase; color: var(--teal-ink);
  margin: 1.5rem 0 .6rem; font-weight: 600;
}}

/* ---- Buttons: coral primary, outline secondary ---- */
[data-testid="stButton"] button {{
  font-size: .82rem; font-weight: 600; letter-spacing: .08em; text-transform: uppercase;
  border-radius: 12px; padding: .78rem 1.5rem;
}}
[data-testid="stButton"] button[kind="primary"] {{
  background: var(--coral); border: 2px solid var(--coral); color: #231309;
}}
[data-testid="stButton"] button[kind="primary"]:hover {{
  background: var(--coral-deep); border-color: var(--coral-deep); color: #231309;
}}
[data-testid="stButton"] button[kind="primary"]:disabled {{
  background: var(--line); border-color: var(--line); color: var(--faint);
}}
[data-testid="stButton"] button[kind="secondary"] {{
  background: transparent; border: 2px solid var(--ink); color: var(--ink);
}}
[data-testid="stButton"] button[kind="secondary"]:hover {{
  background: var(--ink); color: var(--paper);
}}

/* ---- Inputs: rounded, warm ---- */
[data-testid="stTextInput"] input {{
  border-radius: 12px; border: 1.5px solid var(--line); background: var(--paper-2);
  color: var(--ink);
}}
[data-testid="stTextInput"] input:focus {{ border-color: var(--coral); }}
[data-testid="stFileUploader"] section {{
  border: 2px dashed var(--faint); background: var(--paper-2); border-radius: 16px;
  padding: 1.4rem;
}}
[data-testid="stFileUploader"] section:hover {{ border-color: var(--coral); }}
[data-testid="stFileUploader"] button {{ border-radius: 12px; }}

/* ---- Progress: coral, rounded ---- */
[data-testid="stProgress"] > div > div {{
  height: 6px; background: var(--line) !important; border-radius: 99px;
}}
[data-testid="stProgress"] > div > div > div {{
  background: var(--coral) !important; border-radius: 99px;
}}

/* ---- Tabs ---- */
[data-testid="stTab"] button {{
  border-radius: 10px; text-transform: uppercase; font-size: .78rem;
  letter-spacing: .08em; font-weight: 600;
}}
[data-testid="stTab"] [aria-selected="true"] {{
  background: var(--teal-soft); color: var(--ink);
}}

/* ---- Clip cards: paper + tape + tilt ---- */
.clip-row {{
  position: relative; background: var(--card); border: 1px solid var(--line);
  border-radius: 16px; box-shadow: 0 10px 24px rgba(31,27,22,.08);
  padding: 1.5rem 1.3rem 1.3rem; margin-top: 1.6rem;
  transform: rotate(-.5deg); transition: transform .18s ease, box-shadow .18s ease;
}}
.clip-row:nth-of-type(even) {{ transform: rotate(.5deg); }}
.clip-row:hover {{ transform: rotate(0deg); box-shadow: 0 18px 40px rgba(31,27,22,.12); }}
.clip-row::before {{ /* washi tape */
  content: ""; position: absolute; top: -12px; left: 50%; transform: translateX(-50%) rotate(-2deg);
  width: 96px; height: 24px; background: rgba(232,196,120,.55);
  border-left: 1px dashed rgba(120,100,50,.25); border-right: 1px dashed rgba(120,100,50,.25);
  box-shadow: 0 2px 4px rgba(0,0,0,.12); border-radius: 2px;
}}
.clip-head {{ display: flex; align-items: baseline; gap: .9rem; margin: 0 0 .4rem; }}
.clip-idx {{ font-family: var(--serif); font-weight: 700; font-size: 1.15rem; color: var(--coral); }}
.clip-meta {{ font-size: .82rem; color: var(--muted); font-variant-numeric: tabular-nums; }}
.clip-quote {{ font-family: var(--serif); font-size: 1.14rem; line-height: 1.5; margin: .2rem 0 .6rem; color: var(--ink); }}
.clip-quote::before {{ content: '\\201C'; font-size: 1.3em; color: var(--coral); }}
.clip-quote::after {{ content: '\\201D'; font-size: 1.3em; color: var(--coral); }}
.clip-reason {{ color: var(--muted); font-size: .9rem; margin: 0 0 .8rem; }}
[data-testid="stAudio"] audio {{ width: 100%; border: 1px solid var(--line); border-radius: 12px; }}
[data-testid="stVideo"] video {{ border: 1px solid var(--line); border-radius: 12px; }}
[data-testid="stDownloadButton"] button {{
  border-radius: 10px; background: transparent; border: 1.5px solid var(--teal);
  color: var(--teal-ink); font-size: .76rem; font-weight: 600;
  letter-spacing: .08em; text-transform: uppercase;
}}
[data-testid="stDownloadButton"] button:hover {{ background: var(--teal); color: var(--card); }}

/* ---- Alerts ---- */
[data-testid="stAlert"] {{
  border-radius: 14px; border: 1.5px solid var(--line);
}}
[data-testid="stAlert"] .stMarkdown {{ color: inherit; }}

/* ---- Dark mode ---- */
@media (prefers-color-scheme: dark) {{
  :root {{
    --paper: #17130E; --paper-2: #201A13; --ink: #F3ECE1; --muted: #B9AE9F;
    --faint: #857C6E; --line: #3B3228; --coral: #FF6B4A; --coral-deep: #FF8366;
    --teal: #49BFAF; --teal-ink: #7FD3C6; --teal-soft: #1F2C29;
    --card: #241E16;
  }}
  [data-testid="stAppViewContainer"] {{ background: var(--paper); color: var(--ink); }}
  .deco .vinyl {{ opacity: .8; }}
  .deco .mic {{ opacity: .85; }}
  .deco .note {{ opacity: .65; }}
  .tile {{ box-shadow: 0 3px 0 rgba(0,0,0,.5), 0 8px 14px rgba(0,0,0,.5); }}
}}
</style>
"""

st.markdown(_CSS, unsafe_allow_html=True)

st.markdown(
    f"""<div class="deco" aria-hidden="true">
  <span class="vinyl"></span><span class="wave"></span><span class="wave"></span>
  <span class="wave"></span>
  {_MIC_SVG}
  <span class="note n1" style="color:{CORAL}">♪</span>
  <span class="note n2" style="color:{TEAL}">♫</span>
  <span class="note n3" style="color:{CORAL}">♩</span>
  <span class="eq"><i style="height:30%"></i><i style="height:60%"></i><i style="height:45%"></i>
  <i style="height:85%"></i><i style="height:55%"></i><i style="height:70%"></i></span>
</div>
<p class="eyebrow">A podcast clipping tool</p>
{_ransom("PodClip")}
<p class="podclip-tag">Turn a podcast into <b>the five most quotable clips</b> — a bookmark,
a caption, a short video.&ensp;v{__version__}</p>
<hr class="mast-rule">""",
    unsafe_allow_html=True,
)

if not key_ok():
    st.error(
        "**OPENROUTER_API_KEY is missing or still the placeholder.**\n\n"
        "> Open the project's `.env` file and paste your key after `OPENROUTER_API_KEY=`.\n"
        "> Get a free key at https://openrouter.ai/keys, save the file, then refresh "
        "this page (or restart with `start.bat` / `./start.sh`).\n\n"
        "Nothing will run until that is fixed — the app reads the key only from `.env`, "
        "so you never type it here."
    )
    st.stop()

with st.sidebar:
    st.markdown("### Options")
    num_clips = st.slider("Number of clips", 1, 10, int(Settings().num_clips))
    min_s = st.slider("Min clip (seconds)", 10, 60, int(Settings().clip_min_seconds))
    max_s = st.slider("Max clip (seconds)", int(min_s), 120, max(int(Settings().clip_max_seconds), int(min_s)))

st.markdown("<p class='section-label'>Source</p>", unsafe_allow_html=True)
tab_file, tab_url = st.tabs(["Upload a file", "Paste a URL"])

file_handle: Path | None = None
with tab_file:
    uploaded = st.file_uploader(
        "mp3, wav, m4a, mp4…", label_visibility="collapsed",
    )
    if uploaded is not None:
        uploads = ensure_dir(CACHE_DIR / "uploads")
        ext = Path(uploaded.name).suffix or ".bin"
        file_handle = uploads / f"{uuid.uuid4().hex}{ext}"
        file_handle.write_bytes(uploaded.getvalue())
        st.caption(f"Loaded {uploaded.name} ({len(uploaded.getvalue()) / 1e6:.1f} MB)")

url_value: str | None = None
with tab_url:
    url_value = st.text_input("Podcast URL (YouTube or a direct mp3/m4a link)", label_visibility="collapsed")

make = st.button("Find clips", type="primary", use_container_width=True)

run_dir: Path | None = None
if "run_dir" in st.session_state:
    run_dir = st.session_state["run_dir"]
    run_dir = Path(run_dir) if run_dir else None

if make:
    if file_handle is not None:
        source = str(file_handle)
    elif url_value and url_value.strip():
        source = url_value.strip()
    else:
        st.warning("Upload a file or paste a URL first.")
        source = None
    if source:
        bar = st.progress(0.0, text="Starting…")

        def cb(step: int, total: int, label: str) -> None:
            bar.progress(min(step / max(total, 1), 1.0), text=f"{label}  ({step}/{total})")

        try:
            summary = run_pipeline(
                source, settings=Settings(num_clips=num_clips,
                                          clip_min=float(min_s),
                                          clip_max=float(max_s)),
                progress=cb,
            )
            st.session_state["run_dir"] = summary["run_dir"]
            run_dir = Path(summary["run_dir"])
            bar.progress(1.0, text="Complete.")
            st.balloons()
        except PipelineError as exc:
            bar.empty()
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001 - surface any crash in the UI
            bar.empty()
            st.error(f"Something went wrong: {exc}")

if run_dir is not None and run_dir.exists():
    st.markdown("<p class='section-label'>Clips</p>", unsafe_allow_html=True)
    summary_path = run_dir / "summary.json"
    if summary_path.exists():
        import json as _json
        try:
            data = _json.loads(summary_path.read_text(encoding="utf-8"))
            clips = data.get("clips", [])
        except (OSError, ValueError):
            data, clips = {}, []
        if clips:
            for clip in clips:
                start, end, dur = clip["start"], clip["end"], clip["duration"]
                st.markdown(
                    f"""<div class="clip-row">
                    <div class="clip-head">
                      <span class="clip-idx">{clip['index']:02d}</span>
                      <span class="clip-meta">{start:.1f}s – {end:.1f}s · {dur}s · score {clip['score']:.0f}/10</span>
                    </div>
                    <p class="clip-quote">{_esc(clip['quote'])}</p>
                    <p class="clip-reason">{_esc(clip['reason'])}</p></div>""",
                    unsafe_allow_html=True,
                )
                base = run_dir
                mp4 = base / f"clip{clip['index']}.mp4"
                mp3 = base / f"clip{clip['index']}.mp3"
                srt = base / f"clip{clip['index']}.srt"
                col_a, col_b = st.columns(2)
                with col_a:
                    if mp4.exists():
                        st.video(str(mp4))
                    elif mp3.exists():
                        st.audio(str(mp3))
                with col_b:
                    if mp3.exists():
                        st.audio(str(mp3))
                    d1, d2, d3 = st.columns(3)
                    for col, path in ((d1, mp3), (d2, mp4), (d3, srt)):
                        if path.exists():
                            col.download_button(
                                f"{path.suffix[1:].upper()}",
                                data=path.read_bytes(),
                                file_name=path.name,
                                key=f"dl_{clip['index']}_{path.suffix[1:]}",
                            )
        zip_path = run_dir / f"{run_dir.name}.zip"
        if zip_path.exists():
            st.download_button(
                "Download everything (.zip)", data=zip_path.read_bytes(),
                file_name=zip_path.name, key="dl_zip", use_container_width=True,
            )

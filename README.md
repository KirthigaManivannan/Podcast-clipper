# PodClip — Podcast Clipper

Turn a podcast (a local file **or** a YouTube/direct URL) into the **5 most
quotable moments**, delivered as short vertical clips with burned-in captions —
automatically.

No model IDs are hardcoded: PodClip hits the **OpenRouter catalog**, picks the
best **free** models available right now (preferring qwen/deepseek/llama/gemma/
mistral, large context, instruct variants), health-checks them, and falls back
between them when one is rate-limited or broken. Transcription is 100% local
with faster-whisper. No Anthropic/Claude API is ever used.

---

## 1. Requirements

- **Python 3.10–3.13** (Python 3.12 recommended; 3.14+ isn't supported by some
  packages like pyarrow yet).
- **A free OpenRouter API key**: <https://openrouter.ai/keys>
- Internet on first run (to download dependencies, the Whisper model, and to
  talk to OpenRouter).

**ffmpeg is handled for you** — PodClip uses the binary bundled with
`imageio-ffmpeg`. No system ffmpeg needed.

## 2. Quick start

### Windows
Double-click **`start.bat`** — or from a terminal:

```bat
start.bat
```

### macOS / Linux

```bash
./start.sh
```

On the first run the launcher will:

1. Create a virtual environment (`.venv`).
2. Install dependencies.
3. Create `.env` from `.env.example` if it doesn't exist.
4. **Check your key and — if it's missing — stop with a friendly message.**

If you see

```
[podclip] OPENROUTER_API_KEY is missing or still the placeholder.
          Open .env and paste your key after OPENROUTER_API_KEY=
```

open the project's `.env`, paste your key after the `=`, save, and re-run.
That's the **only** thing you ever edit by hand.

## 3. Use it

### The web app (default)
After `start.bat` / `./start.sh` you'll get a Streamlit page:

- **📁 Upload a file** — mp3, wav, m4a, mp4, …
- **🔗 Paste a URL** — YouTube or a direct audio link
- Hit **🎬 Make clips**, watch the progress bar, then play/download each clip
  or grab the zip with everything.

### The CLI

```bash
python cli.py ./episode.mp3
python cli.py "https://www.youtube.com/watch?v=..." --clips 5 --min 20 --max 45
python cli.py file.mp4 --dry-run        # print the plan, do nothing heavy
```

## 4. Outputs

Every run writes into **`outputs/<run-name>/`**:

```
clip1.mp3   clip1.srt   clip1.mp4    # audio, subtitles, captioned 720x1280 video
...                                       # (up to NUM_CLIPS of each)
summary.json                              # machine-readable (scores, reasons)
summary.md                                # human-readable
<run-name>.zip                            # everything above, zipped
```

`summary.json` holds for each clip its `start`, `end`, `score`, `reason`, and
`quote`.

## 5. Configuration

`.env` — the **only mandatory setting** is the key:

| Variable            | Default      | Meaning                                          |
|---------------------|--------------|--------------------------------------------------|
| `OPENROUTER_API_KEY`| *(required)* | Your OpenRouter key                              |
| `PRIMARY_MODEL`     | *(auto)*     | Preferred model id, e.g. `qwen/qwen3:free`        |
| `FALLBACK_MODELS`   | *(auto)*     | Comma-separated extra ids to try next             |
| `WHISPER_MODEL`     | `base`       | faster-whisper size (`tiny`…`large-v3`)          |
| `NUM_CLIPS`         | `5`          | Number of clips                                   |
| `CLIP_MIN_SECONDS`  | `20`         | Minimum clip length                               |
| `CLIP_MAX_SECONDS`  | `45`         | Maximum clip length                               |

If `OPENROUTER_API_KEY` is unset or still the placeholder, the launchers stop
with a clear message and nothing else runs.

## 6. How it works (the pipeline)

1. **Acquire** — local file, or yt-dlp downloads a URL's audio.
2. **Normalise** — ffmpeg converts to 16 kHz mono WAV.
3. **Transcribe** — faster-whisper (`int8`, CPU, local). Results are cached in
   `.cache/transcripts/`, so reruns skip Whisper.
4. **Chunk** — the transcript is split into ~10-minute windows (≤ ~6k tokens
   each, with overlap) so no prompt blows a free model's context.
5. **Analyse** — each chunk cites the transcript and the chosen free LLM picks
   its top 2 moments (JSON only), preferring strong opinions, surprising facts,
   emotional/funny bits, memorable one-liners, and complete thoughts.
6. **Merge** — candidates are de-duplicated (overlaps: higher score wins), the
   top N survive, and start/end are snapped to Whisper sentence boundaries and
   clamped to `CLIP_MIN_SECONDS`…`CLIP_MAX_SECONDS`.
7. **Clip** — ffmpeg re-encodes each cut (accurate), writes a rebased `.srt`,
   and burns captions into a `720x1280` vertical mp4. Caption rendering: the
   ffmpeg `subtitles` filter when the build supports it, else Pillow-drawn
   frames, else SRT-only — it never crashes.
8. **Summarise** — `summary.json` + `summary.md`, then a zip.

### Free-model selection details

At startup PodClip calls `GET https://openrouter.ai/api/v1/models`, keeps the
free (price 0 or `:free`) text-capable models with ≥ 32k context, ranks them
(± qwen/deepseek/llama/gemma/mistral; large context; instruct over reasoning),
health-checks with a tiny JSON probe, caches the result to `.cache/models.json`
for 6 hours, and uses the working models in order. On a 429/timeout/404/bad
JSON each model is retried with backoff, then the next model takes over; if all
fail, the whole list is retried once before giving up with a clear message.
Requests carry `HTTP-Referer` and `X-Title` headers. The API key is never
logged or printed.

## 7. Tests & smoke test

```bash
.venv\Scripts\python -m pytest tests/ -v        # offline unit tests
.venv\Scripts\python smoke_test.py              # end-to-end offline smoke run
```

Unit tests mock the LLM/Whisper/network and run offline. The smoke test
synthesises a 90-second tone with the bundled ffmpeg, uses mocked transcript +
LLM, and asserts real mp3/SRT/mp4/summary files come out.

## 8. Troubleshooting

- **`OPENROUTER_API_KEY` warning** — see Quick start; edit `.env`.
- **"could not reach OpenRouter"** — offline, or openrouter.ai is blocked.
- **Whisper download hangs** — the first transcription downloads the model
  (e.g. `base`, ~145 MB); needs internet, then it's cached.
- **All models failed** — free models are often rate-limited; the "retry once"
  logic handles it, and free inventory changes hourly — just retry later.
- **Python 3.14/3.15** — use 3.10–3.13 (see the launchers' interpreter pick).

## 9. Known limitations

- Free tier models vary in availability/quality moment to moment.
- Whisper quality depends on audio clarity; very noisy or heavily-music-laden
  shows clip poorly.
- YouTube downloads can fail on regional blocks/devices; direct audio links are
  the most reliable.
- The captioned video needs `libx264` (bundled ffmpeg has it) — if not, PodClip
  falls back to Pillow captions or SRT-only rather than failing.
- The `.env` key is never shown in the app; remember to keep `.env` out of any
  repo (a `.gitignore` is included).

## 10. Project layout

```
cli.py  app.py  start.bat  start.sh  .env.example  check_env.py  requirements.txt
podclip/   config.py  download.py  transcribe.py  llm.py  moments.py
           clipper.py  pipeline.py  utils.py  prompts.py
tests/     test_json_parsing.py  test_srt.py  test_snapping.py  test_model_ranking.py
```

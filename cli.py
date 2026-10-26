"""PodClip CLI: `python cli.py <file-or-url> [--clips N] [--min S] [--max S]
[--dry-run] [--model MODEL]`."""

from __future__ import annotations

import argparse
import json
import sys

from podclip import __version__
from podclip.config import Settings
from podclip.pipeline import PipelineError, run_pipeline
from podclip.utils import setup_logging


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="podclip",
        description="Turn a podcast into the 5 most quotable clips with captions.",
    )
    p.add_argument("source", help="local audio/video file path or a URL (YouTube/direct)")
    p.add_argument("--clips", type=int, default=None, help="number of clips (default: 5)")
    p.add_argument("--min", type=float, default=None, help="minimum clip seconds (default: 20)")
    p.add_argument("--max", type=float, default=None, help="maximum clip seconds (default: 45)")
    p.add_argument("--model", default=None, help="preferred OpenRouter model id (overrides .env)")
    p.add_argument("--dry-run", action="store_true",
                   help="print what would happen without transcribing or clipping")
    p.add_argument("--version", action="version", version=f"podclip {__version__}")
    return p


def dry_run(source: str, settings: Settings) -> int:
    """Print a plan for the run without doing heavy work."""
    from podclip.download import looks_like_url
    from podclip.config import PROJECT_DIR
    seen: list[str] = []
    print("PodClip dry-run")
    print(f"  source : {source}")
    print(f"  type   : {'URL' if looks_like_url(source) else 'local file'}")
    print(f"  clips  : {settings.num_clips} (min {settings.clip_min_seconds:.0f}s, "
          f"max {settings.clip_max_seconds:.0f}s)")
    print(f"  whisper: {settings.whisper_model}")
    models = settings.fallback_models
    if settings.primary_model:
        models = [settings.primary_model, *models]
    print(f"  models : {', '.join(models) or 'auto (free) from OpenRouter'}")
    print("  steps  : acquire audio -> normalise -> transcribe -> analyse -> "
          "cut -> SRT -> captioned mp4 -> summary")
    print(f"  output : {PODCLIP_OUTPUTS}")
    print()
    print("Dry run only; nothing was transcribed or clipped.")
    return 0


PODCLIP_OUTPUTS = "outputs/<run>/"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging()
    settings = Settings(
        num_clips=args.clips,
        clip_min=args.min,
        clip_max=args.max,
    )
    if args.model:
        settings.primary_model = args.model
    if args.dry_run:
        return dry_run(args.source, settings)

    try:
        summary = run_pipeline(args.source, settings=settings)
    except PipelineError as exc:
        print(f"podclip: {exc}", file=sys.stderr)
        return 2
    print(f"\nDone! Clips in {summary['run_name']}: {len(summary['clips'])}")
    for clip in summary["clips"]:
        print(
            f"  clip{clip['index']}: {clip['start']}s-{clip['end']}s  "
            f"(score {clip['score']}/10)  {clip['reason']}"
        )
    print(f"\nRun folder: outputs/{summary['run_name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

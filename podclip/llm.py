"""All OpenRouter LLM work: model discovery, ranking, health check, retries,
fallbacks, and robust JSON parsing.

No model IDs are hardcoded except user overrides; every call goes through
OpenRouter's OpenAI-compatible API.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Callable, Iterable

from .config import (
    LLM_CALL_DELAY,
    LLM_TIMEOUT,
    MIN_CONTEXT_TOKENS,
    MODEL_CACHE_TTL_SECONDS,
    OPENROUTER_HTTP_REFERER,
    OPENROUTER_X_TITLE,
)
from .prompts import HEALTH_CHECK_SYSTEM, HEALTH_CHECK_USER
from .utils import cache_dir, ensure_dir, logger

logger = logging.getLogger("podclip.llm")

MODEL_DISCOVERY_URL = "https://openrouter.ai/api/v1/models"
MODEL_CACHE_FILE = "models.json"
MODEL_CACHE_TTL = MODEL_CACHE_TTL_SECONDS
MODEL_SELECTION_CONTEXT_MIN = int(MIN_CONTEXT_TOKENS)

PREFERRED_BRANDS = ("qwen", "deepseek", "llama", "gemma", "mistral")
REASONING_MARKERS = ("reasoner", "thinking", "-r1", "-reasoning", "reasons")
INSTRUCT_MARKERS = ("instruct", "-chat", "turbo", "-it", "-8b")


class AllModelsFailedError(RuntimeError):
    """Every candidate model failed."""


class ModelSelectionError(RuntimeError):
    """No usable free models could be selected."""


# ---------------------------------------------------------------------------
# Model metadata helpers (pure, unit-testable)
# ---------------------------------------------------------------------------

def is_free_model(model: dict) -> bool:
    """True when a model is free: id ends ``:free`` or both prices are 0."""
    mid = str(model.get("id", ""))
    if mid.endswith(":free"):
        return True
    pricing = model.get("pricing") or {}
    prompt = str(pricing.get("prompt", "0"))
    completion = str(pricing.get("completion", "0"))
    return prompt in ("0", "0.0", "0.00") and completion in ("0", "0.0", "0.00")


def context_window(model: dict) -> int:
    """Context length in tokens (0 when unknown)."""
    try:
        value = model.get("context_length")
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def supports_text(model: dict) -> bool:
    """True when the model *outputs* text (text->image models don't count).

    OpenRouter modalities look like ``"text->text"`` or ``"image->text"``;
    the check keys off the right-hand side of the arrow.
    """
    modality = model.get("modality") or model.get("modalities") or "text->text"
    if isinstance(modality, (list, tuple)):
        modality = "+".join(str(x) for x in modality)
    out = str(modality).lower()
    if "->" in out:
        return "text" in out.split("->")[-1]
    return "text" in out


def brand_bonus(model_id: str) -> int:
    return 100 if any(b in model_id.lower() for b in PREFERRED_BRANDS) else 0


def architecture_bonus(model_id: str) -> int:
    """+60 for chat/instruct models, -20 for reasoning-capable ones."""
    mid = model_id.lower()
    if any(m in mid for m in REASONING_MARKERS):
        return -20
    if any(m in mid for m in INSTRUCT_MARKERS):
        return 60
    return 0


def context_bonus(model: dict) -> float:
    return min(context_window(model), 200_000) / 2_000.0  # up to +100


def model_score(model: dict) -> tuple[float, str]:
    """Sort key: (score, id) so ties break deterministically."""
    mid = str(model.get("id", ""))
    score = brand_bonus(mid) + architecture_bonus(mid) + context_bonus(model)
    return (-score, mid)


def rank_models(models: Iterable[dict]) -> list[str]:
    """Filter to usable free text models, return ids ranked best-first."""
    usable = [
        m for m in models
        if is_free_model(m)
        and supports_text(m)
        and context_window(m) >= MODEL_SELECTION_CONTEXT_MIN
    ]
    ranked = sorted(usable, key=model_score)
    return [str(m["id"]) for m in ranked]


# ---------------------------------------------------------------------------
# Discovery / health check (network)
# ---------------------------------------------------------------------------

def fetch_models(session=None) -> list[dict]:
    """GET the OpenRouter catalog (public endpoint, 20s timeout)."""
    import requests
    headers = {
        "HTTP-Referer": OPENROUTER_HTTP_REFERER,
        "X-Title": OPENROUTER_X_TITLE,
    }
    try:
        resp = (session or requests).get(MODEL_DISCOVERY_URL, headers=headers, timeout=20)
        resp.raise_for_status()
        return list(resp.json().get("data") or [])
    except (requests.RequestException, ValueError) as exc:
        raise ModelSelectionError(
            f"could not reach OpenRouter to list free models ({exc}). "
            "Check your internet connection and try again."
        ) from exc


def _cache_path() -> Path:
    return ensure_dir(cache_dir() / "models") / "models.json"


def read_cached_models() -> list[dict] | None:
    """Cached model catalog when fresher than TTL, else None."""
    path = _cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - float(data["fetched_at"]) < MODEL_CACHE_TTL:
            return list(data["models"])
    except (OSError, ValueError, KeyError, TypeError):
        path.unlink(missing_ok=True)
    return None


def write_cached_models(models: list[dict]) -> None:
    try:
        _cache_path().write_text(
            json.dumps({"fetched_at": time.time(), "models": models}),
            encoding="utf-8",
        )
    except OSError:
        logger.warning("could not write model cache")


def health_check(client, model_id: str) -> tuple[bool, str]:
    """Ask one model for ``{"ok": true}``; return (ok, snippet)."""
    try:
        text = client.complete(
            model_id, system=HEALTH_CHECK_SYSTEM, user=HEALTH_CHECK_USER,
            max_tokens=64, temperature=0.0,
        )
    except Exception as exc:  # noqa: BLE001 - probe, then carry on
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"
    parsed = extract_json(text)
    ok = bool(parsed.get("ok")) if isinstance(parsed, dict) else bool(
        parsed and all(d.get("ok") for d in parsed if isinstance(d, dict))
    )
    return bool(ok), text[:60]


def select_models(api_key: str, *, primary_model: str | None = None,
                  fallback_models: tuple[str, ...] = (),
                  session=None, use_cache: bool = True) -> list[str]:
    """Return the ordered candidate model list (user overrides first).

    A bad override is logged and skipped — it never raises; auto-selection
    simply continues with the catalog ranking.
    """
    logger.info("discovering free OpenRouter models…")
    models = read_cached_models() if use_cache else None
    if models is None:
        models = fetch_models(session=session)
        write_cached_models(models)

    ranked = rank_models(models)
    chosen: list[str] = []
    seen: set[str] = set()

    def _claim(mid: str | None) -> None:
        if mid and mid not in seen:
            chosen.append(mid)
            seen.add(mid)

    catalog_ids = {str(m.get("id")) for m in models}
    for override in [primary_model, *fallback_models]:
        if not override:
            continue
        # Accept any override present in the catalog — the account's guardrail
        # may only route to a couple of (possibly non-free) models, so a
        # user-specified model is always more trustworthy than our ranking.
        if override in catalog_ids or override.endswith(":free"):
            _claim(override)
            logger.info("using user-specified model %s", override)
        else:
            logger.warning(
                "override %r is not in the catalog; ignoring it and continuing "
                "with auto-selection", override,
            )
    chosen += [m for m in ranked if m not in seen]

    if not chosen:
        raise ModelSelectionError(
            "no usable free models found on OpenRouter (need free, text-capable "
            f"models with >= {MODEL_SELECTION_CONTEXT_MIN} tokens of context). "
            "Free models go in and out of stock; try again later."
        )
    logger.info("model candidates: %s", ", ".join(chosen))
    return chosen


def filter_healthy(client, candidates: list[str],
                  progress: Callable[[str], None] | None = None,
                  keep: int = 5) -> list[str]:
    """Health-check candidates; return the first ``keep`` that respond OK."""
    healthy: list[str] = []
    for model_id in candidates:
        if progress:
            progress(f"health-checking model {model_id}…")
        ok, detail = health_check(client, model_id)
        if ok:
            healthy.append(model_id)
            logger.info("health check passed: %s", model_id)
            if len(healthy) >= keep:
                break
        else:
            logger.warning("health check failed %s (%s)", model_id, detail)
    return healthy


# ---------------------------------------------------------------------------
# Chat completion, retries, fallback, backoff
# ---------------------------------------------------------------------------

def _new_client(api_key: str):
    from openai import OpenAI
    return OpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        timeout=LLM_TIMEOUT,
        default_headers={
            "HTTP-Referer": OPENROUTER_HTTP_REFERER,
            "X-Title": OPENROUTER_X_TITLE,
        },
    )


_AUTH_HINTS = (
    "api key not found",
    "invalid api key",
    "unauthorized",
    "authentication",
    "401",
    "403",
    "incorrect api key",
)


class LLMClient:
    """Thin OpenAI-compatible coman client (never logs the API key)."""

    def __init__(self, api_key: str | None) -> None:
        self.api_key = api_key
        self._client = None if not api_key else _new_client(api_key)

    def complete(self, model_id: str, *, system: str, user: str,
                 max_tokens: int = 1500, temperature: float = 0.2,
                 reasoning_tokens: int | None = None) -> str:
        if self._client is None:
            raise ValueError("LLMClient has no API key set")
        logger.debug("calling %s (max_tokens=%d, temp=%.1f, reasoning=%s)",
                     model_id, max_tokens, temperature, reasoning_tokens)
        kwargs: dict = dict(
            model=model_id,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        # Reasoning models (deepseek-r1 style) can burn the entire token budget
        # on the reasoning field and never emit content. OpenRouter lets us cap
        # reasoning so answer tokens stay within max_tokens.
        if reasoning_tokens:
            kwargs["extra_body"] = kwargs.get("extra_body") or {}
            kwargs["extra_body"]["reasoning"] = {"max_tokens": reasoning_tokens}
        resp = self._client.chat.completions.create(**kwargs)
        content = resp.choices[0].message.content or ""
        finish = resp.choices[0].finish_reason
        # A length-truncated reply is still useful (partial JSON). Only a
        # genuinely empty stop reply is a failure.
        if not content.strip() and finish in (None, "stop"):
            raise ValueError("model returned an empty completion")
        return content


def classify_error(exc: Exception) -> str:
    """Heuristic bucket for retry/fallback decisions."""
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "ratelimit" in name or "429" in text or "too many" in text:
        return "ratelimit"
    if "timeout" in name or "timed out" in text or "connection" in text:
        return "timeout"
    if "notfound" in name or "404" in text or "not found" in text:
        return "missing"
    if any(k in text for k in _AUTH_HINTS):
        return "auth"
    return "other"


def backoff_sleep(attempt: int) -> None:
    """Exponential backoff with jitter (unused in offline tests)."""
    time.sleep(min(2.0 ** attempt, 16.0) * (0.7 + random.random() * 0.6))


def ask_with_fallback(client, model_ids: list[str], *, system: str, user: str,
                      parse: Callable[[str], list[dict]],
                      max_tokens: int = 3000,
                      temperature: float = 0.7,
                      reasoning_tokens: int | None = 1024,
                      progress: Callable[[str], None] | None = None,
                      call_delay: float = LLM_CALL_DELAY) -> tuple[list[dict], str]:
    """Ask models until one returns parseable data.

    Retries each model twice (3 attempts total) with backoff, then moves to
    the next. If every model fails, waits and retries the whole list once.
    Returns (items, model_id_that_succeeded).
    """
    errors: list[str] = []
    if not model_ids:
        raise AllModelsFailedError("no candidate models were provided to query")
    for round_no in (1, 2):
        for model_id in model_ids:
            for attempt in range(3):
                try:
                    if progress:
                        progress(f"asking {model_id} for moments…")
                    raw = client.complete(
                        model_id, system=system, user=user,
                        max_tokens=max_tokens, temperature=temperature,
                        reasoning_tokens=reasoning_tokens,
                    )
                    items = parse(raw)
                    if items:
                        logger.info("%s returned %d moments", model_id, len(items))
                        return items, model_id
                    raise ValueError("unparseable or empty reply")
                except Exception as exc:  # noqa: BLE001 - retry logic below
                    bucket = classify_error(exc)
                    errors.append(f"{model_id}:{bucket}:{str(exc)[:100]}")
                    logger.warning(
                        "attempt %d on %s failed (%s): %s",
                        attempt + 1, model_id, bucket, str(exc)[:140],
                    )
                    if bucket == "auth":
                        return [], model_ids[0] if model_ids else ""
                    if attempt < 2:
                        backoff_sleep(attempt)
                    else:
                        time.sleep(call_delay)  # politeness before next model
        if round_no == 1:
            logger.warning("all models failed on round 1; waiting 15s and retrying once…")
            time.sleep(15)
    raise AllModelsFailedError(
        "every candidate model failed after retries (last errors: "
        + "; ".join(errors[-4:]) + "). Check your OpenRouter key and limits."
    )


# ---------------------------------------------------------------------------
# Robust JSON extraction / validation
# ---------------------------------------------------------------------------

def _strip_fences(text: str) -> str:
    """Remove markdown fences and XML-ish reasoning blocks."""
    text = re.sub(r"```[a-zA-Z]*\s*", "", text).replace("```", "")
    text = re.sub(r"<\s*thinking\b.*?</\s*thinking\s*>", "", text, flags=re.S)
    text = re.sub(r"<\s*/?\s*(?:thinking|thought|scratchpad)\s*>", "", text, flags=re.I)
    return text.strip()


def _iter_blobs(text: str):
    """Yield the text of each *outermost* balanced [..] / {..} group.

    Inner objects are skipped so that ``[{"a":1}]`` yields the array, not the
    object — the Array-or-Object decision stays with the caller.
    """
    stack_depth = 0
    start = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "[{":
            if stack_depth == 0:
                start = i
            stack_depth += 1
        elif ch in "]}":
            if stack_depth > 0:
                stack_depth -= 1
                if stack_depth == 0:
                    yield text[start:i + 1]


def _try_parse(text: str):
    for blob in _iter_blobs(text):
        try:
            return json.loads(blob)
        except ValueError:
            continue
    return None


def extract_json(text: str):
    """Parse JSON out of noisy LLM text (fences, prose, thinking blocks)."""
    cleaned = _strip_fences(text)
    parsed = _try_parse(cleaned)
    if parsed is not None:
        return parsed
    return _try_parse(text)  # last resort: raw text


def parse_moment_items(raw: str, lo: float | None = None,
                       hi: float | None = None) -> list[dict]:
    """Parse LLM output into validated moment dicts; invalid items discarded."""
    parsed = extract_json(raw)
    if isinstance(parsed, dict):
        for key in ("moments", "clips", "candidates", "results", "items"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
    if not isinstance(parsed, list):
        return []
    items: list[dict] = []
    for entry in parsed:
        item = _validate_item(entry, lo=lo, hi=hi)
        if item is not None:
            items.append(item)
    return items


def _num(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except (ValueError, AttributeError):
            return None
    return None


def _validate_item(entry, lo: float | None, hi: float | None) -> dict | None:
    if not isinstance(entry, dict):
        return None
    start = _num(entry.get("start"))
    end = _num(entry.get("end"))
    score_f = _num(entry.get("score"))
    quote = str(entry.get("quote") or entry.get("text") or "").strip()
    reason = str(entry.get("reason") or "").strip()
    if start is None or end is None or end <= start:
        return None
    if lo is not None and (start < lo - 1e-6 or end > hi + 1e-6):
        return None
    score_f = max(1.0, min(10.0, score_f)) if score_f is not None else 5.0
    if not quote:
        return None
    return {
        "start": start, "end": end, "score": score_f,
        "reason": reason or "quotable moment", "quote": quote,
    }

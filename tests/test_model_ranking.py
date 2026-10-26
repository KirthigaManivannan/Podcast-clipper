"""Tests for free-model selection ranking (offline, no network)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from podclip.llm import (  # noqa: E402
    rank_models,
    is_free_model,
    supports_text,
    context_window,
    architecture_bonus,
    brand_bonus,
    context_bonus,
    model_score,
)


def _m(mid, ctx=32768, prompt="0", completion="0", modality="text->text"):
    return {
        "id": mid,
        "context_length": ctx,
        "pricing": {"prompt": prompt, "completion": completion},
        "modality": modality,
    }


def test_free_detection_pricing_and_suffix():
    assert is_free_model(_m("deepseek/deepseek-chat:free"))       # :free suffix
    assert is_free_model(_m("qwen/qwen3", prompt="0", completion="0"))  # zero pricing
    assert is_free_model(_m("x", prompt="0.00", completion="0.0"))
    assert not is_free_model(_m("openai/gpt-4o", prompt="0.001", completion="0.001"))
    # :free suffix wins even if the catalog records prices (OR logic)
    assert is_free_model(_m("openai/gpt-4o:free", prompt="0.002", completion="0.002"))


def test_context_and_text_filters():
    assert context_window(_m("x", ctx=None)) == 0
    assert context_window(_m("x", ctx=16000)) == 16000
    # text->image models output images, not text -> excluded
    assert not supports_text(_m("x", modality="text->image"))
    # image->text still outputs text -> included
    assert supports_text(_m("x", modality="image->text"))
    assert supports_text(_m("x", modality="text->text"))


def test_ranking_excludes_paid_and_small():
    models = [
        _m("some/unknown:free"),
        _m("openai/gpt-4o", prompt="0.01", completion="0.01"),
        _m("x-ai/grok:free", ctx=8192),
    ]
    ranked = rank_models(models)
    assert "openai/gpt-4o" not in ranked
    assert "x-ai/grok:free" not in ranked
    assert ranked == ["some/unknown:free"]


def test_ranking_prefers_preferred_brands():
    models = [
        _m("some/unknown-model:free"),
        _m("qwen/qwen3:free"),
        _m("deepseek/deepseek-chat:free"),
        _m("meta-llama/llama-3.3-70b-instruct:free"),
        _m("google/gemma-2-9b-it:free"),
        _m("mistralai/mistral-7b:free"),
    ]
    ranked = rank_models(models)
    # unknown brand still ranks last (no brand bonus) but is included
    assert ranked[-1] == "some/unknown-model:free"
    assert ranked.index("meta-llama/llama-3.3-70b-instruct:free") < ranked.index(
        "mistralai/mistral-7b:free")  # instruct bonus beats plain base


def test_ranking_prefers_instruct_over_reasoning():
    models = [
        _m("deepseek/deepseek-reasoner:free"),
        _m("deepseek/deepseek-chat:free"),
    ]
    ranked = rank_models(models)
    assert ranked.index("deepseek/deepseek-chat:free") < ranked.index(
        "deepseek/deepseek-reasoner:free")


def test_ranking_prefers_bigger_context():
    assert model_score(_m("x:free", ctx=131072)) < model_score(_m("x:free", ctx=32768))
    # different ids, larger context should outrank a similarly-branded small one
    models = [
        _m("qwen/qwen3:free", ctx=32768),
        _m("deepseek/deepseek-chat:free", ctx=131072),
    ]
    ranked = rank_models(models)
    assert ranked.index("deepseek/deepseek-chat:free") < ranked.index("qwen/qwen3:free")


def test_architecture_and_brand_bonuses():
    assert brand_bonus("qwen/qwen3:free") > 0
    assert brand_bonus("totally/unknown:free") == 0
    assert architecture_bonus("deepseek/deepseek-chat:free") > 0
    assert architecture_bonus("deepseek/deepseek-reasoner:free") < 0
    assert architecture_bonus("unknown/really:free") == 0

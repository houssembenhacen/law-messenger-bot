#!/usr/bin/env python3
"""Answer a law question in the bot's own words, from lecture passages only.

Uses the Gemini API through the registered custom.gemini connection
(see custom-connectors/custom.gemini/CONNECTOR.md). The API key never
lives in a file: dynamic_credentials.py supplies a surrogate that the
runtime swaps for the real key on the approved request.

If Gemini is unavailable (no key, network error, bad response), returns
None and the caller falls back to quoting the passages verbatim.
"""
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
try:
    from dynamic_credentials import url_with_surrogate_query_param
except Exception:  # noqa: BLE001 - synth simply unavailable without it
    url_with_surrogate_query_param = None

# Models in preference order. The full Flash models were returning 503
# "high demand" / timing out on 2026-11-21, while the Lite model answered
# in ~1s, so Lite goes first; the others are fallbacks.
MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-flash-latest",
    "gemini-3.5-flash",
    "gemini-3.8-flash",
]
API_HOST = "generativelanguage.googleapis.com"
CREDENTIAL = "custom.gemini"


def _clean(text: str) -> str:
    text = re.sub(r"[\u200b-\u200f\u202a-\u202e]", "", text or "")
    return re.sub(r"\s+", " ", text).strip()


LANG_NAMES = {"ar": "Arabic", "fr": "French", "en": "English"}


def synthesize(question: str, lang: str, hits) -> str | None:
    if url_with_surrogate_query_param is None:
        return None
    passages = []
    for i, (score, chunk) in enumerate(hits, start=1):
        text = _clean(chunk["text"])
        if len(text) > 1500:
            text = text[:1500].rstrip() + "…"
        passages.append(
            f"[Passage {i}] Source: {chunk['source']}, page {chunk['page']}\n{text}"
        )
    if not passages:
        return None
    language = LANG_NAMES.get(lang, "Arabic")
    prompt = (
        f"Answer this law question in {language}, in your own words, using ONLY "
        "the lecture passages below. Do not use outside knowledge, do not invent "
        "facts, articles or case names, and do not add anything the passages do "
        "not support. If the passages do not contain enough to answer, say so "
        "plainly in one sentence and stop. Structure the answer clearly: start "
        "with a short direct answer (1-2 sentences), then the key points as a "
        "short list when there is more than one. After every claim, cite the "
        "passage it comes from as [Source file, p. N] using the exact source "
        "names given. Keep the whole answer under 250 words.\n\n"
        f"Question: {question}\n\n"
        + "\n\n".join(passages)
    )
    body = json.dumps(
        {"contents": [{"parts": [{"text": prompt}]}]},
        ensure_ascii=False,
    ).encode("utf-8")
    import os

    # Prefer a key from the environment (bot.env) when present; otherwise
    # use the registered custom.gemini connection via its surrogate.
    env_key = os.environ.get("GEMINI_API_KEY", "").strip()
    for model in MODELS:
        try:
            if env_key:
                url = (
                    f"https://{API_HOST}/v1beta/models/{model}:generateContent"
                    f"?key={urllib.parse.quote(env_key, safe='')}"
                )
            else:
                if url_with_surrogate_query_param is None:
                    return None
                url = url_with_surrogate_query_param(
                    f"https://{API_HOST}/v1beta/models/{model}:generateContent",
                    CREDENTIAL,
                    allowed_hosts=[API_HOST],
                )
            req = urllib.request.Request(url, data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            with urllib.request.build_opener().open(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts).strip()
            if text:
                return text
        except Exception as exc:  # noqa: BLE001 - try next model, else fall back
            print(f"Gemini {model} failed: {exc}", file=sys.stderr)
            continue
    return None

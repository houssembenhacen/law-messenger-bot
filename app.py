#!/usr/bin/env python3
"""Messenger (Facebook Page) version of the law lectures bot.

Same brain as the Telegram bot (@Law_algeria_bot): lecture-only BM25
retrieval over data/index.json in ../law-telegram-bot, Gemini synthesis
when available, verbatim passages otherwise. Same welcome message and
same commands (/modules /search /stats /clear /lectures /help).

Messenger flow (Meta Messenger Platform):
- Meta calls GET /webhook?hub.mode=subscribe&hub.verify_token=...&hub.challenge=...
  to verify the webhook. We answer with the challenge when the token matches.
- Meta POSTs message events to /webhook. We answer each text message via
  the Graph API (POST https://graph.facebook.com/v21.0/me/messages).

Config comes from messenger.env (never commit it):
    PAGE_ACCESS_TOKEN=...   # from Meta app -> Messenger -> Page token
    VERIFY_TOKEN=...        # any random string; paste the same in Meta
    PORT=5000
"""
import json
import logging
import os
import re
import sys
from pathlib import Path

import httpx
from flask import Flask, jsonify, request

BASE = Path(__file__).resolve().parent
TELEGRAM_BOT = BASE
sys.path.insert(0, str(TELEGRAM_BOT))

from retrieve import load_index  # noqa: E402
from welcome import WELCOME, _module_label  # noqa: E402
from welcome import lang_of  # noqa: E402

GRAPH = "https://graph.facebook.com/v21.0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(BASE / "messenger.log"), logging.StreamHandler()],
)
log = logging.getLogger("messenger-bot")

app = Flask(__name__)

PAGE_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN", "").strip()
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "").strip()


def _send_text(recipient_id: str, text: str) -> None:
    """Send one text message via the Graph API. Messenger caps a message
    at 2000 characters, so long answers are split into chunks."""
    if not PAGE_TOKEN:
        log.warning("PAGE_ACCESS_TOKEN not set; cannot send to %s", recipient_id)
        return
    chunks = [text[i : i + 1900] for i in range(0, len(text), 1900)] or [""]
    with httpx.Client(timeout=30.0) as client:
        for chunk in chunks:
            resp = client.post(
                f"{GRAPH}/me/messages",
                params={"access_token": PAGE_TOKEN},
                json={
                    "recipient": {"id": recipient_id},
                    "message": {"text": chunk},
                },
            )
            if resp.status_code != 200:
                log.error("send failed %s: %s", resp.status_code, resp.text[:300])


def _typing(recipient_id: str) -> None:
    if not PAGE_TOKEN:
        return
    try:
        with httpx.Client(timeout=15.0) as client:
            client.post(
                f"{GRAPH}/me/messages",
                params={"access_token": PAGE_TOKEN},
                json={
                    "recipient": {"id": recipient_id},
                    "sender_action": "typing_on",
                },
            )
    except Exception:  # noqa: BLE001 - typing indicator is best-effort
        pass


def _lectures_text() -> str:
    idx = load_index()
    if not idx or not idx.chunks:
        return "لا توجد محاضرات مفهرسة بعد."
    names = sorted({c["source"] for c in idx.chunks})
    return "المحاضرات المفهرسة:\n" + "\n".join(f"• {n}" for n in names)


def _modules_text() -> str:
    idx = load_index()
    if not idx or not idx.chunks:
        return "لا توجد محاضرات مفهرسة بعد."
    groups: dict[str, set] = {}
    for c in idx.chunks:
        groups.setdefault(_module_label(c["source"]), set()).add(c["source"])
    lines = ["📚 المقاييس والمستويات المتاحة:\n", "🎓 المستوى: ماستر 1 — قانون جنائي وعلوم جنائية\n"]
    for module, sources in sorted(groups.items()):
        lines.append(f"• {module} ({len(sources)} ملف)")
        for s in sorted(sources):
            lines.append(f"   - {s.replace('.pdf', '')}")
    return "\n".join(lines)


def _stats_text() -> str:
    idx = load_index()
    if not idx or not idx.chunks:
        return "لا توجد محاضرات مفهرسة بعد."
    sources, pages = {}, {}
    for c in idx.chunks:
        sources[c["source"]] = sources.get(c["source"], 0) + 1
        pages[c["source"]] = max(pages.get(c["source"], 0), c["page"])
    lines = [
        "📊 إحصائيات قاعدة البيانات:",
        f"• عدد الملفات: {len(sources)}",
        f"• عدد المقاطع المفهرسة: {len(idx.chunks)}",
        f"• مجموع الصفحات: {sum(pages.values())}",
        "",
        "📚 حسب الملف:",
    ]
    for s in sorted(sources):
        lines.append(f"• {s.replace('.pdf', '')}: {sources[s]} مقطع، {pages[s]} صفحة")
    return "\n".join(lines)


def _search_text(query: str) -> str:
    idx = load_index()
    if not idx:
        return "لا توجد محاضرات مفهرسة بعد."
    hits = idx.search(query, top_k=3)
    if not hits:
        return f"لا توجد نتائج لـ «{query}» في المحاضرات."
    parts = [f"🔍 نتائج البحث عن «{query}»:"]
    for _score, c in hits:
        excerpt = c["text"]
        if len(excerpt) > 500:
            excerpt = excerpt[:500].rstrip() + "…"
        parts.append(f"\n“{excerpt}”\n— {c['source']}, ص. {c['page']}")
    return "\n".join(parts)


def _answer_question(question: str) -> str:
    lang = lang_of(question)
    t_page = "ص." if lang == "ar" else "p."
    t_source = "المصدر" if lang == "ar" else "Source"
    not_found = {
        "ar": "لم أجد ذلك في محاضراتك.\nجرّب كلمات أخرى من الدرس، أو حدّد الفصل / المادة.",
        "fr": "Je ne trouve pas ça dans tes cours.\nEssaie avec d'autres mots-clés du cours, ou précise le chapitre / la matière.",
        "en": "I can't find that in your lectures.\nTry different keywords from the course, or name the chapter / subject.",
    }[lang]
    idx = load_index()
    if idx is None:
        return "لا توجد محاضرات مفهرسة بعد."
    hits = idx.search(question, top_k=3)
    if not hits:
        return not_found
    try:
        from synth import synthesize

        explained = synthesize(question, lang, hits)
    except Exception:  # noqa: BLE001 - fall back to verbatim passages
        explained = None
    if explained:
        sources = "\n".join(f"• {c['source']}, {t_page} {c['page']}" for _s, c in hits)
        return f"{explained}\n\n{t_source}:\n{sources}"
    head = {"ar": "من محاضراتك:", "fr": "D'après tes cours :", "en": "From your lectures:"}[lang]
    parts = [head]
    for _score, chunk in hits:
        excerpt = chunk["text"]
        if len(excerpt) > 900:
            excerpt = excerpt[:900].rstrip() + "…"
        parts.append(f"\n“{excerpt}”\n— {t_source}: {chunk['source']}, {t_page} {chunk['page']}")
    return "\n".join(parts)


def handle_text(text: str) -> str:
    """Route a message exactly like the Telegram bot's commands."""
    text = (text or "").strip()
    low = text.lower()
    if low in ("/start", "start", "السلام عليكم", "سلام", "مرحبا", "مرحباً"):
        return WELCOME
    if low == "/help":
        return (
            "📌 تعليمات إضافية\n\n"
            "• اكتب سؤالك كاملاً بجملة واضحة، واذكر المادة أو القانون إن كنت تعرفه.\n"
            "• أجيب فقط من المحاضرات والمطبوعات المفهرسة، وأذكر المصدر ورقم الصفحة مع كل إجابة.\n"
            "• إن لم أجد الجواب في المحاضرات سأقول لك ذلك صراحة، ولن أخترع إجابة.\n\n"
            "الأوامر: /modules ، /search ، /stats ، /clear ، /lectures ، /help"
        )
    if low == "/lectures":
        return _lectures_text()
    if low == "/modules":
        return _modules_text()
    if low == "/stats":
        return _stats_text()
    if low == "/clear":
        return "✅ تم مسح سياق المحادثة.\nيمكنك الآن بدء موضوع جديد — اكتب سؤالك القانوني مباشرة."
    if low.startswith("/search"):
        q = text[len("/search") :].strip()
        if not q:
            return "استخدم: /search كلمة\nمثال: /search الحبس المؤقت"
        return _search_text(q)
    return _answer_question(text)


@app.get("/webhook")
def verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN and challenge:
        log.info("webhook verified")
        return challenge, 200
    log.warning("webhook verify failed: mode=%s", mode)
    return "verification failed", 403


@app.post("/webhook")
def receive():
    body = request.get_json(silent=True) or {}
    if body.get("object") != "page":
        return jsonify({"status": "ignored"}), 200
    for entry in body.get("entry", []):
        for event in entry.get("messaging", []):
            sender = (event.get("sender") or {}).get("id")
            if not sender:
                continue
            if "postback" in event:
                payload = (event["postback"] or {}).get("payload", "")
                log.info("postback %s from %s", payload, sender)
                if payload in ("GET_STARTED", "START"):
                    _send_text(sender, WELCOME)
                continue
            msg = event.get("message") or {}
            if msg.get("is_echo"):
                continue
            text = (msg.get("text") or "").strip()
            if not text:
                continue
            log.info("message from %s: %s", sender, text[:120])
            _typing(sender)
            reply = handle_text(text)
            _send_text(sender, reply)
    return jsonify({"status": "ok"}), 200


@app.get("/health")
def health():
    idx = load_index()
    return jsonify(
        {
            "status": "ok",
            "chunks": len(idx.chunks) if idx else 0,
            "page_token_set": bool(PAGE_TOKEN),
        }
    )


def main():
    port = int(os.environ.get("PORT", "5000"))
    log.info("Messenger bot webhook listening on port %s", port)
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()

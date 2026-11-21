# Deploy the Messenger bot (Render free tier)

This folder is self-contained: the webhook app + the lecture index
(`data/index.json`, 41 files / 3,800 chunks). No Telegram code needed.

## 1. Put it on GitHub (once)
- Create a repo, upload this whole `deploy/` folder as the repo root.

## 2. Create the Render service
- render.com → New → Web Service → connect the repo.
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120`
- Environment variables (Render dashboard → Environment):
  - `PAGE_ACCESS_TOKEN` — from Meta (step 3)
  - `VERIFY_TOKEN` — Render can generate it (see render.yaml), copy its value
  - `GEMINI_API_KEY` — same key as the Telegram bot, so answers are
    explained in the bot's own words; without it the bot still answers
    with verbatim passages.
- Deploy. The service URL looks like
  `https://law-messenger-bot.onrender.com` — the webhook URL is that
  plus `/webhook`.

## 3. Meta (Facebook Page + app)
- Create a Facebook Page for the bot, or use one you manage.
- developers.facebook.com → create app → add the **Messenger** product.
- Messenger settings → generate a **Page Access Token** for the Page →
  paste it as `PAGE_ACCESS_TOKEN` in Render and redeploy.
- Add a webhook: callback URL = `<service URL>/webhook`,
  verify token = the `VERIFY_TOKEN` value. Subscribe to `messages`
  and `messaging_postbacks`.
- In the same screen, subscribe the Page to the webhook.

## 4. Test
- Message the Page on Messenger. `/start` shows the welcome text,
  a law question gets an answer with source + page.
- Note: Render's free tier sleeps after inactivity, so the first
  message after a while can take ~30–60s to answer. The Telegram bot
  is unaffected.

## Updating lectures
When new PDFs are added to the Telegram bot, copy the new
`data/index.json` from `../law-telegram-bot/data/` into `deploy/data/`,
commit and redeploy.

<div align="center">

**[🇷🇺 Русский](README.md) · 🇬🇧 English**

# ⭐ Telegram Stars Bot

**Sell Telegram Stars right inside your bot — with your own markup.**
A user picks a recipient and the number of stars, pays by card or SBP, and the
stars are delivered automatically. Payments and fulfilment go through WATA.
The bot is bilingual: a **Russian and English** interface with on-the-fly switching.

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
&nbsp;
[![aiogram](https://img.shields.io/badge/aiogram-3.x-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://docs.aiogram.dev/)
&nbsp;
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://docs.docker.com/compose/)
&nbsp;
![License](https://img.shields.io/badge/License-MIT-111A2B?style=for-the-badge&logo=opensourceinitiative&logoColor=white)

> ⚠️ A self-hosted server project. You deploy the bot on your own server and are
> solely responsible for the legality of reselling Stars and for taxes. The
> project is not affiliated with the WATA provider.

### [✨ Features](#-features) · [🤖 No server](#-no-server-required) · [🧱 Stack](#-stack) · [🚀 Quick start](#-quick-start) · [🖥️ Server](#️-getting-a-server) · [⚙️ Configuration](#️-configuration) · [🌐 Reverse proxy & HTTPS](#-reverse-proxy--https) · [🪝 Webhooks](#-webhooks-telegram--wata) · [📦 Statuses](#-order-statuses) · [🔁 Updating](#-updating) · [❓ FAQ](#-faq) · [⚠️ Disclaimer](#️-disclaimer) · [📄 License](#-license)

</div>

---

## ✨ Features

| | Feature | Description |
|---|---|---|
| ⭐ | **Sell Telegram Stars** | Buy stars for any @username (from 50 to 50,000) via the WATA Digital Goods API |
| 💰 | **Configurable margin** | Markup over the minimum price via `MARKUP_PERCENT`, clamped to +50% |
| 💳 | **Card / SBP payment** | One-off WATA payment link: bank card or SBP |
| 🔔 | **Payment tracking** | WATA webhook with RSA signature verification + status reconciliation via API |
| ✅ | **Auto-confirm** | Automatic `confirm` after payment (`AUTO_CONFIRM=true`) — stars ship instantly |
| 🧾 | **Order history** | The `/orders` command shows the user's orders with statuses |
| 📊 | **Admin stats** | `/stats` for `ADMIN_IDS`: turnover, order count and total margin |
| 🤝 | **Referral program** | Referral links: 5% of each invitee's payment, payout via СБП/USDT with admin moderation and proof |
| 🧩 | **Partner bots** | A partner creates their own bot (Telegram managed bots) inside the bot, sets a markup and earns; all hosted by you (multibot) |
| 🧪 | **Test mode** | `TEST_MODE=true` — a "paid" button instead of WATA to exercise the referral/partner mechanics |
| 🌐 | **Two languages** | Russian and English interface, switch with a button |
| 🪝 | **Webhook mode** | Both Telegram and WATA run over webhooks — one HTTP server behind your reverse proxy |
| 🐳 | **Docker out of the box** | `docker compose up -d --build` brings up the bot, PostgreSQL and Redis |

---

## 🧱 Stack

- **Python 3.12** + **aiogram 3.x** (webhook mode)
- **aiohttp** — async WATA client and a single webhook server (Telegram + WATA + `/health`)
- **PostgreSQL 16** + **SQLAlchemy 2 (async)** + **Alembic**
- **Redis** — FSM state storage
- **Docker** + **docker compose**

---

## 🤖 No server required

Don't want to deploy and maintain a bot yourself? Create your own Stars-reselling
bot right inside [@nnstorestarsbot](https://t.me/nnstorestarsbot) — **no server
rental and no WATA merchant account needed**. The bot is created in a couple of
taps: you set your own markup (up to +50%) and earn on every sale, while all the
infrastructure, payments and star delivery stay on our side.

This repository is only for those who want to run everything on their own server.

---

## 🚀 Quick start

### Option A — manual

```bash
git clone https://github.com/evansvl/telegram-stars-bot.git
cd telegram-stars-bot
cp .env.example .env          # then edit .env (see below)
docker compose up -d --build  # migrations are applied automatically on start
docker compose logs -f bot    # watch the logs
```

### Option B — server script

```bash
curl -fsSL https://raw.githubusercontent.com/evansvl/telegram-stars-bot/main/scripts/install.sh -o install.sh
bash install.sh
```

`scripts/install.sh` is idempotent: it installs Docker (if missing), clones the
repository, creates `.env` from the template with hints, then builds and starts the
stack. On the first run it will ask you to fill in `.env` — edit it and run the
script again.

> ⚠️ The bot runs in **webhook mode only** — both Telegram and WATA send requests to
> your server. So a **public HTTPS domain is mandatory**: set `WEBHOOK_HOST` in `.env`
> and put a TLS reverse proxy in front (see below). Without externally reachable HTTPS
> the bot won't receive Telegram updates and won't start.

---

## 🖥️ Getting a server

The bot needs a **Linux VPS** with Docker and a public domain for webhooks
(Telegram only accepts webhooks over HTTPS). Any provider works; we use
**[Aeza](https://aeza.net/?ref=613643)** — fast, hourly-billed servers with data
centers in Russia and Europe. Signing up via
[this link](https://aeza.net/?ref=613643) also supports the project 💚

Minimum requirements:

- Ubuntu 22.04+ / Debian 12+
- 1 vCPU, 1 GB RAM, 10 GB disk
- ports 80 and 443 open, a domain (A-record) pointing at the server IP

---

## ⚙️ Configuration

All settings live in the `.env` file (created from `.env.example`). Secrets never
land in the repository.

| Variable | Required | Description |
|---|---|---|
| `BOT_TOKEN` | yes | Bot token from [@BotFather](https://t.me/BotFather) |
| `WATA_TOKEN` | yes | Merchant token from the WATA dashboard |
| `WATA_BASE_URL` | — | Digital Goods API base (default `https://dg-api.wata.pro/api`) |
| `WATA_PUBLIC_KEY_URL` | — | URL of the WATA public key for webhook signature verification |
| `MARKUP_PERCENT` | — | Markup over `minPrice` in %, result clamped to +50% (default `20`) |
| `AUTO_CONFIRM` | — | `true` — auto-confirm star delivery after payment |
| `ADMIN_IDS` | — | Comma-separated Telegram IDs of admins (for `/stats`) |
| `DATABASE_URL` | — | PostgreSQL DSN (compose already points it at the `postgres` service) |
| `REDIS_URL` | — | Redis DSN (compose points it at the `redis` service) |
| `WEBHOOK_HOST` | **yes** | Public domain behind your reverse proxy (e.g. `bot.example.com`) — the bot won't start without it |
| `WEBHOOK_PORT` | — | HTTP port of the webhook server inside the container (default `8080`) |
| `WEBHOOK_PATH` | — | WATA webhook path (default `/wata/webhook`) |
| `WEBHOOK_SECRET` | — | Extra secret: checked against the `X-Webhook-Secret` header, if set |
| `TELEGRAM_WEBHOOK_PATH` | — | Telegram webhook path (default `/tg/webhook`) |
| `TELEGRAM_WEBHOOK_SECRET` | — | Secret for the `X-Telegram-Bot-Api-Secret-Token` header; if empty, derived from `BOT_TOKEN` |
| `LOG_LEVEL` | — | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

### Where to get the tokens

- **`BOT_TOKEN`** — message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
- **`WATA_TOKEN`** — WATA merchant dashboard: [merchant.wata.pro](https://merchant.wata.pro/login)
  (sandbox: [merchant-sandbox.wata.pro](https://merchant-sandbox.wata.pro/), access granted by a WATA manager).

### How the price is computed

```
amount = minPrice × count × (1 + MARKUP_PERCENT / 100)
```

The result is hard-clamped to the range WATA requires:

```
minPrice × count  ≤  amount  ≤  minPrice × count × 1.5   (at most +50%)
```

Your profit = `amount − price − commission` (the purchase price and WATA commission
come back in the order-creation response and are stored in the DB as `margin`).

---

## 🌐 Reverse proxy & HTTPS

Inside the container the webhook server listens over **plain HTTP** on
`127.0.0.1:8080` (port from `WEBHOOK_PORT`) and serves **all** paths from a single
application: the Telegram webhook (`TELEGRAM_WEBHOOK_PATH`, default `/tg/webhook`),
the WATA webhook (`WEBHOOK_PATH`, default `/wata/webhook`), partner-bot webhooks
(`/partner/{token}`) and `/health`. So it's
enough to proxy the whole domain to `127.0.0.1:8080` — separate `location` blocks per
path aren't needed. You set up TLS termination and public exposure **yourself**,
outside `docker-compose`. Two options below; in both **replace `bot.example.com` with
your domain**.

### Option 1 — Caddy (simplest, auto-TLS)

Caddy obtains and renews a Let's Encrypt certificate on its own — nothing else to do.
Add to your `Caddyfile`:

```caddyfile
bot.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

```bash
sudo systemctl reload caddy
```

### Option 2 — Nginx (if nginx is already installed)

Start with an **HTTP block on port 80** — this is the input for the certbot plugin:

```nginx
server {
    listen 80;
    server_name bot.example.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Then issue the certificate via certbot (Let's Encrypt):

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d bot.example.com
```

> ℹ️ Port 80 here is the **starting** block. `certbot --nginx` rewrites it for you:
> it adds `listen 443 ssl` with the certificate paths and an 80 → 443 redirect. The
> working webhook ends up on **HTTPS (443)** — Telegram only accepts webhooks over
> HTTPS. If you prefer to configure TLS by hand, use `listen 443 ssl;`, the
> `ssl_certificate`/`ssl_certificate_key` directives, and a separate port-80 block
> with `return 301 https://$host$request_uri;`.

Issuing a certificate requires **open ports 80/443** and a **domain A-record**
pointing to your server. certbot sets up auto-renewal itself (the `certbot.timer`
systemd unit); verify with `sudo certbot renew --dry-run`.

---

## 🪝 Webhooks (Telegram & WATA)

The bot runs in webhook mode: a single HTTP server receives updates from both services.

### Telegram

You **don't** need to register the webhook manually — on startup the bot calls
`setWebhook` for `https://<WEBHOOK_HOST><TELEGRAM_WEBHOOK_PATH>` (default
`https://bot.example.com/tg/webhook`) itself and deletes it on shutdown. Every update
is checked against the `X-Telegram-Bot-Api-Secret-Token` secret: if
`TELEGRAM_WEBHOOK_SECRET` is empty, the secret is derived deterministically from
`BOT_TOKEN`. The main thing is a correct `WEBHOOK_HOST` and a domain reachable over
HTTPS from the outside.

### WATA

In the WATA dashboard (terminal/notifications section) set the webhook URL:

```
https://<your-domain><WEBHOOK_PATH>    e.g. https://bot.example.com/wata/webhook
```

The bot verifies the `X-Signature` (RSA SHA-512) against the WATA public key, logs the
full payload, and then **reconciles the order status via `GET /stars/order/{id}`** —
i.e. the API response is treated as the source of truth, not the notification body.
The GET to WATA is rate-limited (1 request / 30 s per object), so polling is used only
as a fallback.

> 📜 Docker logs are human-readable (`docker compose logs -f bot`):
> `2026-06-19 12:00:00 INFO     app.main: telegram webhook set url=...`.

---

## 📦 Order statuses

```
Pending  →  Review  →  Paid / Refunded  →  Success / Fail
awaiting    awaiting     paid/refunded       fulfilled/failed
payment     confirm
```

- **Pending** — the link is created, awaiting payment.
- **Review** — paid, awaiting merchant confirmation. With `AUTO_CONFIRM=true` the bot
  immediately calls `confirm` → **Paid**.
- **Paid** — confirmed, stars are being delivered. **Success** — stars delivered.
- **Refunded** — order rejected, money refunded to the payer. **Fail** — delivery failed.

---

## 🔁 Updating

From the project directory on the server:

```bash
bash scripts/update.sh
```

The script runs `git pull`, rebuilds and restarts the containers (migrations apply on
start), prunes old images and prints the current version (git SHA).

---

## ❓ FAQ

**Payment went through, but the stars didn't arrive.**
The bot notifies about payment automatically via the WATA webhook — there is no manual
check button. If no notification arrives, make sure the WATA webhook points at
`https://<domain><WEBHOOK_PATH>` and that this address is reachable from the outside
(`docker compose logs -f bot` shows incoming notifications). WATA rate-limits
`GET /stars/order` (1 request / 30s per order), so when that call is unavailable the bot
takes the status straight from the webhook body.

**Error `STR_1002` when entering the recipient.**
WATA couldn't find such a Telegram account. Check the @username (no `@`, 5–32 chars).

**Error `STR_1003`.**
The number of stars is outside the 50–50,000 range.

**How do I change the markup?**
Change `MARKUP_PERCENT` in `.env` and restart: `docker compose up -d`. Remember the
+50% ceiling — a larger value will be clamped anyway.

**Do I need a deposit in WATA?**
No. It uses the "Telegram Stars + Acquiring" product: the customer pays via the link,
WATA delivers the stars, and your profit is the built-in margin.

**How do I switch the bot's language?**
The `/language` command or the "🌐 Язык / Language" menu button. The choice is saved
per-user; on first contact the language is detected from the Telegram `language_code`
(Russian by default).

---

## ⚠️ Disclaimer

This is an educational project distributed "as is". You are solely responsible for the
legality of reselling Telegram Stars in your jurisdiction, for paying taxes, and for
complying with the WATA and Telegram terms. The authors are not affiliated with WATA
or Telegram and bear no responsibility for your use of the bot.

---

## 📄 License

[MIT](LICENSE) © evansvl

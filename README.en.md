<div align="center">

**[🇷🇺 Русский](README.md) · 🇬🇧 English**

# ⭐ Telegram Stars Bot — open-source bot to sell Telegram Stars

**A ready-made open-source bot and shop template for selling Telegram Stars with
your own markup.** A user enters the recipient's `@username` and the number of
stars, pays **by card, SBP or crypto (TON)**, and the stars are delivered
automatically. In short — a ready-to-run script for auto-selling Telegram Stars
that you deploy on your own server in ~30–40 minutes via the
[step-by-step guide](#-step-by-step-install-for-beginners). Payments and delivery
go through **WATA** (card/SBP) and **GreenGamePay** (TON). The interface is
bilingual — **Russian and English** — with on-the-fly switching.

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
&nbsp;
[![aiogram](https://img.shields.io/badge/aiogram-3.x-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://docs.aiogram.dev/)
&nbsp;
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://docs.docker.com/compose/)
&nbsp;
![License](https://img.shields.io/badge/License-MIT-111A2B?style=for-the-badge&logo=opensourceinitiative&logoColor=white)
&nbsp;
[![Telegram](https://img.shields.io/badge/Channel-@evansvl__stars-229ED9?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/evansvl_stars)

**📢 Updates and announcements — on the Telegram channel [@evansvl_stars](https://t.me/evansvl_stars).**

> ⚠️ A self-hosted server project. You deploy the bot on your own server and are
> solely responsible for the legality of reselling Stars and for taxes. The
> project is not affiliated with the WATA provider.

### [✨ Features](#-features) · [🤖 No server](#-no-server-required) · [🧱 Stack](#-stack) · [🚀 Step-by-step install](#-step-by-step-install-for-beginners) · [⚙️ Configuration](#️-configuration) · [🌐 Reverse proxy & HTTPS](#-reverse-proxy--https) · [🪝 Webhooks](#-webhooks-telegram--wata) · [📦 Statuses](#-order-statuses) · [🔁 Updating](#-updating) · [❓ FAQ](#-faq) · [⚠️ Disclaimer](#️-disclaimer) · [📄 License](#-license)

</div>

---

## ✨ Features

| | Feature | Description |
|---|---|---|
| ⭐ | **Sell Telegram Stars** | Buy stars for any @username (from 50 to 50,000) via the WATA Digital Goods API |
| 💰 | **Configurable margin** | Markup over the minimum price via `MARKUP_PERCENT`, clamped to +50% |
| 💳 | **Card / SBP payment** | One-off WATA payment link: bank card or SBP |
| 💎 | **Crypto (TON) payment** | A second rail: pay in TON via GreenGamePay with its **own** markup (`CRYPTO_MARKUP_PERCENT`). Delivery is gated on the GGP balance: once funds land, stars ship via `buyStars` |
| 🪙 | **Independent payment methods** | Card and crypto enable separately and work together or alone. No WATA? Leave `WATA_TOKEN` empty and run crypto-only |
| 💱 | **Two ⭐ rates** | The menu honestly shows both independent rates, card (WATA) and crypto (GGP), without promising that either one is always cheaper |
| 🔔 | **Payment tracking** | WATA webhook with RSA signature verification + status reconciliation via API; GGP balance check (`check_balance`) for crypto |
| ✅ | **Auto-confirm** | Automatic `confirm` after payment (`AUTO_CONFIRM=true`) — stars ship instantly |
| 🧾 | **Order history** | The “My orders” button shows the user's orders with statuses |
| 📊 | **Admin stats** | `/stats` for `ADMIN_IDS`: turnover, order count and total margin |
| 🤝 | **Referral program** | Referral links: 5% of each invitee's payment, payout via СБП/USDT with admin moderation and proof |
| 🧩 | **Partner bots** | A partner creates their own bot (Telegram managed bots) inside the bot, sets their own markup (up to `50% − MARKUP_PERCENT`) and earns it on sales; you keep `PARTNER_COMMISSION_PERCENT`% of their markup; all hosted by you (multibot) |
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

## 🚀 Step-by-step install (for beginners)

> Never worked with a server before? No problem — just follow the steps in order.
> The whole thing takes **~30–40 minutes**. Everything you need to type is in `code`
> blocks you can copy as-is.
>
> **Already experienced?** In short: rent a VPS, point a domain at its IP, run
> `curl -fsSL https://raw.githubusercontent.com/evansvl/telegram-stars-bot/main/scripts/install.sh | bash`,
> fill in `.env` (`BOT_TOKEN`, `WATA_TOKEN`, `WEBHOOK_HOST`, `ADMIN_IDS`), put Caddy
> in front of `127.0.0.1:8080`, restart. Details below.

### What you'll need

- A **Telegram** account.
- A Linux **VPS** — from ~$3/mo (step 3).
- A **domain** — from ~$2/yr, or a free subdomain (step 4).
- **WATA** access to accept card payments (step 2).

---

### Step 1. Create a bot in Telegram → `BOT_TOKEN`

1. Open [@BotFather](https://t.me/BotFather) in Telegram.
2. Send `/newbot`, pick a name and a `username` (must end in `bot`).
3. BotFather sends a **token** like `123456789:AAE-Ct...` — copy it.

> 🔒 The token is your bot's password. Never share or publish it.

---

### Step 2. Get WATA access → `WATA_TOKEN`

WATA lets the bot accept card/SBP payments and deliver stars (no deposit needed).

1. Sign up at [merchant.wata.pro](https://merchant.wata.pro/login) and enable the
   **“Telegram Stars + Acquiring”** product.
2. Find the **API token** in the dashboard — that's `WATA_TOKEN`.

> There's a sandbox at [merchant-sandbox.wata.pro](https://merchant-sandbox.wata.pro/)
> (access granted by a WATA manager).

---

### Step 3. Rent a server (VPS)

The bot needs a **Linux VPS** with a public IP. Any provider works; we use
**[Aeza](https://aeza.net/?ref=613643)** — signing up via
[this link](https://aeza.net/?ref=613643) also supports the project 💚

- OS: **Ubuntu 22.04+** (or Debian 12+).
- Plan: **1 vCPU, 1 GB RAM, 10 GB** disk — plenty.
- Ports **80 and 443** must be open.

After creating it, note the server's **IP address** and **root password**.

---

### Step 4. Point a domain at the server

Telegram delivers updates over **HTTPS** only, which needs a domain.

1. Buy a domain (e.g. [namecheap.com](https://www.namecheap.com)) or grab a free
   subdomain (e.g. [duckdns.org](https://www.duckdns.org)).
2. In DNS settings create an **A-record**: name `bot`, value = your **server IP**
   from step 3, e.g. `bot.example.com → 12.34.56.78`.
3. Wait 5–30 minutes for the record to propagate.

Remember `bot.example.com` — that's your **`WEBHOOK_HOST`**.

---

### Step 5. Connect to the server over SSH

- **Windows:** open **PowerShell** (or “Terminal”) and run:
  ```powershell
  ssh root@YOUR-SERVER-IP
  ```
- **macOS / Linux:** open “Terminal” and run the same.

On first connect it asks `yes/no` — type `yes`. Then enter the root password
(characters **won't show** as you type — that's normal, just type and press Enter).

---

### Step 6. Install the bot with one command

On the server, run:

```bash
curl -fsSL https://raw.githubusercontent.com/evansvl/telegram-stars-bot/main/scripts/install.sh -o install.sh
bash install.sh
```

The script installs Docker, downloads the project and creates the `.env` settings
file. At the end it says *“edit .env and run the script again”* — let's do that.

---

### Step 7. Fill in the settings — the `.env` file

Open it in an editor:

```bash
cd telegram-stars-bot
nano .env
```

Set at least these lines (leave the rest as-is):

```dotenv
BOT_TOKEN=123456789:AAE...           # from step 1
WATA_TOKEN=your-wata-token           # from step 2
WEBHOOK_HOST=bot.example.com         # from step 4 (no https:// and no trailing /)
ADMIN_IDS=123456789                  # your Telegram ID (see below)
MARKUP_PERCENT=20                    # your markup, %
```

**How to find your Telegram ID:** message [@userinfobot](https://t.me/userinfobot) —
it replies with your number.

Save in `nano`: **Ctrl+O** → **Enter**, exit: **Ctrl+X**.

> 💎 Crypto (TON) payment can be enabled later — see the “(Optional) Crypto (TON)
> payment” block below.

---

### Step 8. Enable HTTPS (required)

Telegram only accepts webhooks over HTTPS. The easiest option is **Caddy** — it
obtains and renews the SSL certificate for you.

```bash
# install Caddy (Ubuntu/Debian)
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

Open the config with `sudo nano /etc/caddy/Caddyfile` and add (replace the domain):

```caddyfile
bot.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

Apply it: `sudo systemctl reload caddy`.

> More detail (and an Nginx option) is in [🌐 Reverse proxy & HTTPS](#-reverse-proxy--https).

---

### Step 9. Start the bot

```bash
bash install.sh            # builds and starts the containers
docker compose logs -f bot # watch the logs (exit with Ctrl+C)
```

You should see a line like:

```
INFO  app.main: telegram webhook set url=https://bot.example.com/tg/webhook
```

That means it works 🎉 (DB migrations run automatically on start).

---

### Step 10. Verify and set the WATA webhook

1. Open your bot in Telegram and tap **/start** — the menu appears. The
   **“🛠 Admin”** button shows up if your ID is in `ADMIN_IDS`.
2. In the WATA dashboard, set the **webhook URL**:
   ```
   https://bot.example.com/wata/webhook
   ```

Done — the bot accepts orders and payments. 🚀

---

### (Optional) Crypto (TON) payment

**Where to register:** create an account with **GreenGamePay** —
[пополнистим.рф](https://xn--h1aahgceagbyl.xn--p1ai/). After signing up, your
dashboard gives you a login/password and a **TON deposit address** — put those in
`.env`.

How it works: the buyer pays TON to your GGP deposit address → your GGP balance grows
→ the bot sees the funds (`check_balance`) and delivers stars via `buyStars`. Since
the funds always reach you, delivery is gated **on the balance**, not on scanning the
chain: the oldest unpaid crypto order ships first (FIFO).

Crypto is an independent payment method: it turns on by itself as soon as the three
fields `GGP_USERNAME`, `GGP_PASSWORD` and `GGP_TON_ADDRESS` are set. Put them in `.env`
and restart (`docker compose up -d`):

```dotenv
GGP_USERNAME=your-ggp-login
GGP_PASSWORD=your-ggp-password
GGP_TON_ADDRESS=ton-deposit-address        # from your GGP dashboard
```

**Crypto only (no WATA).** Both rails are independent — just leave `WATA_TOKEN` empty
and the card rail is hidden everywhere. At least one rail (card and/or crypto) must be
configured.

See [docs/ECONOMICS.md](docs/ECONOMICS.md) for the crypto economics.

---

### Something went wrong?

- **Check the logs:** `docker compose logs -f bot`.
- **Bot won't start** → usually `WEBHOOK_HOST` is empty. Make sure the domain opens
  over `https://` in a browser (i.e. Caddy issued a certificate).
- **No response in the bot** → make sure ports 80/443 are open and the domain's
  A-record points at the server IP.
- **Payments don't go through** → check `WATA_TOKEN` and that the WATA webhook URL is
  correct (step 10). Common errors are in the [❓ FAQ](#-faq).

---

## ⚙️ Configuration

All settings live in the `.env` file (created from `.env.example`). Secrets never
land in the repository.

| Variable | Required | Description |
|---|---|---|
| `BOT_TOKEN` | yes | Bot token from [@BotFather](https://t.me/BotFather) |
| `WATA_TOKEN` | for card | Merchant token from the WATA dashboard. Set → card payment on; empty → card off |
| `WATA_BASE_URL` | — | Digital Goods API base (default `https://dg-api.wata.pro/api`) |
| `WATA_PUBLIC_KEY_URL` | — | URL of the WATA public key for webhook signature verification |
| `MARKUP_PERCENT` | — | Markup over `minPrice` in % (card/WATA), result clamped to +50% (default `20`) |
| `AUTO_CONFIRM` | — | `true` — auto-confirm star delivery after payment |
| `CRYPTO_MARKUP_PERCENT` | — | Markup for crypto payment in % (its own, usually lower; no +50% clamp) |
| `GGP_USERNAME` / `GGP_PASSWORD` | for crypto | GreenGamePay credentials (via `.env` only). Set together with the address → crypto on |
| `GGP_BASE_URL` | — | GGP API base (default `пополнистим.рф` in IDNA form) |
| `GGP_TON_ADDRESS` | for crypto | TON deposit address that funds the GGP balance (from the GreenGamePay dashboard). Required to enable crypto |
| `GGP_DEPOSIT_MEMO` | — | Payment comment = your GGP login (default `evansvl`) |
| `TON_POLL_INTERVAL` | — | GGP balance check interval in seconds (default `20`) |
| `CRYPTO_PAYMENT_TTL_MIN` | — | Validity of a quoted TON amount in minutes (default `30`) |
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

**Crypto (TON via GGP)** is a separate rail with its own markup and no WATA clamp.
GGP returns a wholesale `{ton, rub}` price per quantity; the buyer pays
`ton × (1 + (CRYPTO_MARKUP_PERCENT + partner) / 100)`, rounded up. The buyer sends
TON to your GGP deposit address and taps “✅ I paid”. Delivery is gated **on the GGP
balance**, not on scanning the chain: the bot checks `check_balance`, and as soon as
there are funds it delivers the oldest unpaid crypto order via GGP `buyStars` (FIFO).
A background worker also completes orders if the buyer never taps the button. If a
buyer paid but no stars arrived, the bot tells them to contact **@nnstorecontactbot**.
See [docs/ECONOMICS.md](docs/ECONOMICS.md) for the math.

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

**How do I make a bot to sell Telegram Stars?**
This is a ready-made open-source template for exactly that. Deploy it on your own
server via the [step-by-step guide](#-step-by-step-install-for-beginners): create a
bot in BotFather, get a WATA token, fill in `.env`, set up HTTPS — and your Stars shop
is live. You set the markup yourself (`MARKUP_PERCENT`); stars are delivered automatically.

**Is it a ready-to-run script/template or do I need to write code?**
It's ready to run: payment acceptance (card/SBP/TON crypto), automatic star delivery, a
referral program, partner bots and an admin panel are already included. You only need a
server, a domain and tokens (BotFather + WATA). The code is open — tweak it if you want.

**Can I sell stars for crypto (TON)?**
Yes. Besides card/SBP via WATA, there's a second method — crypto (TON) via GreenGamePay
with its own, lower markup (`CRYPTO_MARKUP_PERCENT`). See the “(Optional) Crypto (TON)
payment” block in the install guide.

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
The “🌐 Language / Язык” menu button. The choice is saved
per-user; on first contact the language is detected from the Telegram `language_code`
(Russian by default).

---

## 📢 Follow updates

New releases, fixes and announcements — on the Telegram channel **[@evansvl_stars](https://t.me/evansvl_stars)**.

---

## 💎 Support development

If this project was useful, you can support the developer. Thank you! ❤️

- **SBP / RU card:** https://yookassa.ru/my/i/aJRiTyq5D3VB/l
- **CryptoBot:** https://t.me/send?start=IVftnggXmRv8
- **TON:** `UQAtdMEig3Wl_D3FNx4RU3RhxnoJI3IizGxrrNj3O8Q-fDpK`
- **USDT (TRC20):** `TBW9TFUh93U1G5eTT1VTsZw51L669khCiz`

---

## ⚠️ Disclaimer

This is an educational project distributed "as is". You are solely responsible for the
legality of reselling Telegram Stars in your jurisdiction, for paying taxes, and for
complying with the WATA and Telegram terms. The authors are not affiliated with WATA
or Telegram and bear no responsibility for your use of the bot.

---

## 📄 License

[MIT](LICENSE) © evansvl

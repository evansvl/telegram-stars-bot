# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

A bilingual (RU primary / EN secondary) Telegram bot that sells Telegram Stars via the **WATA Digital Goods API**, with a referral program, partner-owned hosted bots, and admin-moderated withdrawals. Python 3.12, aiogram 3.x, aiohttp, SQLAlchemy 2 async, PostgreSQL, Redis, Alembic.

## Commands

A local `.venv` exists. On this Windows host use the Bash tool with the venv interpreter directly:

```bash
.venv/Scripts/python.exe -m ruff check .          # lint (run before committing)
.venv/Scripts/python.exe -m ruff check --fix .    # autofix (import sort etc.)
.venv/Scripts/python.exe -m pytest -q             # full test suite
.venv/Scripts/python.exe -m pytest tests/test_wata_parsing.py::test_message_key_mapping  # single test
.venv/Scripts/python.exe -m mypy app              # type check
```

Docker (production / integration — cannot run in this dev environment, no Docker here):

```bash
docker compose up -d --build      # bot + postgres + redis; entrypoint runs `alembic upgrade head` first
docker compose logs -f bot
```

Alembic migrations auto-apply on container start (`entrypoint.sh`). To add one, create
`alembic/versions/000N_<slug>.py` with `down_revision` pointing at the previous head
(chain is `0001_initial` → `0002` → `0003` → `0004`).

### Dependency / Python-version caveat

`requirements.txt` pins versions for **Python 3.12** (the Docker image). This dev host only has
Python 3.14, for which the pinned `aiohttp`/`asyncpg`/`pydantic-core` have no wheels, so the local
`.venv` was created with **unpinned latest** versions instead. Code therefore must stay compatible
with both — it currently runs on aiogram 3.29 locally and the pinned 3.29 in Docker. Avoid APIs that
exist only in one minor version.

## Architecture

### One process, one aiohttp server (webhook mode only)

`app/main.py` is the entrypoint. The bot does **not** long-poll. A single aiohttp app (built in
`app/webhook/server.py`, extended in `main.py`) serves every inbound path behind a user-managed TLS
reverse proxy:

- `TELEGRAM_WEBHOOK_PATH` (default `/tg/webhook`) — main bot updates via aiogram `SimpleRequestHandler`
- `WEBHOOK_PATH` (default `/wata/webhook`) — WATA payment notifications (custom handler)
- `/partner/{bot_token}` — all partner bots, via aiogram `TokenBasedRequestHandler` (multibot)
- `/health`

`WEBHOOK_HOST` is **required** or the process exits. On startup the bot calls `set_webhook` for itself
and for every active partner bot; on shutdown it deletes its webhook. The Telegram secret token is
`TELEGRAM_WEBHOOK_SECRET` or, if empty, `sha256(BOT_TOKEN)`.

### Layering

`handlers (aiogram) → services → repositories → db.session` plus a `wata` client package and pure
`pricing`/`i18n` leaf modules.

- **`app/services.py`** is the core. Three service classes, each holding `Settings` + `Database`:
  - `OrderService` — pricing/quote, `create_order`, `sync_order`, stats, language cache, `simulate_payment` (test mode).
  - `ReferralService` — referral attribution, `credit_for_order` (idempotent per order), balance, withdrawals.
  - `PartnerService` — partner bot CRUD, per-bot markup, markup cache.
- **`app/db/repositories.py`** — thin per-aggregate repositories; each takes an `AsyncSession`. Upserts use Postgres `ON CONFLICT` (`pg_insert`).
- **`app/db/session.py`** — `Database.session()` async context manager commits on success, rolls back on error. **`expire_on_commit=False`**, so returning ORM objects from a service and reading their attributes after the session closes is safe (the codebase relies on this).

### Dependency injection into handlers

`main.py` puts shared singletons on the Dispatcher (`dp["settings"]`, `dp["service"]`, `dp["referral"]`,
`dp["partner"]`); aiogram injects them into handlers by matching the parameter name. `LanguageMiddleware`
(`app/bot/middleware.py`) injects `lang: str` into every message/callback handler by resolving the user's
stored/Telegram language. The same `dp` serves the main bot **and** all partner bots, so handlers must be
bot-agnostic — use `event.bot.id` to tell which bot is running (e.g. to look up a partner markup).

### Money & pricing invariant (`app/pricing.py`)

All money is `Decimal`. WATA constrains the order amount: `minPrice*count ≤ amount ≤ minPrice*count*1.5`
(**markup capped at +50%**). `compute_amount` applies the markup and clamps into that range. This +50% cap
is a hard ceiling that ripples into the partner program: the partner markup is added **on top of** the
operator markup (`MARKUP_PERCENT`) and the total is clamped, so a partner's effective max markup is
`50% − operator%` (`PartnerService.max_partner_markup`). Partner earning = (amount at total markup) −
(amount at operator-only markup).

### WATA integration (`app/wata/`)

`client.py` is a typed async wrapper with retry/backoff. `signature.py` verifies the webhook
`X-Signature` (RSA SHA-512, PKCS1v15) against WATA's public key. **The webhook body is never trusted as
the source of truth** — `OrderService.sync_order` re-fetches authoritative status via `GET /stars/order/{id}`
(and auto-confirms `Review → Paid` when `AUTO_CONFIRM`). Errors raise `WataApiError`/`WataNetworkError`,
whose `message_key()` returns an i18n key (not user text).

### Order & money flows

Order status (`OrderStatusEnum`, a `StrEnum`): `New → Pending → Review → Paid/Refunded → Success/Fail`
(plus `Error`). Earnings are credited when an order reaches `Paid`/`Success`, via `credit_for_order`
(idempotent — one `referral_earnings` row per `order_id`): a partner-bot order pays the partner its
pre-computed `partner_earning`; otherwise the buyer's referrer earns `REFERRAL_PERCENT` (5%) of the gross.
Both feed one wallet; `WithdrawalService` flow: request (locks balance) → admin approves with proof
(TRON/TON link or PDF `file_id`) or rejects with a reason.

### Test mode

`TEST_MODE=true` makes `create_order` skip WATA entirely (no payment link); the buyer gets an "I paid (TEST)"
button whose handler calls `simulate_payment` and runs the real payout flow. Use it to exercise referral and
partner logic without money.

## Conventions

- **i18n is mandatory and enforced.** All user-facing text lives in `app/bot/i18n.py` `TEXTS` as
  `{key: {"ru": ..., "en": ...}}`; render with `t(key, lang, **kwargs)`. `tests/test_wata_parsing.py::test_i18n_completeness`
  fails if any key lacks `ru` or `en`. Never hardcode user strings in handlers.
- **Button-driven, single-message UI.** The only slash commands are `/start` (also the referral deep-link
  target `?start=ref_<id>` and the native Menu button) and the hidden admin `/stats`. Everything else is
  inline buttons. The `_render()` helper in `handlers.py` **edits the existing message in place** (delete+resend
  fallback) to avoid piling up chat messages — prefer it over `message.answer` for navigation.
- **Ruff** (line length 100). `RUF001/2/3` are ignored because Cyrillic text triggers false "ambiguous
  Unicode" warnings — keep that in mind, don't "fix" Russian characters.
- `.gitattributes` forces **LF** for `*.sh`, `*.py`, `Dockerfile` (shell scripts must be LF to run in Linux containers).
- README is bilingual: `README.md` (RU, primary) + `README.en.md` (EN). Keep both in sync when documenting features.

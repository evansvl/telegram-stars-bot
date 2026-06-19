<div align="center">

**🇷🇺 Русский · [🇬🇧 English](README.en.md)**

# ⭐ Telegram Stars Bot

**Продавайте Telegram Stars прямо в боте — с вашей наценкой.**
Пользователь выбирает получателя и количество звёзд, оплачивает картой или
через СБП, а звёзды доставляются автоматически. Платежи и выдача — через WATA.
Бот двуязычный: интерфейс на **русском и английском** с переключением на лету.

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
&nbsp;
[![aiogram](https://img.shields.io/badge/aiogram-3.x-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://docs.aiogram.dev/)
&nbsp;
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://docs.docker.com/compose/)
&nbsp;
![License](https://img.shields.io/badge/License-MIT-111A2B?style=for-the-badge&logo=opensourceinitiative&logoColor=white)

> ⚠️ Самостоятельный серверный проект. Вы разворачиваете бота на своём сервере
> и сами отвечаете за легальность перепродажи Stars и налоги. С провайдером
> WATA проект не аффилирован.

### [✨ Возможности](#-возможности) · [🤖 Без сервера](#-без-своего-сервера) · [🧱 Стек](#-стек) · [🚀 Быстрый старт](#-быстрый-старт) · [🖥️ Сервер](#️-где-взять-сервер) · [⚙️ Конфигурация](#️-конфигурация) · [🌐 Reverse proxy и HTTPS](#-reverse-proxy-и-https) · [🪝 Вебхуки](#-вебхуки-telegram-и-wata) · [📦 Статусы](#-статусы-заказа) · [🔁 Обновление](#-обновление) · [❓ FAQ](#-faq) · [⚠️ Дисклеймер](#️-дисклеймер) · [📄 Лицензия](#-лицензия)

</div>

---

## ✨ Возможности

| | Возможность | Описание |
|---|---|---|
| ⭐ | **Продажа Telegram Stars** | Покупка звёзд для любого @username (от 50 до 50 000) через WATA Digital Goods API |
| 💰 | **Настраиваемая маржа** | Наценка над минимальной ценой задаётся через `MARKUP_PERCENT`, кламп до +50% |
| 💳 | **Оплата картой / СБП** | Одноразовая платёжная ссылка WATA: банковская карта или СБП |
| 🔔 | **Трекинг оплаты** | Приём вебхука WATA с проверкой RSA-подписи + сверка статуса по API |
| ✅ | **Авто-подтверждение** | Автоматический `confirm` после оплаты (`AUTO_CONFIRM=true`) — звёзды уходят сразу |
| 🧾 | **История заказов** | Команда `/orders` показывает заказы пользователя со статусами |
| 📊 | **Админ-статистика** | `/stats` для `ADMIN_IDS`: оборот, число заказов и суммарная маржа |
| 🤝 | **Партнёрская программа** | Реферальные ссылки: 5% с каждой оплаты приглашённого, вывод на СБП/USDT с модерацией и пруфом |
| 🌐 | **Два языка** | Русский и английский интерфейс, переключение кнопкой |
| 🪝 | **Webhook-режим** | И Telegram, и WATA работают через вебхуки — один HTTP-сервер за вашим reverse proxy |
| 🐳 | **Docker «из коробки»** | `docker compose up -d --build` поднимает бота, PostgreSQL и Redis |

---

## 🧱 Стек

- **Python 3.12** + **aiogram 3.x** (webhook-режим)
- **aiohttp** — async-клиент WATA и единый сервер вебхуков (Telegram + WATA + `/health`)
- **PostgreSQL 16** + **SQLAlchemy 2 (async)** + **Alembic**
- **Redis** — хранилище FSM-состояний
- **Docker** + **docker compose**

---

## 🤖 Без своего сервера

Не хотите разворачивать и обслуживать бота сами? Создайте собственного бота для
продажи звёзд прямо внутри [@nnstorestarsbot](https://t.me/nnstorestarsbot) —
**без аренды сервера и без подключения мерчанта WATA**. Бот создаётся в пару
нажатий: вы задаёте свою наценку (до +50%) и получаете доход с каждой продажи, а
вся инфраструктура, приём платежей и выдача звёзд остаются на нашей стороне.

Этот репозиторий нужен только тем, кто хочет поднять всё на собственном сервере.

---

## 🚀 Быстрый старт

### Вариант А — вручную

```bash
git clone https://github.com/evansvl/telegram-stars-bot.git
cd telegram-stars-bot
cp .env.example .env          # затем отредактируйте .env (см. ниже)
docker compose up -d --build  # миграции применяются автоматически при старте
docker compose logs -f bot    # смотрим логи
```

### Вариант Б — скрипт для сервера

```bash
curl -fsSL https://raw.githubusercontent.com/evansvl/telegram-stars-bot/main/scripts/install.sh -o install.sh
bash install.sh
```

`scripts/install.sh` идемпотентен: установит Docker (если нет), склонирует репозиторий,
создаст `.env` из шаблона с подсказками, соберёт и запустит стек. После первого
запуска он попросит заполнить `.env` — отредактируйте и запустите скрипт повторно.

> ⚠️ Бот работает **только в webhook-режиме** — и Telegram, и WATA шлют запросы на
> ваш сервер. Поэтому **публичный HTTPS-домен обязателен**: задайте `WEBHOOK_HOST` в
> `.env` и поднимите reverse proxy с TLS (см. ниже). Без доступного снаружи HTTPS бот
> не получит обновления Telegram и не запустится.

---

## 🖥️ Где взять сервер

Боту нужен **Linux-VPS** с Docker и публичным доменом для вебхуков (Telegram
принимает вебхуки только по HTTPS). Подойдёт любой провайдер; мы используем
**[Aeza](https://aeza.net/?ref=613643)** — быстрые серверы с почасовой оплатой и
дата-центрами в РФ и Европе. Зарегистрировавшись по
[этой ссылке](https://aeza.net/?ref=613643), вы заодно поддержите проект 💚

Минимальные требования:

- Ubuntu 22.04+ / Debian 12+
- 1 vCPU, 1 ГБ RAM, 10 ГБ диска
- открытые порты 80 и 443, домен (A-запись) на IP сервера

---

## ⚙️ Конфигурация

Все настройки — в файле `.env` (создаётся из `.env.example`). Секреты в репозиторий
не попадают.

| Переменная | Обязательна | Описание |
|---|---|---|
| `BOT_TOKEN` | да | Токен бота от [@BotFather](https://t.me/BotFather) |
| `WATA_TOKEN` | да | Токен мерчанта из ЛК WATA |
| `WATA_BASE_URL` | — | База Digital Goods API (по умолчанию `https://dg-api.wata.pro/api`) |
| `WATA_PUBLIC_KEY_URL` | — | URL публичного ключа WATA для проверки подписи вебхука |
| `MARKUP_PERCENT` | — | Наценка над `minPrice` в %, итог клампится до +50% (по умолч. `20`) |
| `AUTO_CONFIRM` | — | `true` — авто-подтверждение выдачи звёзд после оплаты |
| `ADMIN_IDS` | — | Telegram ID администраторов через запятую (для `/stats`) |
| `DATABASE_URL` | — | DSN PostgreSQL (в compose уже настроен на сервис `postgres`) |
| `REDIS_URL` | — | DSN Redis (в compose настроен на сервис `redis`) |
| `WEBHOOK_HOST` | **да** | Публичный домен за вашим reverse proxy (напр. `bot.example.com`) — без него бот не стартует |
| `WEBHOOK_PORT` | — | HTTP-порт webhook-сервера внутри контейнера (по умолч. `8080`) |
| `WEBHOOK_PATH` | — | Путь вебхука WATA (по умолч. `/wata/webhook`) |
| `WEBHOOK_SECRET` | — | Доп. секрет: сверяется в заголовке `X-Webhook-Secret`, если задан |
| `TELEGRAM_WEBHOOK_PATH` | — | Путь вебхука Telegram (по умолч. `/tg/webhook`) |
| `TELEGRAM_WEBHOOK_SECRET` | — | Секрет заголовка `X-Telegram-Bot-Api-Secret-Token`; если пусто — генерируется из `BOT_TOKEN` |
| `LOG_LEVEL` | — | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

### Где взять токены

- **`BOT_TOKEN`** — напишите [@BotFather](https://t.me/BotFather) → `/newbot` → скопируйте токен.
- **`WATA_TOKEN`** — личный кабинет мерчанта WATA: [merchant.wata.pro](https://merchant.wata.pro/login)
  (sandbox: [merchant-sandbox.wata.pro](https://merchant-sandbox.wata.pro/), доступ выдаёт менеджер WATA).

### Как считается цена

```
amount = minPrice × count × (1 + MARKUP_PERCENT / 100)
```

Итог жёстко клампится в диапазон, который требует WATA:

```
minPrice × count  ≤  amount  ≤  minPrice × count × 1.5   (максимум +50%)
```

Ваш доход = `amount − price − commission` (закупочная цена и комиссия WATA приходят
в ответе на создание заказа и сохраняются в БД как `margin`).

---

## 🌐 Reverse proxy и HTTPS

Webhook-сервер внутри контейнера слушает **обычный HTTP** на `127.0.0.1:8080`
(порт из `WEBHOOK_PORT`) и обслуживает **оба** пути одним приложением: вебхук
Telegram (`TELEGRAM_WEBHOOK_PATH`, по умолч. `/tg/webhook`), вебхук WATA
(`WEBHOOK_PATH`, по умолч. `/wata/webhook`) и `/health`. Поэтому достаточно
проксировать весь домен на `127.0.0.1:8080` — отдельные `location` для каждого пути
не нужны. TLS-терминацию и публикацию наружу вы настраиваете **сами**, вне
`docker-compose`. Ниже — два варианта на выбор; в обоих **замените `bot.example.com`
на свой домен**.

### Вариант 1 — Caddy (проще, авто-TLS)

Caddy сам получает и продлевает сертификат Let's Encrypt — больше ничего делать
не нужно. Добавьте в ваш `Caddyfile`:

```caddyfile
bot.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

```bash
sudo systemctl reload caddy
```

### Вариант 2 — Nginx (если nginx уже стоит)

Начните с **HTTP-блока на порту 80** — это вход для плагина certbot:

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

Затем выпустите сертификат через certbot (Let's Encrypt):

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d bot.example.com
```

> ℹ️ Порт 80 здесь — это **исходный** блок. `certbot --nginx` сам перепишет его:
> добавит `listen 443 ssl` с путями к сертификату и редирект 80 → 443. Итоговый
> рабочий вебхук будет на **HTTPS (443)** — Telegram принимает вебхуки только по
> HTTPS. Если предпочитаете прописать TLS вручную, используйте `listen 443 ssl;`,
> директивы `ssl_certificate`/`ssl_certificate_key` и отдельный 80-й блок с
> `return 301 https://$host$request_uri;`.

Для выпуска сертификата нужны **открытые порты 80/443** и **A-запись домена** на
ваш сервер. Автопродление certbot настраивает сам (systemd-таймер `certbot.timer`);
проверить можно командой `sudo certbot renew --dry-run`.

---

## 🪝 Вебхуки (Telegram и WATA)

Бот работает в webhook-режиме: один HTTP-сервер принимает обновления от обоих сервисов.

### Telegram

Регистрировать вебхук вручную **не нужно** — при старте бот сам вызывает
`setWebhook` на `https://<WEBHOOK_HOST><TELEGRAM_WEBHOOK_PATH>` (по умолчанию
`https://bot.example.com/tg/webhook`) и удаляет его при остановке. Каждое обновление
проверяется по секрету `X-Telegram-Bot-Api-Secret-Token`: если `TELEGRAM_WEBHOOK_SECRET`
пуст, секрет детерминированно выводится из `BOT_TOKEN`. Главное — чтобы `WEBHOOK_HOST`
был корректным и домен был доступен по HTTPS снаружи.

### WATA

В личном кабинете WATA (раздел терминала/уведомлений) укажите URL вебхука:

```
https://<ваш-домен><WEBHOOK_PATH>      напр. https://bot.example.com/wata/webhook
```

Бот проверяет подпись `X-Signature` (RSA SHA-512) по публичному ключу WATA, логирует
весь payload и затем **сверяет статус заказа через `GET /stars/order/{id}`** — то есть
истинным источником считается ответ API, а не тело уведомления. GET к WATA лимитирован
(1 запрос / 30 c на объект), поэтому поллинг используется только как fallback.

> 📜 Логи в Docker — человекочитаемые (`docker compose logs -f bot`):
> `2026-06-19 12:00:00 INFO     app.main: telegram webhook set url=...`.

---

## 📦 Статусы заказа

```
Pending  →  Review  →  Paid / Refunded  →  Success / Fail
ожидает     ждёт        оплачен/возврат      исполнен/ошибка
оплаты      подтвержд.
```

- **Pending** — ссылка создана, ждём оплату.
- **Review** — оплачено, ждёт подтверждения мерчантом. При `AUTO_CONFIRM=true`
  бот сразу вызывает `confirm` → **Paid**.
- **Paid** — подтверждён, звёзды выдаются. **Success** — звёзды доставлены.
- **Refunded** — заказ отклонён, деньги возвращены плательщику. **Fail** — сбой выдачи.

---

## 🔁 Обновление

Из каталога проекта на сервере:

```bash
bash scripts/update.sh
```

Скрипт делает `git pull`, пересобирает и перезапускает контейнеры (миграции
применяются на старте), чистит старые образы и печатает текущую версию (git SHA).

---

## ❓ FAQ

**Оплата прошла, но звёзды не пришли.**
Нажмите «Проверить оплату» в боте — он сверит статус через API. Если статус так и не
меняется, проверьте, что вебхук WATA указывает на `https://<домен><WEBHOOK_PATH>` и
этот адрес доступен снаружи (`docker compose logs -f bot` покажет входящие уведомления).

**Ошибка `STR_1002` при вводе получателя.**
Такого Telegram-аккаунта WATA не нашёл. Проверьте @username (без `@`, 5–32 символа).

**Ошибка `STR_1003`.**
Количество звёзд вне диапазона 50–50 000.

**Как изменить наценку?**
Поменяйте `MARKUP_PERCENT` в `.env` и перезапустите: `docker compose up -d`.
Помните про потолок +50% — большее значение всё равно заклампится.

**Нужен ли депозит в WATA?**
Нет. Используется продукт «Telegram Stars + Эквайринг»: клиент платит по ссылке,
звёзды выдаёт WATA, ваша прибыль — заложенная маржа.

**Как сменить язык бота?**
Команда `/language` или кнопка «🌐 Язык / Language» в меню. Выбор сохраняется per-user;
при первом входе язык определяется по `language_code` Telegram (по умолчанию русский).

---

## ⚠️ Дисклеймер

Проект учебный и распространяется «как есть». Вы самостоятельно отвечаете за
легальность перепродажи Telegram Stars в вашей юрисдикции, за уплату налогов и за
соблюдение условий WATA и Telegram. Авторы проекта не аффилированы с WATA и
Telegram и не несут ответственности за ваше использование бота.

---

## 📄 Лицензия

[MIT](LICENSE) © evansvl

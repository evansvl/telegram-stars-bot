<div align="center">

# ⭐ Telegram Stars Bot

**Продавайте Telegram Stars прямо в боте — с вашей наценкой.**
Пользователь выбирает получателя и количество звёзд, оплачивает картой или
через СБП, а звёзды доставляются автоматически. Платежи и выдача — через WATA.

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

### [✨ Возможности](#-возможности) · [🧱 Стек](#-стек) · [🚀 Быстрый старт](#-быстрый-старт) · [⚙️ Конфигурация](#️-конфигурация) · [🌐 Reverse proxy и HTTPS](#-reverse-proxy-и-https) · [🔔 Вебхук WATA](#-настройка-вебхука-wata) · [📦 Статусы](#-статусы-заказа) · [🔁 Обновление](#-обновление) · [❓ FAQ](#-faq) · [⚠️ Дисклеймер](#️-дисклеймер) · [📄 Лицензия](#-лицензия)

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
| 🐳 | **Docker «из коробки»** | `docker compose up -d --build` поднимает бота, PostgreSQL и Redis |

---

## 🧱 Стек

- **Python 3.12** + **aiogram 3.x** (long-polling)
- **aiohttp** — async-клиент WATA и сервер приёма вебхуков
- **PostgreSQL 16** + **SQLAlchemy 2 (async)** + **Alembic**
- **Redis** — хранилище FSM-состояний
- **Docker** + **docker compose**

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

> ℹ️ Бот работает по long-polling, поэтому **публичный HTTPS нужен только для приёма
> вебхуков об оплате от WATA**. Без него оплата всё равно подтвердится по кнопке
> «Проверить оплату», но удобнее настроить вебхук (см. ниже).

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
| `WEBHOOK_HOST` | для вебхука | Публичный домен за вашим reverse proxy (напр. `bot.example.com`) |
| `WEBHOOK_PORT` | — | HTTP-порт webhook-сервера внутри контейнера (по умолч. `8080`) |
| `WEBHOOK_PATH` | — | Путь вебхука (по умолч. `/wata/webhook`) |
| `WEBHOOK_SECRET` | — | Доп. секрет: сверяется в заголовке `X-Webhook-Secret`, если задан |
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
(порт из `WEBHOOK_PORT`). TLS-терминацию и публикацию наружу вы настраиваете
**сами**, вне `docker-compose`. Ниже — два варианта на выбор. В обоих **замените
`bot.example.com` на свой домен**, а в ЛК WATA укажите вебхук на
`https://<домен><WEBHOOK_PATH>`.

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

Блок `server` для вашего nginx:

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

TLS-сертификат — через certbot (Let's Encrypt):

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d bot.example.com
```

Для выпуска сертификата нужны **открытые порты 80/443** и **A-запись домена** на
ваш сервер. Автопродление certbot настраивает сам (systemd-таймер `certbot.timer`);
проверить можно командой `sudo certbot renew --dry-run`.

---

## 🔔 Настройка вебхука WATA

В личном кабинете WATA (раздел терминала/уведомлений) укажите URL вебхука:

```
https://<ваш-домен><WEBHOOK_PATH>      напр. https://bot.example.com/wata/webhook
```

Бот проверяет подпись `X-Signature` (RSA SHA-512) по публичному ключу WATA, логирует
весь payload и затем **сверяет статус заказа через `GET /stars/order/{id}`** — то есть
истинным источником считается ответ API, а не тело уведомления. GET к WATA лимитирован
(1 запрос / 30 c на объект), поэтому поллинг используется только как fallback.

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

---

## ⚠️ Дисклеймер

Проект учебный и распространяется «как есть». Вы самостоятельно отвечаете за
легальность перепродажи Telegram Stars в вашей юрисдикции, за уплату налогов и за
соблюдение условий WATA и Telegram. Авторы проекта не аффилированы с WATA и
Telegram и не несут ответственности за ваше использование бота.

---

## 📄 Лицензия

[MIT](LICENSE) © evansvl

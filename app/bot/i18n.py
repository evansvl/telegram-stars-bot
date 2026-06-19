"""Minimal dictionary-based i18n for the bot (Russian + English).

Russian is the primary language; English is the secondary ("side") language.
Strings are looked up by key and language; missing translations fall back to
Russian, then to the raw key.
"""

from __future__ import annotations

from typing import Any

DEFAULT_LANG = "ru"
SUPPORTED_LANGS = ("ru", "en")

LANGUAGE_NAMES = {"ru": "🇷🇺 Русский", "en": "🇬🇧 English"}


def normalize_lang(code: str | None) -> str:
    """Map a Telegram ``language_code`` (or stored value) to a supported lang."""
    if not code:
        return DEFAULT_LANG
    c = code.lower()
    if c.startswith("ru"):
        return "ru"
    if c.startswith("en"):
        return "en"
    return DEFAULT_LANG


# key -> {lang -> template}
TEXTS: dict[str, dict[str, str]] = {
    # ── Common / menu ─────────────────────────────────────────
    "welcome": {
        "ru": (
            "👋 <b>Привет!</b>\n\n"
            "Я помогу купить <b>Telegram Stars</b> ⭐ для любого пользователя Telegram.\n"
            "Оплата картой или через СБП — быстро и безопасно через WATA.\n\n"
            "Нажми кнопку ниже, чтобы начать.\n\n"
            "Используя бота, вы принимаете пользовательское соглашение (/terms) "
            "и политику конфиденциальности (/privacy)."
        ),
        "en": (
            "👋 <b>Hi!</b>\n\n"
            "I'll help you buy <b>Telegram Stars</b> ⭐ for any Telegram user.\n"
            "Pay by card or SBP — fast and secure via WATA.\n\n"
            "Tap the button below to start.\n\n"
            "By using the bot you accept the Terms of Service (/terms) "
            "and the Privacy Policy (/privacy)."
        ),
    },
    "help": {
        "ru": (
            "<b>❓ Как это работает</b>\n\n"
            "1. Нажми «Купить звёзды» и укажи <b>@username</b> получателя.\n"
            "2. Выбери количество звёзд (от 50 до 50 000).\n"
            "3. Получи ссылку на оплату и оплати картой/СБП.\n"
            "4. После оплаты звёзды доставятся автоматически ⭐\n\n"
            "Всё управление — кнопками ниже. Кнопка «Меню» рядом с полем ввода "
            "всегда открывает главное меню."
        ),
        "en": (
            "<b>❓ How it works</b>\n\n"
            "1. Tap “Buy Stars” and enter the recipient's <b>@username</b>.\n"
            "2. Pick the amount of stars (from 50 to 50,000).\n"
            "3. Get a payment link and pay by card/SBP.\n"
            "4. After payment the stars are delivered automatically ⭐\n\n"
            "Everything is controlled with the buttons below. The “Menu” button "
            "next to the input field always opens the main menu."
        ),
    },
    "choose_language": {
        "ru": "Выберите язык / Choose your language:",
        "en": "Choose your language / Выберите язык:",
    },
    "language_set": {
        "ru": "✅ Язык переключён на русский.",
        "en": "✅ Language switched to English.",
    },
    "cancelled": {"ru": "Отменено.", "en": "Cancelled."},
    "cancelled_menu": {
        "ru": "Отменено. Возвращаю в меню.",
        "en": "Cancelled. Back to the menu.",
    },
    "send_text_or_cancel": {
        "ru": "Пожалуйста, отправьте текстовое сообщение или нажмите /cancel.",
        "en": "Please send a text message or tap /cancel.",
    },
    # ── Buy flow ──────────────────────────────────────────────
    "ask_username": {
        "ru": "Кому отправляем звёзды?\nУкажите <b>@username</b> получателя в Telegram:",
        "en": "Who are the stars for?\nEnter the recipient's Telegram <b>@username</b>:",
    },
    "username_invalid": {
        "ru": (
            "Это не похоже на корректный @username (5–32 символа, буквы/цифры/_). "
            "Попробуйте ещё раз:"
        ),
        "en": (
            "That doesn't look like a valid @username (5–32 chars, letters/digits/_). "
            "Please try again:"
        ),
    },
    "enter_other_username": {
        "ru": "Введите другой @username:",
        "en": "Enter another @username:",
    },
    "user_found": {
        "ru": "✅ Пользователь <b>@{username}</b> найден.\n\nСколько звёзд отправить?",
        "en": "✅ User <b>@{username}</b> found.\n\nHow many stars to send?",
    },
    "ask_custom_count": {
        "ru": "Введите количество звёзд числом (от {min} до {max}):",
        "en": "Enter the number of stars (from {min} to {max}):",
    },
    "not_integer": {
        "ru": "Нужно целое число. Попробуйте ещё раз:",
        "en": "A whole number is required. Please try again:",
    },
    "count_out_of_range": {
        "ru": "Количество должно быть от {min} до {max}. Попробуйте ещё раз:",
        "en": "The amount must be between {min} and {max}. Please try again:",
    },
    "session_expired": {
        "ru": "Сессия истекла. Начните заново /start.",
        "en": "Session expired. Start over with /start.",
    },
    "session_expired_alert": {
        "ru": "Сессия истекла, начните заново.",
        "en": "Session expired, please start over.",
    },
    "quote": {
        "ru": (
            "🧾 <b>Проверьте заказ</b>\n\n"
            "Получатель: <b>@{target}</b>\n"
            "Количество: <b>{count}</b> ⭐\n"
            "К оплате: <b>{amount} ₽</b>\n\n"
            "Нажмите «Оплатить», чтобы получить ссылку."
        ),
        "en": (
            "🧾 <b>Review your order</b>\n\n"
            "Recipient: <b>@{target}</b>\n"
            "Amount: <b>{count}</b> ⭐\n"
            "To pay: <b>{amount} ₽</b>\n\n"
            "Tap “Pay” to get the payment link."
        ),
    },
    "creating_order": {"ru": "Создаю заказ…", "en": "Creating the order…"},
    "order_created": {
        "ru": (
            "💳 Заказ создан!\n\n"
            "<b>{count}</b> ⭐ для <b>@{target}</b>\n"
            "К оплате: <b>{amount} ₽</b>\n\n"
            "Оплатите по ссылке ниже. После оплаты звёзды придут автоматически."
        ),
        "en": (
            "💳 Order created!\n\n"
            "<b>{count}</b> ⭐ for <b>@{target}</b>\n"
            "To pay: <b>{amount} ₽</b>\n\n"
            "Pay via the link below. The stars arrive automatically after payment."
        ),
    },
    "test_order_created": {
        "ru": (
            "🧪 <b>ТЕСТОВЫЙ заказ</b>\n\n"
            "<b>{count}</b> ⭐ для <b>@{target}</b>\n"
            "Сумма: <b>{amount} ₽</b>\n\n"
            "Платёжная ссылка не создаётся. Нажмите кнопку ниже, чтобы сымитировать "
            "успешную оплату и проверить начисления."
        ),
        "en": (
            "🧪 <b>TEST order</b>\n\n"
            "<b>{count}</b> ⭐ for <b>@{target}</b>\n"
            "Amount: <b>{amount} ₽</b>\n\n"
            "No payment link is created. Tap the button below to simulate a "
            "successful payment and check the payouts."
        ),
    },
    "test_paid_done": {
        "ru": "🧪 Тестовая оплата проведена! Звёзды «отправлены», начисления обработаны.",
        "en": "🧪 Test payment completed! Stars “delivered”, payouts processed.",
    },
    "checking_status": {"ru": "Проверяю статус…", "en": "Checking status…"},
    "order_not_found": {"ru": "Заказ не найден.", "en": "Order not found."},
    "order_status": {
        "ru": "Статус заказа: <b>{label}</b>",
        "en": "Order status: <b>{label}</b>",
    },
    # ── History & admin ───────────────────────────────────────
    "orders_header": {
        "ru": "<b>🧾 Ваши последние заказы:</b>\n",
        "en": "<b>🧾 Your recent orders:</b>\n",
    },
    "orders_empty": {
        "ru": "У вас пока нет заказов.",
        "en": "You don't have any orders yet.",
    },
    "order_line": {
        "ru": "• {count} ⭐ → @{target} — {amount} ₽ — {status}",
        "en": "• {count} ⭐ → @{target} — {amount} ₽ — {status}",
    },
    "stats": {
        "ru": (
            "<b>📊 Статистика</b>\n\n"
            "Всего заказов: <b>{total}</b>\n"
            "Оплачено: <b>{paid}</b>\n"
            "Оборот: <b>{turnover} ₽</b>\n"
            "Суммарная маржа: <b>{margin} ₽</b>"
        ),
        "en": (
            "<b>📊 Statistics</b>\n\n"
            "Total orders: <b>{total}</b>\n"
            "Paid: <b>{paid}</b>\n"
            "Turnover: <b>{turnover} ₽</b>\n"
            "Total margin: <b>{margin} ₽</b>"
        ),
    },
    "admin_only": {
        "ru": "Команда доступна только администраторам.",
        "en": "This command is for administrators only.",
    },
    # ── Terms of service ──────────────────────────────────────
    "terms": {
        "ru": "📄 Пользовательское соглашение (публичная оферта):\n{url}",
        "en": "📄 Terms of Service (public offer):\n{url}",
    },
    "terms_unavailable": {
        "ru": "Пользовательское соглашение пока не опубликовано.",
        "en": "The Terms of Service are not published yet.",
    },
    "privacy": {
        "ru": "🔒 Политика конфиденциальности и обработки персональных данных:\n{url}",
        "en": "🔒 Privacy Policy and personal data processing:\n{url}",
    },
    "privacy_unavailable": {
        "ru": "Политика конфиденциальности пока не опубликована.",
        "en": "The Privacy Policy is not published yet.",
    },
    # ── Referral program ──────────────────────────────────────
    "referral_overview": {
        "ru": (
            "🤝 <b>Партнёрская программа</b>\n\n"
            "Приглашайте друзей и получайте <b>{percent}%</b> с каждой их оплаты.\n\n"
            "Ваша ссылка:\n{link}\n\n"
            "👥 Приглашено: <b>{referrals}</b>\n"
            "💰 Всего начислено: <b>{earned} ₽</b>\n"
            "✅ Доступно к выводу: <b>{available} ₽</b>"
        ),
        "en": (
            "🤝 <b>Referral program</b>\n\n"
            "Invite friends and earn <b>{percent}%</b> of each of their payments.\n\n"
            "Your link:\n{link}\n\n"
            "👥 Invited: <b>{referrals}</b>\n"
            "💰 Total earned: <b>{earned} ₽</b>\n"
            "✅ Available to withdraw: <b>{available} ₽</b>"
        ),
    },
    "referral_earned_notify": {
        "ru": "🤝 Вам начислено <b>{amount} ₽</b> по партнёрской программе!",
        "en": "🤝 You earned <b>{amount} ₽</b> from the referral program!",
    },
    # ── Withdrawals (user) ────────────────────────────────────
    "withdraw_choose_method": {
        "ru": "Выберите способ вывода средств:",
        "en": "Choose a withdrawal method:",
    },
    "withdraw_ask_destination_sbp": {
        "ru": "Отправьте реквизиты для СБП (номер телефона и банк получателя):",
        "en": "Send your СБП details (phone number and recipient bank):",
    },
    "withdraw_ask_destination_crypto": {
        "ru": "Отправьте адрес кошелька USDT (сеть TRC20 или TON):",
        "en": "Send your USDT wallet address (TRC20 or TON network):",
    },
    "withdraw_ask_amount": {
        "ru": "Введите сумму вывода в ₽.\nДоступно: <b>{available} ₽</b>, минимум: <b>{min} ₽</b>.",
        "en": "Enter the amount in ₽.\nAvailable: <b>{available} ₽</b>, minimum: <b>{min} ₽</b>.",
    },
    "withdraw_not_number": {
        "ru": "Нужно число. Введите сумму вывода в ₽:",
        "en": "A number is required. Enter the amount in ₽:",
    },
    "wd_has_pending": {
        "ru": "У вас уже есть заявка на вывод в обработке. Дождитесь её решения.",
        "en": "You already have a withdrawal in progress. Please wait for it to be resolved.",
    },
    "wd_below_min": {
        "ru": "Минимальная сумма вывода — {min} ₽.",
        "en": "The minimum withdrawal amount is {min} ₽.",
    },
    "wd_over_balance": {
        "ru": "Недостаточно средств. Доступно: {available} ₽.",
        "en": "Insufficient balance. Available: {available} ₽.",
    },
    "withdraw_created": {
        "ru": (
            "✅ Заявка на вывод <b>{amount} ₽</b> создана (#{id}).\n"
            "Обработка — до 5 рабочих дней."
        ),
        "en": (
            "✅ Withdrawal request for <b>{amount} ₽</b> created (#{id}).\n"
            "Processed within 5 business days."
        ),
    },
    "withdraw_cancelled": {"ru": "Вывод отменён.", "en": "Withdrawal cancelled."},
    "withdrawals_empty": {
        "ru": "У вас пока нет заявок на вывод.",
        "en": "You don't have any withdrawal requests yet.",
    },
    "withdrawals_header": {"ru": "<b>📜 Ваши выводы:</b>\n", "en": "<b>📜 Your withdrawals:</b>\n"},
    "withdrawal_line": {
        "ru": "• #{id} — {amount} ₽ — {method} — {status}",
        "en": "• #{id} — {amount} ₽ — {method} — {status}",
    },
    "withdraw_approved_notify": {
        "ru": "✅ Ваш вывод #{id} на <b>{amount} ₽</b> выполнен.",
        "en": "✅ Your withdrawal #{id} for <b>{amount} ₽</b> has been paid.",
    },
    "withdraw_rejected_notify": {
        "ru": (
            "❌ Ваш вывод #{id} отклонён.\n"
            "Причина: {reason}\n"
            "Средства возвращены на баланс."
        ),
        "en": (
            "❌ Your withdrawal #{id} was rejected.\n"
            "Reason: {reason}\n"
            "The funds were returned to your balance."
        ),
    },
    "wd_status_Pending": {"ru": "⏳ в обработке", "en": "⏳ pending"},
    "wd_status_Approved": {"ru": "✅ выплачено", "en": "✅ paid"},
    "wd_status_Rejected": {"ru": "❌ отклонено", "en": "❌ rejected"},
    "method_sbp": {"ru": "СБП", "en": "СБП"},
    "method_crypto": {"ru": "USDT (TRC20/TON)", "en": "USDT (TRC20/TON)"},
    # ── Withdrawals (admin moderation) ────────────────────────
    "admin_new_withdrawal": {
        "ru": (
            "🆕 <b>Заявка на вывод #{id}</b>\n"
            "Пользователь: {user}\n"
            "Сумма: <b>{amount} ₽</b>\n"
            "Способ: {method}\n"
            "Реквизиты: <code>{destination}</code>"
        ),
        "en": (
            "🆕 <b>Withdrawal request #{id}</b>\n"
            "User: {user}\n"
            "Amount: <b>{amount} ₽</b>\n"
            "Method: {method}\n"
            "Details: <code>{destination}</code>"
        ),
    },
    "admin_ask_proof": {
        "ru": (
            "Отправьте пруф оплаты для заявки #{id}:\n"
            "ссылку на транзакцию (TRON/TON) или PDF-файл (для оплаты через ИП)."
        ),
        "en": (
            "Send proof of payment for request #{id}:\n"
            "a transaction link (TRON/TON) or a PDF file (for ИП payments)."
        ),
    },
    "admin_proof_invalid": {
        "ru": "Пришлите ссылку (http/https) или PDF-документ.",
        "en": "Send a link (http/https) or a PDF document.",
    },
    "admin_ask_reject_reason": {
        "ru": "Укажите причину отклонения заявки #{id}:",
        "en": "Enter the rejection reason for request #{id}:",
    },
    "admin_withdrawal_done": {
        "ru": "Готово. Заявка #{id} обработана.",
        "en": "Done. Request #{id} has been processed.",
    },
    "admin_withdrawal_gone": {
        "ru": "Заявка не найдена или уже обработана.",
        "en": "Request not found or already processed.",
    },
    "admin_proof_caption": {
        "ru": "Пруф по выводу #{id}",
        "en": "Proof for withdrawal #{id}",
    },
    # ── Status labels ─────────────────────────────────────────
    "status_New": {"ru": "🆕 создаётся", "en": "🆕 creating"},
    "status_Pending": {"ru": "⏳ ожидает оплаты", "en": "⏳ awaiting payment"},
    "status_Review": {"ru": "🔎 проверяется", "en": "🔎 under review"},
    "status_Paid": {"ru": "✅ оплачен, исполняется", "en": "✅ paid, processing"},
    "status_Success": {"ru": "⭐ звёзды отправлены", "en": "⭐ stars delivered"},
    "status_Refunded": {"ru": "↩️ возврат средств", "en": "↩️ refunded"},
    "status_Fail": {"ru": "❌ ошибка исполнения", "en": "❌ fulfilment failed"},
    "status_Error": {"ru": "⚠️ не удалось создать", "en": "⚠️ creation failed"},
    # ── Webhook buyer notifications ───────────────────────────
    "notify_Paid": {
        "ru": "✅ Оплата получена! Звёзды отправляются ⭐",
        "en": "✅ Payment received! The stars are on their way ⭐",
    },
    "notify_Success": {
        "ru": "⭐ Готово! Звёзды отправлены получателю.",
        "en": "⭐ Done! The stars have been delivered.",
    },
    "notify_Refunded": {
        "ru": "↩️ Заказ не подтверждён, средства возвращены.",
        "en": "↩️ Order not confirmed, your money has been refunded.",
    },
    "notify_Fail": {
        "ru": "❌ Не удалось исполнить заказ. Попробуйте снова.",
        "en": "❌ The order could not be fulfilled. Please try again.",
    },
    # ── WATA error messages ───────────────────────────────────
    "err_invalid_data": {
        "ru": "Переданы неверные данные заказа. Попробуйте ещё раз.",
        "en": "Invalid order data. Please try again.",
    },
    "err_user_not_found": {
        "ru": "Пользователь Telegram с таким @username не найден.",
        "en": "No Telegram user with that @username was found.",
    },
    "err_count_range": {
        "ru": "Количество звёзд должно быть от 50 до 50 000.",
        "en": "The number of stars must be between 50 and 50,000.",
    },
    "err_create_failed": {
        "ru": "Не удалось создать заказ на стороне WATA. Попробуйте позже.",
        "en": "WATA could not create the order. Please try again later.",
    },
    "err_order_not_found": {"ru": "Заказ не найден.", "en": "Order not found."},
    "err_payment_expired": {
        "ru": "Истёк срок оплаты заказа. Создайте новый заказ.",
        "en": "The payment window has expired. Please create a new order.",
    },
    "err_link_unavailable": {
        "ru": "Ссылка на оплату недоступна или уже оплачена.",
        "en": "The payment link is unavailable or already paid.",
    },
    "err_auth": {
        "ru": "Ошибка авторизации платёжного сервиса (проверьте WATA_TOKEN).",
        "en": "Payment service authorization error (check WATA_TOKEN).",
    },
    "err_rate_limit": {
        "ru": "Слишком много запросов к платёжному сервису. Подождите немного.",
        "en": "Too many requests to the payment service. Please wait a moment.",
    },
    "err_network": {
        "ru": "Платёжный сервис временно недоступен. Попробуйте через минуту.",
        "en": "The payment service is temporarily unavailable. Try again in a minute.",
    },
    "err_generic": {
        "ru": "Произошла ошибка при обращении к платёжному сервису. Попробуйте позже.",
        "en": "An error occurred while contacting the payment service. Try again later.",
    },
    # ── Buttons ───────────────────────────────────────────────
    "btn_buy": {"ru": "⭐ Купить звёзды", "en": "⭐ Buy Stars"},
    "btn_orders": {"ru": "🧾 Мои заказы", "en": "🧾 My orders"},
    "btn_help": {"ru": "❓ Помощь", "en": "❓ Help"},
    "btn_language": {"ru": "🌐 Язык / Language", "en": "🌐 Language / Язык"},
    "btn_referral": {"ru": "🤝 Партнёрка", "en": "🤝 Referrals"},
    "btn_withdraw": {"ru": "💸 Вывести средства", "en": "💸 Withdraw"},
    "btn_withdrawals": {"ru": "📜 Мои выводы", "en": "📜 My withdrawals"},
    "btn_method_sbp": {"ru": "🏦 СБП", "en": "🏦 СБП"},
    "btn_method_crypto": {"ru": "🪙 USDT (TRC20/TON)", "en": "🪙 USDT (TRC20/TON)"},
    "btn_approve": {"ru": "✅ Подтвердить", "en": "✅ Approve"},
    "btn_reject": {"ru": "❌ Отклонить", "en": "❌ Reject"},
    "btn_terms": {"ru": "📄 Оферта", "en": "📄 Terms"},
    "btn_privacy": {"ru": "🔒 Конфиденциальность", "en": "🔒 Privacy"},
    "btn_custom": {"ru": "✏️ Ввести своё", "en": "✏️ Custom amount"},
    "btn_cancel": {"ru": "✖️ Отмена", "en": "✖️ Cancel"},
    "btn_pay": {"ru": "💳 Оплатить", "en": "💳 Pay"},
    "btn_goto_pay": {"ru": "💳 Перейти к оплате", "en": "💳 Go to payment"},
    "btn_check": {"ru": "🔄 Проверить оплату", "en": "🔄 Check payment"},
    "btn_test_pay": {"ru": "✅ Я оплатил (ТЕСТ)", "en": "✅ I paid (TEST)"},
    "btn_retry": {"ru": "🔁 Попробовать снова", "en": "🔁 Try again"},
    "btn_menu": {"ru": "🏠 В меню", "en": "🏠 Menu"},
}


def t(key: str, lang: str = DEFAULT_LANG, /, **kwargs: Any) -> str:
    """Translate ``key`` into ``lang`` with optional ``str.format`` arguments."""
    entry = TEXTS.get(key)
    if entry is None:
        return key
    template = entry.get(lang) or entry.get(DEFAULT_LANG) or key
    return template.format(**kwargs) if kwargs else template


def status_label(status: str, lang: str = DEFAULT_LANG) -> str:
    """Localized human label for an order status value."""
    return t(f"status_{status}", lang) if f"status_{status}" in TEXTS else status


def withdrawal_status_label(status: str, lang: str = DEFAULT_LANG) -> str:
    """Localized human label for a withdrawal status value."""
    return t(f"wd_status_{status}", lang) if f"wd_status_{status}" in TEXTS else status

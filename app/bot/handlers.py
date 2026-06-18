"""aiogram handlers implementing the buy flow, history, help and admin stats."""

from __future__ import annotations

import logging
import re
from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards
from app.bot.states import BuyStates
from app.config import Settings
from app.db.models import OrderStatusEnum
from app.pricing import MAX_STARS, MIN_STARS, compute_amount, is_valid_count
from app.services import OrderService
from app.wata.errors import WataError

logger = logging.getLogger(__name__)
router = Router(name="main")

_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")

_STATUS_LABELS = {
    OrderStatusEnum.NEW.value: "🆕 создаётся",
    OrderStatusEnum.PENDING.value: "⏳ ожидает оплаты",
    OrderStatusEnum.REVIEW.value: "🔎 проверяется",
    OrderStatusEnum.PAID.value: "✅ оплачен, исполняется",
    OrderStatusEnum.SUCCESS.value: "⭐ звёзды отправлены",
    OrderStatusEnum.REFUNDED.value: "↩️ возврат средств",
    OrderStatusEnum.FAIL.value: "❌ ошибка исполнения",
    OrderStatusEnum.ERROR.value: "⚠️ не удалось создать",
}

WELCOME = (
    "👋 <b>Привет!</b>\n\n"
    "Я помогу купить <b>Telegram Stars</b> ⭐ для любого пользователя Telegram.\n"
    "Оплата картой или через СБП — быстро и безопасно через WATA.\n\n"
    "Нажми кнопку ниже, чтобы начать."
)

HELP_TEXT = (
    "<b>❓ Как это работает</b>\n\n"
    "1. Нажми «Купить звёзды» и укажи <b>@username</b> получателя.\n"
    "2. Выбери количество звёзд (от 50 до 50 000).\n"
    "3. Получи ссылку на оплату и оплати картой/СБП.\n"
    "4. После оплаты звёзды доставятся автоматически ⭐\n\n"
    "<b>Команды:</b>\n"
    "/start — главное меню\n"
    "/orders — мои заказы\n"
    "/help — эта справка\n"
    "/cancel — отменить текущее действие"
)


def _status_label(status: str) -> str:
    return _STATUS_LABELS.get(status, status)


def _format_quote(target: str, count: int, amount: Decimal) -> str:
    return (
        f"🧾 <b>Проверьте заказ</b>\n\n"
        f"Получатель: <b>@{target}</b>\n"
        f"Количество: <b>{count}</b> ⭐\n"
        f"К оплате: <b>{amount} ₽</b>\n\n"
        f"Нажмите «Оплатить», чтобы получить ссылку."
    )


# ── Entry points ─────────────────────────────────────────────


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(WELCOME, reply_markup=keyboards.main_menu())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=keyboards.main_menu())


@router.callback_query(F.data == "help:show")
async def cb_help(call: CallbackQuery) -> None:
    if isinstance(call.message, Message):
        await call.message.answer(HELP_TEXT, reply_markup=keyboards.main_menu())
    await call.answer()


@router.callback_query(F.data == "menu:show")
async def cb_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if isinstance(call.message, Message):
        await call.message.answer(WELCOME, reply_markup=keyboards.main_menu())
    await call.answer()


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено. Возвращаю в меню.", reply_markup=keyboards.main_menu())


@router.callback_query(F.data == "buy:cancel")
async def cb_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if isinstance(call.message, Message):
        await call.message.answer("Отменено.", reply_markup=keyboards.main_menu())
    await call.answer()


# ── Buy flow ─────────────────────────────────────────────────


@router.callback_query(F.data == "buy:start")
async def cb_buy_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(BuyStates.waiting_username)
    if isinstance(call.message, Message):
        await call.message.answer(
            "Кому отправляем звёзды?\nУкажите <b>@username</b> получателя в Telegram:"
        )
    await call.answer()


@router.message(BuyStates.waiting_username, F.text)
async def on_username(message: Message, state: FSMContext, service: OrderService) -> None:
    username = (message.text or "").strip().lstrip("@")
    if not _USERNAME_RE.match(username):
        await message.answer(
            "Это не похоже на корректный @username (5–32 символа, буквы/цифры/_). "
            "Попробуйте ещё раз:"
        )
        return

    try:
        price = await service.get_star_price(username)
    except WataError as exc:
        await message.answer(f"⚠️ {exc.user_message()}\n\nВведите другой @username:")
        return

    await state.update_data(target_username=username, min_price=str(price.min_price))
    await state.set_state(BuyStates.waiting_count)
    await message.answer(
        f"✅ Пользователь <b>@{username}</b> найден.\n\nСколько звёзд отправить?",
        reply_markup=keyboards.count_presets(),
    )


async def _show_quote(
    message: Message, state: FSMContext, service: OrderService, count: int
) -> None:
    data = await state.get_data()
    target = data.get("target_username")
    min_price_raw = data.get("min_price")
    if not target or min_price_raw is None:
        await message.answer("Сессия истекла. Начните заново /start.")
        await state.clear()
        return

    quote = compute_amount(Decimal(str(min_price_raw)), count, service.markup_percent)
    await state.update_data(count=count, amount=str(quote.amount))
    await state.set_state(BuyStates.confirm_payment)
    await message.answer(
        _format_quote(target, count, quote.amount), reply_markup=keyboards.confirm_payment()
    )


@router.callback_query(BuyStates.waiting_count, F.data.startswith("count:"))
async def cb_count(call: CallbackQuery, state: FSMContext, service: OrderService) -> None:
    value = (call.data or "").split(":", 1)[1]
    if value == "custom":
        await state.set_state(BuyStates.waiting_custom_count)
        if isinstance(call.message, Message):
            await call.message.answer(
                f"Введите количество звёзд числом (от {MIN_STARS} до {MAX_STARS}):"
            )
        await call.answer()
        return

    count = int(value)
    if isinstance(call.message, Message):
        await _show_quote(call.message, state, service, count)
    await call.answer()


@router.message(BuyStates.waiting_custom_count, F.text)
async def on_custom_count(message: Message, state: FSMContext, service: OrderService) -> None:
    raw = (message.text or "").strip().replace(" ", "")
    if not raw.isdigit():
        await message.answer("Нужно целое число. Попробуйте ещё раз:")
        return
    count = int(raw)
    if not is_valid_count(count):
        await message.answer(
            f"Количество должно быть от {MIN_STARS} до {MAX_STARS}. Попробуйте ещё раз:"
        )
        return
    await _show_quote(message, state, service, count)


@router.callback_query(BuyStates.confirm_payment, F.data == "pay:confirm")
async def cb_pay(call: CallbackQuery, state: FSMContext, service: OrderService) -> None:
    data = await state.get_data()
    target = data.get("target_username")
    count = data.get("count")
    amount = data.get("amount")
    if not (target and count and amount) or not isinstance(call.message, Message):
        await call.answer("Сессия истекла, начните заново.", show_alert=True)
        await state.clear()
        return

    await call.answer("Создаю заказ…")
    user = call.from_user
    try:
        created = await service.create_order(
            buyer_tg_id=user.id,
            buyer_username=user.username,
            target_username=str(target),
            count=int(count),
            amount=Decimal(str(amount)),
        )
    except WataError as exc:
        await call.message.answer(
            f"⚠️ {exc.user_message()}", reply_markup=keyboards.retry_buy()
        )
        await state.clear()
        return

    await state.clear()
    await call.message.answer(
        (
            f"💳 Заказ создан!\n\n"
            f"<b>{created.count}</b> ⭐ для <b>@{created.target_username}</b>\n"
            f"К оплате: <b>{created.amount} ₽</b>\n\n"
            f"Оплатите по ссылке ниже. После оплаты звёзды придут автоматически."
        ),
        reply_markup=keyboards.payment_link(created.payment_link, created.order_id),
    )


@router.callback_query(F.data.startswith("check:"))
async def cb_check(call: CallbackQuery, service: OrderService) -> None:
    order_id = (call.data or "").split(":", 1)[1]
    await call.answer("Проверяю статус…")
    order = await service.sync_order(order_id)
    if order is None or not isinstance(call.message, Message):
        if isinstance(call.message, Message):
            await call.message.answer("Заказ не найден.")
        return
    await call.message.answer(f"Статус заказа: <b>{_status_label(order.status)}</b>")


# ── History & admin ──────────────────────────────────────────


@router.message(Command("orders"))
async def cmd_orders(message: Message, service: OrderService) -> None:
    await _send_orders(message, service, message.from_user.id if message.from_user else 0)


@router.callback_query(F.data == "orders:list")
async def cb_orders(call: CallbackQuery, service: OrderService) -> None:
    if isinstance(call.message, Message) and call.from_user:
        await _send_orders(call.message, service, call.from_user.id)
    await call.answer()


async def _send_orders(message: Message, service: OrderService, tg_id: int) -> None:
    orders = await service.list_orders(tg_id)
    if not orders:
        await message.answer("У вас пока нет заказов.", reply_markup=keyboards.main_menu())
        return
    lines = ["<b>🧾 Ваши последние заказы:</b>\n"]
    for o in orders:
        lines.append(
            f"• {o.count} ⭐ → @{o.target_username} — {o.amount} ₽ — {_status_label(o.status)}"
        )
    await message.answer("\n".join(lines), reply_markup=keyboards.main_menu())


@router.message(Command("stats"))
async def cmd_stats(message: Message, service: OrderService, settings: Settings) -> None:
    if not message.from_user or not settings.is_admin(message.from_user.id):
        await message.answer("Команда доступна только администраторам.")
        return
    stats = await service.stats()
    await message.answer(
        
            "<b>📊 Статистика</b>\n\n"
            f"Всего заказов: <b>{stats['total_orders']}</b>\n"
            f"Оплачено: <b>{stats['paid_orders']}</b>\n"
            f"Оборот: <b>{stats['turnover']} ₽</b>\n"
            f"Суммарная маржа: <b>{stats['margin']} ₽</b>"
        
    )


@router.message(BuyStates.waiting_username)
@router.message(BuyStates.waiting_custom_count)
async def on_unexpected(message: Message) -> None:
    await message.answer("Пожалуйста, отправьте текстовое сообщение или нажмите /cancel.")

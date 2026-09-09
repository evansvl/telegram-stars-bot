"""Application services tying together WATA, pricing and the database."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.bot.i18n import DEFAULT_LANG, normalize_lang
from app.config import Settings
from app.db.models import (
    Order,
    OrderStatusEnum,
    PartnerBot,
    User,
    Withdrawal,
    WithdrawalMethod,
    WithdrawalStatus,
)
from app.db.repositories import (
    OrderRepository,
    PartnerBotRepository,
    ReferralRepository,
    UserRepository,
    WithdrawalRepository,
)
from app.db.session import Database
from app.ggp.client import GgpClient
from app.ggp.errors import GgpApiError, GgpNetworkError
from app.pricing import (
    PriceQuote,
    compute_amount,
    compute_ton_due,
    estimated_margin,
    jitter_ton_amount,
)
from app.ton.fragment_rate import FragmentTonRate
from app.ton.rates import TonRates
from app.ton.scanner import TonScanner
from app.wata.client import WataClient
from app.wata.models import OrderStatus, StarPrice

logger = logging.getLogger(__name__)

# WATA -> local status mapping (identity for the documented states).
_WATA_TO_LOCAL = {
    "Pending": OrderStatusEnum.PENDING,
    "Review": OrderStatusEnum.REVIEW,
    "Paid": OrderStatusEnum.PAID,
    "Success": OrderStatusEnum.SUCCESS,
    "Refunded": OrderStatusEnum.REFUNDED,
    "Fail": OrderStatusEnum.FAIL,
}


def map_status(wata_status: str) -> str:
    enum_val = _WATA_TO_LOCAL.get(wata_status)
    if enum_val is None:
        raise OrderVerificationError(f"unsupported WATA status: {wata_status!r}")
    return enum_val.value


class OrderVerificationError(RuntimeError):
    """Raised when provider data does not match the order stored by the bot."""


_WATA_TRANSITIONS = {
    OrderStatusEnum.NEW.value: {
        OrderStatusEnum.PENDING.value,
        OrderStatusEnum.REVIEW.value,
        OrderStatusEnum.PAID.value,
        OrderStatusEnum.SUCCESS.value,
        OrderStatusEnum.REFUNDED.value,
        OrderStatusEnum.FAIL.value,
    },
    OrderStatusEnum.PENDING.value: {
        OrderStatusEnum.PENDING.value,
        OrderStatusEnum.REVIEW.value,
        OrderStatusEnum.PAID.value,
        OrderStatusEnum.SUCCESS.value,
        OrderStatusEnum.REFUNDED.value,
        OrderStatusEnum.FAIL.value,
    },
    OrderStatusEnum.REVIEW.value: {
        OrderStatusEnum.REVIEW.value,
        OrderStatusEnum.PAID.value,
        OrderStatusEnum.SUCCESS.value,
        OrderStatusEnum.REFUNDED.value,
        OrderStatusEnum.FAIL.value,
    },
    OrderStatusEnum.PAID.value: {
        OrderStatusEnum.PAID.value,
        OrderStatusEnum.SUCCESS.value,
        OrderStatusEnum.REFUNDED.value,
        OrderStatusEnum.FAIL.value,
    },
    OrderStatusEnum.SUCCESS.value: {OrderStatusEnum.SUCCESS.value},
    OrderStatusEnum.REFUNDED.value: {OrderStatusEnum.REFUNDED.value},
    OrderStatusEnum.FAIL.value: {OrderStatusEnum.FAIL.value},
    OrderStatusEnum.ERROR.value: {
        OrderStatusEnum.PENDING.value,
        OrderStatusEnum.REVIEW.value,
        OrderStatusEnum.PAID.value,
        OrderStatusEnum.SUCCESS.value,
        OrderStatusEnum.REFUNDED.value,
        OrderStatusEnum.FAIL.value,
    },
}


def _wata_transition_allowed(current: str, new: str) -> bool:
    return new in _WATA_TRANSITIONS.get(current, set())


def _normalise_username(value: str) -> str:
    return value.strip().lstrip("@").casefold()


def _verify_wata_order(local: Order, remote: OrderStatus) -> None:
    """Bind an authenticated WATA response to the immutable local order facts."""
    if remote.order_id != local.order_id:
        raise OrderVerificationError(
            f"WATA order id mismatch: expected {local.order_id!r}, got {remote.order_id!r}"
        )
    if _normalise_username(remote.username) != _normalise_username(local.target_username):
        raise OrderVerificationError(f"WATA username mismatch for order {local.order_id}")
    if remote.count != local.count:
        raise OrderVerificationError(f"WATA count mismatch for order {local.order_id}")
    if remote.amount != local.amount:
        raise OrderVerificationError(f"WATA amount mismatch for order {local.order_id}")


@dataclass(slots=True)
class Quote:
    star_price: StarPrice
    quote: PriceQuote


@dataclass(slots=True)
class BuyerProfile:
    registered: datetime | None
    orders: int
    stars: int
    spent: Decimal


@dataclass(slots=True)
class CreatedOrder:
    order_id: str
    payment_link: str
    amount: Decimal
    count: int
    target_username: str
    test: bool = False


@dataclass(slots=True)
class CryptoQuote:
    """A crypto (TON) quote for ``count`` stars at a given total markup."""

    ton_cost: Decimal  # GGP USD price converted to TON
    rub_cost: Decimal  # normalized GGP RUB purchase cost (basis for ₽ earnings)
    ton_due: Decimal  # TON the buyer pays (markup applied, pre-jitter)
    rub_equiv: Decimal  # ₽-equivalent paid (for stats/referral)
    display_rub: Decimal | None = None  # market estimate of ton_due, display only


@dataclass(slots=True)
class CreatedCryptoOrder:
    order_id: str
    ton_amount: Decimal  # exact (jittered) TON the buyer must send
    address: str
    memo: str
    amount: Decimal
    count: int
    target_username: str
    ttl_min: int


_DEFINITIVE_INSUFFICIENT_MESSAGES = {
    # Observed documented business rejection from GGP. Keep this exact: broad
    # free-text matching could retry an ambiguous 5xx after stars were delivered.
    "на балансе недостаточно средств",
    "insufficient funds",
}

# GGP calls time out well below this.  A claim older than the threshold is
# ambiguous (the provider may have delivered before our process died), so it is
# quarantined as ERROR rather than retried and potentially charged twice.
_STALE_GGP_CLAIM_MINUTES = 10


def _is_definitive_insufficient(exc: Exception) -> bool:
    return (
        isinstance(exc, GgpApiError)
        and exc.status == 400
        and exc.message.strip().casefold() in _DEFINITIVE_INSUFFICIENT_MESSAGES
    )


class OrderService:
    def __init__(
        self,
        settings: Settings,
        wata: WataClient,
        db: Database,
        ggp: GgpClient | None = None,
        ton_scanner: TonScanner | None = None,
        ton_rates: TonRates | None = None,
        fragment_rate: FragmentTonRate | None = None,
    ) -> None:
        self._settings = settings
        self._wata = wata
        self._ggp = ggp
        self._ton_scanner = ton_scanner
        self._ton_rates = ton_rates
        self._fragment_rate = fragment_rate
        self._db = db
        self._lang_cache: dict[int, str] = {}
        self._banned_cache: dict[int, bool] = {}
        self._rate_min_price: Decimal | None = None
        self._rate_fetched_at: float = 0.0
        self._ggp_usd_50: Decimal | None = None
        self._ggp_fetched_at: float = 0.0

    def is_admin(self, tg_id: int) -> bool:
        return self._settings.is_admin(tg_id)

    # ── Bans ─────────────────────────────────────────────────────

    async def is_banned(self, tg_id: int) -> bool:
        cached = self._banned_cache.get(tg_id)
        if cached is not None:
            return cached
        async with self._db.session() as session:
            banned = await UserRepository(session).is_banned(tg_id)
        self._banned_cache[tg_id] = banned
        return banned

    async def set_banned(self, tg_id: int, banned: bool) -> None:
        async with self._db.session() as session:
            await UserRepository(session).set_banned(tg_id, banned)
        self._banned_cache[tg_id] = banned

    # ── Admin views ──────────────────────────────────────────────

    async def get_user(self, tg_id: int) -> User | None:
        async with self._db.session() as session:
            return await UserRepository(session).get(tg_id)

    async def user_count(self) -> int:
        async with self._db.session() as session:
            return await UserRepository(session).count()

    async def recent_users(self, limit: int = 6) -> list[User]:
        async with self._db.session() as session:
            return await UserRepository(session).recent(limit)

    async def list_users_page(self, offset: int, limit: int) -> list[User]:
        """A page of users, newest first (for the admin browser)."""
        async with self._db.session() as session:
            return await UserRepository(session).list_page(offset, limit)

    async def find_user_by_username(self, username: str) -> User | None:
        async with self._db.session() as session:
            return await UserRepository(session).find_by_username(username)

    async def period_stats(self, days: int) -> dict[str, Any]:
        async with self._db.session() as session:
            return await OrderRepository(session).period_stats(days)

    # ── Displayed star rates (card: 1h, crypto: 5min) ────────────

    async def _reference_min_price(self) -> Decimal | None:
        """Cached minPrice from the reference username; refreshed at most hourly."""
        fresh = (time.monotonic() - self._rate_fetched_at) < 3600
        if self._rate_min_price is not None and fresh:
            return self._rate_min_price
        try:
            price = await self._wata.get_star_price(self._settings.rate_reference_username)
        except Exception:
            logger.warning("failed to refresh star rate; keeping cached value")
            return self._rate_min_price
        self._rate_min_price = price.min_price
        self._rate_fetched_at = time.monotonic()
        return self._rate_min_price

    async def star_rate(self, markup_percent: float) -> Decimal | None:
        """Per-star price a buyer pays at ``markup_percent``: amount(50) / 50.

        Reads the cached reference minPrice only — never blocks on the network, so
        the menu stays responsive. ``refresh_rates`` (background) keeps it warm.
        """
        min_price = self._rate_min_price
        if min_price is None:
            return None
        quote = compute_amount(min_price, 50, markup_percent)
        return (quote.amount / 50).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    async def _reference_ggp_usd(self) -> Decimal | None:
        """Cached GGP USD price for 50 stars, refreshed every five minutes."""
        if self._ggp is None:
            return None
        fresh = (time.monotonic() - self._ggp_fetched_at) < 300
        if self._ggp_usd_50 is not None and fresh:
            return self._ggp_usd_50
        try:
            price = await self._ggp.get_stars_price(50)
        except Exception:
            logger.warning("failed to refresh GGP rate; keeping cached value")
            return self._ggp_usd_50
        self._ggp_usd_50 = price.usd
        self._ggp_fetched_at = time.monotonic()
        return self._ggp_usd_50

    async def crypto_star_rate(self, markup_percent: float | None = None) -> Decimal | None:
        """Market RUB estimate of the actual rounded TON quote per star.

        Cache-only, like ``star_rate`` — GGP can be slow (retries), so the menu must
        never await it. ``refresh_rates`` (background) keeps the value warm.
        """
        if not self.crypto_enabled:
            return None
        usd = self._ggp_usd_50
        rate = self._fragment_rate.usd if self._fragment_rate is not None else None
        if usd is None or rate is None or time.monotonic() - self._ggp_fetched_at >= 900:
            return None
        ton = usd / rate
        markup = self.crypto_markup_percent if markup_percent is None else markup_percent
        due = compute_ton_due(ton, markup, step=self._settings.ton_amount_jitter_step)
        rub = self.crypto_display_rub(due)
        if rub is None:
            return None
        per_star = rub / 50
        return per_star.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def crypto_display_rub(self, ton_amount: Decimal) -> Decimal | None:
        """Display estimate only; provider RUB accounting remains independent."""
        rate = self._ton_rates.rub if self._ton_rates is not None else None
        if rate is None:
            return None
        return (ton_amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    async def refresh_rates(self) -> None:
        """Fetch/refresh the displayed star-rate caches (WATA + GGP).

        Called once at startup and then periodically by a background task, so the
        menu always reads a warm cache instead of blocking a handler on a slow API.
        Card prices refresh hourly, TON prices and market FX every five minutes.
        Crypto estimates stop displaying after 15 minutes without fresh data.
        """
        refreshes: list[Coroutine[Any, Any, object]] = []
        if self.wata_enabled:
            refreshes.append(self._reference_min_price())
        if self.crypto_enabled:
            refreshes.append(self._reference_ggp_usd())
            if self._ton_rates is not None:
                refreshes.append(self._ton_rates.refresh())
            if self._fragment_rate is not None:
                refreshes.append(self._fragment_rate.get())
        await asyncio.gather(*refreshes)

    @property
    def markup_percent(self) -> float:
        return self._settings.markup_percent

    @property
    def crypto_markup_percent(self) -> float:
        return self._settings.crypto_markup_percent

    @property
    def wata_enabled(self) -> bool:
        """Card (WATA) rail is offered."""
        return self._settings.wata_enabled

    @property
    def crypto_enabled(self) -> bool:
        """Crypto is offered only with delivery and on-chain verification clients."""
        return self._ggp is not None and self._ton_scanner is not None

    @property
    def admin_ids(self) -> list[int]:
        return self._settings.admin_ids

    # ── Language preferences ─────────────────────────────────────

    async def resolve_language(self, tg_id: int, telegram_language_code: str | None) -> str:
        """Return the user's language: stored choice, else Telegram locale, else default."""
        cached = self._lang_cache.get(tg_id)
        if cached is not None:
            return cached
        async with self._db.session() as session:
            stored = await UserRepository(session).get_language(tg_id)
        lang = stored or normalize_lang(telegram_language_code)
        self._lang_cache[tg_id] = lang
        return lang

    async def set_language(self, tg_id: int, language: str) -> None:
        lang = normalize_lang(language)
        async with self._db.session() as session:
            await UserRepository(session).set_language(tg_id, lang)
        self._lang_cache[tg_id] = lang

    async def get_user_language(self, tg_id: int) -> str:
        """Best-effort language lookup for out-of-band messages (e.g. webhooks)."""
        cached = self._lang_cache.get(tg_id)
        if cached is not None:
            return cached
        async with self._db.session() as session:
            stored = await UserRepository(session).get_language(tg_id)
        lang = stored or DEFAULT_LANG
        self._lang_cache[tg_id] = lang
        return lang

    async def get_star_price(self, username: str) -> StarPrice:
        """Validate a Telegram username and return WATA prices (raises WataApiError)."""
        return await self._wata.get_star_price(username)

    async def quote(self, username: str, count: int) -> Quote:
        """Validate the username via WATA and compute the payable amount."""
        price = await self._wata.get_star_price(username)
        quote = compute_amount(price.min_price, count, self._settings.markup_percent)
        return Quote(star_price=price, quote=quote)

    async def create_order(
        self,
        *,
        buyer_tg_id: int,
        buyer_username: str | None,
        target_username: str,
        count: int,
        amount: Decimal,
        partner_owner_tg_id: int | None = None,
        partner_earning: Decimal = Decimal("0"),
        bot_id: int | None = None,
        chat_id: int | None = None,
        message_id: int | None = None,
        redirect_url: str | None = None,
    ) -> CreatedOrder:
        """Create a WATA order and persist it. Returns the payment link."""
        order_id = uuid.uuid4().hex

        async with self._db.session() as session:
            users = UserRepository(session)
            orders = OrderRepository(session)
            await users.upsert(buyer_tg_id, buyer_username)
            await orders.create(
                order_id=order_id,
                buyer_tg_id=buyer_tg_id,
                target_username=target_username,
                count=count,
                amount=amount,
                status=OrderStatusEnum.NEW.value,
                provider="test" if self._settings.test_mode else "wata",
                partner_owner_tg_id=partner_owner_tg_id,
                partner_earning=partner_earning,
                bot_id=bot_id,
                chat_id=chat_id,
                message_id=message_id,
            )

        # Test mode: no WATA call, no payment link. The order waits for a simulated
        # payment (a button in the chat) so referral/partner flows can be exercised.
        if self._settings.test_mode:
            async with self._db.session() as session:
                await OrderRepository(session).update_status(
                    order_id,
                    status=OrderStatusEnum.PENDING.value,
                    price=Decimal("0"),
                    commission=Decimal("0"),
                    margin=amount,
                    payment_link="",
                )
            logger.info(
                "TEST order created order_id=%s count=%d amount=%s", order_id, count, amount
            )
            return CreatedOrder(
                order_id=order_id,
                payment_link="",
                amount=amount,
                count=count,
                target_username=target_username,
                test=True,
            )

        try:
            result = await self._wata.create_order(
                username=target_username,
                count=count,
                amount=amount,
                order_id=order_id,
                description=f"Telegram Stars x{count} for @{target_username}",
                telegram_id=buyer_tg_id,
                success_redirect_url=redirect_url,
                fail_redirect_url=redirect_url,
            )
        except Exception:
            async with self._db.session() as session:
                await OrderRepository(session).update_status(
                    order_id, status=OrderStatusEnum.ERROR.value
                )
            raise

        margin = estimated_margin(amount, result.price, result.commission)
        async with self._db.session() as session:
            await OrderRepository(session).update_status(
                order_id,
                status=OrderStatusEnum.PENDING.value,
                price=result.price,
                commission=result.commission,
                margin=margin,
                payment_link=result.payment_link,
            )

        logger.info(
            "order created order_id=%s count=%d amount=%s margin=%s",
            order_id,
            count,
            amount,
            margin,
        )
        return CreatedOrder(
            order_id=order_id,
            payment_link=result.payment_link,
            amount=amount,
            count=count,
            target_username=target_username,
        )

    # ── Crypto rail (TON via GreenGamePay) ───────────────────────

    async def crypto_quote(self, count: int, total_markup_percent: float) -> CryptoQuote:
        """Convert GGP USD to TON before applying markup and commercial rounding."""
        assert self._ggp is not None, "crypto rail not configured"
        if self._fragment_rate is None:
            raise GgpNetworkError("USD/TON conversion rate not configured")
        price = await self._ggp.get_stars_price(count)
        usd_per_ton = await self._fragment_rate.get()
        ton_cost = price.usd / usd_per_ton
        step = self._settings.ton_amount_jitter_step
        ton_due = compute_ton_due(ton_cost, total_markup_percent, step=step)
        rub_cost = price.quoted_rub / usd_per_ton
        provider_ton_rub = price.quoted_rub / price.usd
        rub_equiv = (ton_due * provider_ton_rub).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        return CryptoQuote(
            ton_cost=ton_cost,
            rub_cost=rub_cost,
            ton_due=ton_due,
            rub_equiv=rub_equiv,
            display_rub=self.crypto_display_rub(ton_due),
        )

    async def create_crypto_order(
        self,
        *,
        buyer_tg_id: int,
        buyer_username: str | None,
        target_username: str,
        count: int,
        ton_due: Decimal,
        rub_equiv: Decimal,
        rub_cost: Decimal,
        partner_owner_tg_id: int | None = None,
        partner_earning: Decimal = Decimal("0"),
        bot_id: int | None = None,
        chat_id: int | None = None,
        message_id: int | None = None,
    ) -> CreatedCryptoOrder:
        """Persist a PENDING crypto order with a unique (jittered) TON amount.

        Issued verified-flow amounts are permanently reserved, and legacy amounts
        are included when choosing the smallest free nanoton suffix. The immutable
        payment deadline ensures a transfer can only be bound to the displayed
        window plus the bounded reconciliation grace.
        """
        order_id = uuid.uuid4().hex
        step = self._settings.ton_amount_jitter_step
        ton_amount = ton_due
        payment_expires_at = datetime.now(tz=UTC) + timedelta(
            minutes=self._settings.crypto_payment_ttl_min
        )
        for attempt in range(6):
            try:
                async with self._db.session() as session:
                    users = UserRepository(session)
                    orders = OrderRepository(session)
                    await users.upsert(buyer_tg_id, buyer_username)
                    taken = await orders.outstanding_ton_amounts()
                    ton_amount = jitter_ton_amount(ton_due, taken, step=step)
                    await orders.create(
                        order_id=order_id,
                        buyer_tg_id=buyer_tg_id,
                        target_username=target_username,
                        count=count,
                        amount=rub_equiv,
                        status=OrderStatusEnum.PENDING.value,
                        provider="ggp",
                        ton_amount=ton_amount,
                        payment_expires_at=payment_expires_at,
                        partner_owner_tg_id=partner_owner_tg_id,
                        partner_earning=partner_earning,
                        bot_id=bot_id,
                        chat_id=chat_id,
                        message_id=message_id,
                    )
                    await orders.update_status(
                        order_id,
                        status=OrderStatusEnum.PENDING.value,
                        price=rub_cost,
                        margin=(rub_equiv - rub_cost),
                    )
                break
            except IntegrityError:
                logger.warning(
                    "TON amount %s taken, retrying (attempt %d)", ton_amount, attempt + 1
                )
        else:
            raise RuntimeError("could not assign a unique TON amount after retries")
        logger.info("crypto order created order_id=%s count=%d ton=%s", order_id, count, ton_amount)
        return CreatedCryptoOrder(
            order_id=order_id,
            ton_amount=ton_amount,
            address=self._settings.ggp_ton_address,
            memo=self._settings.ggp_deposit_memo,
            amount=rub_equiv,
            count=count,
            target_username=target_username,
            ttl_min=self._settings.crypto_payment_ttl_min,
        )

    @property
    def crypto_payment_ttl_min(self) -> int:
        return self._settings.crypto_payment_ttl_min

    async def pending_crypto_order(self, buyer_tg_id: int) -> Order | None:
        """The buyer's current unpaid crypto order (for the one-at-a-time guard)."""
        async with self._db.session() as session:
            return await OrderRepository(session).pending_crypto_order(buyer_tg_id)

    async def cancel_crypto_order(self, order_id: str, buyer_tg_id: int) -> bool:
        """Close a pending invoice while reconciling any already-broadcast TON."""
        async with self._db.session() as session:
            return await OrderRepository(session).cancel_pending_crypto(order_id, buyer_tg_id)

    async def expire_stale_crypto(self) -> int:
        """Auto-cancel crypto orders unpaid past the TTL. Returns how many."""
        async with self._db.session() as session:
            return await OrderRepository(session).expire_stale_crypto(
                self._settings.ton_reconciliation_grace_min
            )

    async def fail_stale_crypto_claims(self) -> int:
        """Quarantine abandoned in-flight GGP deliveries for manual reconciliation."""
        async with self._db.session() as session:
            return await OrderRepository(session).fail_stale_crypto_claims(_STALE_GGP_CLAIM_MINUTES)

    async def oldest_verified_crypto(self) -> Order | None:
        """The oldest on-chain-verified crypto order awaiting GGP delivery."""
        async with self._db.session() as session:
            return await OrderRepository(session).oldest_verified_crypto()

    async def scan_crypto_payments(
        self,
        *,
        since_utime: int | None = None,
        order_id: str | None = None,
        ton_amount: Decimal | None = None,
    ) -> list[Order]:
        """Bind finalized TON transfers to exact pending orders.

        The scanner already rejects non-final, bounced, emulated, aborted and
        wrong-account transactions.  Here we additionally require the configured
        GGP memo; the repository atomically enforces exact amount, payment window,
        provider/status and one-time transaction-hash use.

        Scanner failures deliberately propagate.  The worker therefore does not
        expire orders during an indexing outage, avoiding a payment being missed at
        the edge of its deadline.
        """
        assert self._ton_scanner is not None, "TON scanner not configured"

        if since_utime is None:
            async with self._db.session() as session:
                earliest = await OrderRepository(session).oldest_pending_crypto_created_at()
            if earliest is None:
                return []
            since_utime = max(0, int(earliest.timestamp()))

        transfers = await self._ton_scanner.incoming_transfers(since_utime=max(0, since_utime))
        matched: list[Order] = []
        for transfer in transfers:
            # The comment is a provider account identifier.  It is intentionally
            # exact and case-sensitive: normalising it could credit another account.
            if transfer.comment != self._settings.ggp_deposit_memo:
                continue
            if ton_amount is not None and transfer.value_ton != ton_amount:
                continue
            tx_utime = datetime.fromtimestamp(transfer.utime, tz=UTC)
            async with self._db.session() as session:
                match_kwargs: dict[str, Any] = {
                    "ton_amount": transfer.value_ton,
                    "tx_hash": transfer.hash,
                    "tx_lt": transfer.lt,
                    "tx_utime": tx_utime,
                    "reconciliation_grace_minutes": (self._settings.ton_reconciliation_grace_min),
                }
                if order_id is not None:
                    match_kwargs["order_id"] = order_id
                order = await OrderRepository(session).match_pending_crypto_payment(**match_kwargs)
            if order is not None:
                matched.append(order)
                logger.info(
                    "verified TON payment order_id=%s tx=%s lt=%s",
                    order.order_id,
                    transfer.hash,
                    transfer.lt,
                )
        return matched

    async def try_deliver_crypto(
        self, order_id: str, *, buyer_tg_id: int | None = None
    ) -> tuple[Order | None, str]:
        """Verify a buyer's TON payment, then deliver only a verified order.

        Outcomes are ``delivered``, ``failed``, ``payment_pending`` (no matching
        finalized transfer), ``provider_pending`` (payment is verified but GGP is
        not ready / another verified order is in flight), and ``gone``.
        """
        assert self._ggp is not None and self._ton_scanner is not None, "crypto rail not configured"
        local = await self._get_local(order_id)
        if (
            local is None
            or local.provider != "ggp"
            or (buyer_tg_id is not None and local.buyer_tg_id != buyer_tg_id)
        ):
            return None, "gone"

        if local.status == OrderStatusEnum.PENDING.value:
            if local.ton_amount is None:
                return local, "gone"
            await self.scan_crypto_payments(
                since_utime=max(0, int(local.created_at.timestamp())),
                order_id=local.order_id,
                ton_amount=local.ton_amount,
            )
            local = await self._get_local(order_id)
            if local is None:
                return None, "gone"
        if local.status == OrderStatusEnum.PENDING.value:
            return local, "payment_pending"
        if local.status == OrderStatusEnum.PAID.value:
            return local, "provider_pending"
        if local.status != OrderStatusEnum.REVIEW.value:
            return local, "gone"

        order = await self.deliver_ggp_order(order_id, buyer_tg_id=buyer_tg_id)
        if order is None:
            # A concurrent worker or an earlier verified FIFO order may currently
            # own the serialized GGP claim.  Do not misreport a verified payment as
            # absent; refresh its state and keep it in the provider queue.
            refreshed = await self._get_local(order_id)
            if refreshed is not None and refreshed.status in {
                OrderStatusEnum.REVIEW.value,
                OrderStatusEnum.PAID.value,
            }:
                return refreshed, "provider_pending"
            return refreshed, "gone"
        if order.status == OrderStatusEnum.SUCCESS.value:
            return order, "delivered"
        if order.status == OrderStatusEnum.ERROR.value:
            return order, "failed"
        return order, "provider_pending"

    async def deliver_ggp_order(
        self, order_id: str, *, buyer_tg_id: int | None = None
    ) -> Order | None:
        """Claim an on-chain-verified order and deliver via GGP ``buyStars``.

        REVIEW proves the buyer payment.  The atomic FIFO claim moves REVIEW ->
        PAID before the non-idempotent provider call, preventing double delivery.
        Insufficient provider inventory releases PAID -> REVIEW without discarding
        the stored TON proof; permanent failures become ERROR.
        """
        assert self._ggp is not None, "crypto rail not configured"
        async with self._db.session() as session:
            repo = OrderRepository(session)
            order = await repo.claim_verified_crypto(order_id, buyer_tg_id=buyer_tg_id)
            if order is None:
                return None
            target, count = order.target_username, order.count

        # This balance is only a provider-readiness check.  It cannot prove who paid;
        # the immutable on-chain proof was required before the claim above.
        try:
            balance = await self._ggp.check_ton_balance()
        except asyncio.CancelledError:
            # Balance lookup has no side effect, so this claim is safe to release.
            async with self._db.session() as session:
                await OrderRepository(session).finish_crypto_claim(
                    order_id, status=OrderStatusEnum.REVIEW.value
                )
            raise
        except Exception:
            async with self._db.session() as session:
                await OrderRepository(session).finish_crypto_claim(
                    order_id, status=OrderStatusEnum.REVIEW.value
                )
            raise
        if balance <= 0:
            async with self._db.session() as session:
                return await OrderRepository(session).finish_crypto_claim(
                    order_id, status=OrderStatusEnum.REVIEW.value
                )

        try:
            result = await self._ggp.buy_stars(target, count)
        except asyncio.CancelledError:
            # Cancellation during a non-idempotent request is ambiguous: the
            # provider may already have delivered. Keep PAID; the stale-claim
            # quarantine will turn it into ERROR for manual reconciliation.
            logger.critical(
                "buyStars interrupted; leaving claim for reconciliation order_id=%s",
                order_id,
            )
            raise
        except Exception as exc:
            retryable = _is_definitive_insufficient(exc)
            new_status = OrderStatusEnum.REVIEW.value if retryable else OrderStatusEnum.ERROR.value
            logger.warning(
                "buyStars failed order_id=%s definitive_insufficient=%s: %s",
                order_id,
                retryable,
                exc,
            )
            async with self._db.session() as session:
                return await OrderRepository(session).finish_crypto_claim(
                    order_id, status=new_status
                )

        async with self._db.session() as session:
            updated = await OrderRepository(session).finish_crypto_claim(
                order_id, status=OrderStatusEnum.SUCCESS.value, ggp_tx_id=result.tx_id
            )
        if updated is None:
            logger.error("lost GGP delivery claim after buyStars order_id=%s", order_id)
            return None
        logger.info("crypto order delivered order_id=%s tx=%s", order_id, result.tx_id)
        return updated

    async def sync_order(
        self, order_id: str, raw_webhook: dict[str, Any] | None = None
    ) -> Order | None:
        """Fetch and persist authoritative WATA state for a matching WATA order.

        Provider errors deliberately propagate to the webhook handler so it can return
        a retryable HTTP error.  The notification body is never a status fallback.
        """
        local = await self._get_local(order_id)
        if local is None:
            return None
        if local.provider != "wata":
            logger.warning(
                "ignored WATA webhook for non-WATA order order_id=%s provider=%s",
                order_id,
                local.provider,
            )
            return None

        status = await self._wata.get_order(order_id)
        _verify_wata_order(local, status)

        wata_status = status.status
        mapped_status = map_status(wata_status)

        # Auto-confirm: Review -> Paid (release the stars).
        if wata_status == "Review" and self._settings.auto_confirm:
            try:
                confirmed = await self._wata.confirm_order(order_id)
                if confirmed.order_id != order_id:
                    raise OrderVerificationError(f"WATA confirm id mismatch for order {order_id}")
                # WATA documents the successful POST as Review -> Paid but does not
                # guarantee a response payload.  A successful empty response is
                # therefore authoritative Paid; a returned status is still parsed
                # and validated normally.
                wata_status = confirmed.status or OrderStatusEnum.PAID.value
                mapped_status = map_status(wata_status)
                logger.info("order auto-confirmed order_id=%s status=%s", order_id, wata_status)
            except Exception:
                logger.exception("auto-confirm failed order_id=%s", order_id)
                raise

        # The documented status response always includes ``amount`` but may omit
        # acquisition price/commission. Preserve the values captured at creation
        # instead of accidentally turning the whole payment into apparent margin.
        effective_price = status.price or local.price or Decimal("0")
        effective_commission = status.commission or local.commission or Decimal("0")
        margin = estimated_margin(status.amount, effective_price, effective_commission)
        async with self._db.session() as session:
            repo = OrderRepository(session)
            # Serialize updates for this order. Without the row lock, two valid
            # webhook retries could both read Pending and commit out of order,
            # allowing a stale Review response to overwrite Success.
            current = await repo.get_by_order_id_for_update(order_id)
            if current is None or current.provider != "wata":
                return None
            if not _wata_transition_allowed(current.status, mapped_status):
                logger.warning(
                    "ignored invalid WATA transition order_id=%s current=%s remote=%s",
                    order_id,
                    current.status,
                    mapped_status,
                )
                return None
            updated = await repo.update_status(
                order_id,
                status=mapped_status,
                price=status.price or None,
                commission=status.commission or None,
                margin=margin,
                raw_webhook=raw_webhook,
            )
        return updated

    async def simulate_payment(self, order_id: str, buyer_tg_id: int) -> Order | None:
        """Complete the caller's own pending test order; disabled outside test mode."""
        if not self._settings.test_mode:
            logger.warning("rejected simulated payment outside test mode order_id=%s", order_id)
            return None
        async with self._db.session() as session:
            repo = OrderRepository(session)
            order = await repo.get_by_order_id(order_id)
            if (
                order is None
                or order.provider != "test"
                or order.buyer_tg_id != buyer_tg_id
                or order.status != OrderStatusEnum.PENDING.value
            ):
                return None
            return await repo.update_status(order_id, status=OrderStatusEnum.SUCCESS.value)

    async def list_orders(self, buyer_tg_id: int, limit: int = 20) -> list[Order]:
        async with self._db.session() as session:
            return await OrderRepository(session).list_for_buyer(buyer_tg_id, limit=limit)

    async def active_order_count(self, tg_id: int) -> int:
        async with self._db.session() as session:
            return await OrderRepository(session).count_active(tg_id)

    async def buyer_profile(self, tg_id: int) -> BuyerProfile:
        async with self._db.session() as session:
            registered = await UserRepository(session).get_created_at(tg_id)
            stats = await OrderRepository(session).buyer_stats(tg_id)
        return BuyerProfile(
            registered=registered,
            orders=stats["orders"],
            stars=stats["stars"],
            spent=stats["spent"],
        )

    async def stats(self) -> dict[str, Any]:
        async with self._db.session() as session:
            return await OrderRepository(session).stats()

    async def _get_local(self, order_id: str) -> Order | None:
        async with self._db.session() as session:
            return await OrderRepository(session).get_by_order_id(order_id)

    async def get_order(self, order_id: str) -> Order | None:
        """Fetch a stored order by id (used by the public web API for status polling)."""
        return await self._get_local(order_id)


# ── Referral program ─────────────────────────────────────────────


class WithdrawalError(Exception):
    """Raised when a withdrawal request fails validation; ``key`` is an i18n key."""

    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(key)
        self.key = key
        self.params = params


@dataclass(slots=True)
class ReferralOverview:
    link: str
    referrals: int
    earned: Decimal
    available: Decimal
    has_pending: bool


class ReferralService:
    def __init__(self, settings: Settings, db: Database, *, bot_username: str = "") -> None:
        self._settings = settings
        self._db = db
        self.bot_username = bot_username

    @property
    def percent(self) -> float:
        return self._settings.referral_percent

    def referral_link(self, tg_id: int) -> str:
        return f"https://t.me/{self.bot_username}?start=ref_{tg_id}"

    async def register_referral(self, new_tg_id: int, referrer_tg_id: int) -> bool:
        """Attribute a new user to a referrer (once, never self). Returns True if applied."""
        if new_tg_id == referrer_tg_id:
            return False
        async with self._db.session() as session:
            return await UserRepository(session).set_referrer(new_tg_id, referrer_tg_id)

    async def credit_for_order(self, order: Order) -> tuple[int, Decimal] | None:
        """Credit earnings for a paid order. Idempotent per order.

        Partner-bot orders pay the partner their pre-computed markup; otherwise the
        buyer's referrer earns 5%. Returns (earner_tg_id, amount) on a new credit.
        """
        if order.provider == "wata":
            if order.status not in {
                OrderStatusEnum.PAID.value,
                OrderStatusEnum.SUCCESS.value,
            }:
                return None
        elif order.provider == "ggp":
            if (
                order.status != OrderStatusEnum.SUCCESS.value
                or not order.ton_payment_tx_hash
                or not order.ggp_tx_id
            ):
                logger.warning(
                    "refusing to credit unverified GGP order order_id=%s", order.order_id
                )
                return None
        elif order.provider == "test":
            if order.status != OrderStatusEnum.SUCCESS.value:
                return None
        else:
            return None
        # Partner-bot order: the partner keeps their markup (the 5% referral does
        # not apply here).
        if order.partner_owner_tg_id and order.partner_earning > 0:
            async with self._db.session() as session:
                inserted = await ReferralRepository(session).credit(
                    referrer_tg_id=order.partner_owner_tg_id,
                    referred_tg_id=order.buyer_tg_id,
                    order_id=order.order_id,
                    amount=order.partner_earning,
                )
            if not inserted:
                return None
            logger.info(
                "partner credited owner=%s order_id=%s amount=%s",
                order.partner_owner_tg_id,
                order.order_id,
                order.partner_earning,
            )
            return order.partner_owner_tg_id, order.partner_earning

        percent = Decimal(str(self._settings.referral_percent))
        amount = (order.amount * percent / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN
        )
        if amount <= 0:
            return None
        async with self._db.session() as session:
            referrer = await UserRepository(session).get_referrer(order.buyer_tg_id)
            if not referrer:
                return None
            inserted = await ReferralRepository(session).credit(
                referrer_tg_id=referrer,
                referred_tg_id=order.buyer_tg_id,
                order_id=order.order_id,
                amount=amount,
            )
        if not inserted:
            return None
        logger.info(
            "referral credited referrer=%s order_id=%s amount=%s",
            referrer,
            order.order_id,
            amount,
        )
        return referrer, amount

    async def overview(self, tg_id: int) -> ReferralOverview:
        async with self._db.session() as session:
            referrals = await UserRepository(session).count_referrals(tg_id)
            earned = await ReferralRepository(session).total_earned(tg_id)
            committed = await WithdrawalRepository(session).committed_total(tg_id)
            has_pending = await WithdrawalRepository(session).has_pending(tg_id)
        return ReferralOverview(
            link=self.referral_link(tg_id),
            referrals=referrals,
            earned=earned,
            available=earned - committed,
            has_pending=has_pending,
        )

    async def bot_totals(self) -> tuple[Decimal, Decimal]:
        """Bot-wide wallet figures: (total ever credited, outstanding/owed)."""
        async with self._db.session() as session:
            credited = await ReferralRepository(session).total_credited()
            paid = await WithdrawalRepository(session).total_approved()
        return credited, credited - paid

    def min_for_method(self, method: str) -> Decimal:
        if method == WithdrawalMethod.CRYPTO.value:
            return self._settings.withdraw_min_crypto
        return self._settings.withdraw_min_sbp

    async def create_withdrawal(
        self, *, tg_id: int, method: str, destination: str, amount: Decimal
    ) -> Withdrawal:
        """Validate against balance/minimums and create a pending request."""
        overview = await self.overview(tg_id)
        if overview.has_pending:
            raise WithdrawalError("wd_has_pending")
        minimum = self.min_for_method(method)
        if amount < minimum:
            raise WithdrawalError("wd_below_min", min=minimum)
        if amount > overview.available:
            raise WithdrawalError("wd_over_balance", available=overview.available)
        async with self._db.session() as session:
            return await WithdrawalRepository(session).create(
                user_tg_id=tg_id, amount=amount, method=method, destination=destination
            )

    async def list_withdrawals(self, tg_id: int, limit: int = 20) -> list[Withdrawal]:
        async with self._db.session() as session:
            return await WithdrawalRepository(session).list_for_user(tg_id, limit=limit)

    async def get_withdrawal(self, withdrawal_id: int) -> Withdrawal | None:
        async with self._db.session() as session:
            return await WithdrawalRepository(session).get(withdrawal_id)

    async def approve_withdrawal(
        self, withdrawal_id: int, *, admin_id: int, proof_type: str, proof_value: str
    ) -> Withdrawal | None:
        async with self._db.session() as session:
            return await WithdrawalRepository(session).resolve(
                withdrawal_id,
                status=WithdrawalStatus.APPROVED.value,
                resolved_by=admin_id,
                proof_type=proof_type,
                proof_value=proof_value,
            )

    async def reject_withdrawal(
        self, withdrawal_id: int, *, admin_id: int, reason: str
    ) -> Withdrawal | None:
        async with self._db.session() as session:
            return await WithdrawalRepository(session).resolve(
                withdrawal_id,
                status=WithdrawalStatus.REJECTED.value,
                resolved_by=admin_id,
                reject_reason=reason,
            )


# ── Partner bots (managed bots hosted by us) ─────────────────────


class PartnerService:
    """Manages partner-owned bots.

    A partner sets their own markup (on top of the operator markup, total capped
    at WATA's +50%). The partner earns that markup on each sale, minus the
    operator's cut (``PARTNER_COMMISSION_PERCENT``, e.g. 10% of the markup). The
    partner's share is paid into the shared referral wallet / withdrawal system.
    """

    def __init__(self, settings: Settings, db: Database) -> None:
        self._settings = settings
        self._db = db
        self._markup_cache: dict[int, Decimal] = {}

    @property
    def operator_cut_percent(self) -> Decimal:
        """The operator's commission, as a percent of the partner's markup."""
        return Decimal(str(self._settings.partner_commission_percent))

    @property
    def max_partner_markup(self) -> Decimal:
        """Room left under WATA's +50% cap after the operator's own markup."""
        room = Decimal("50") - Decimal(str(self._settings.markup_percent))
        return room if room > Decimal("0") else Decimal("0")

    def partner_share(self, gross_markup: Decimal) -> Decimal:
        """The partner's take of their markup money, after the operator's cut."""
        keep = (Decimal("100") - self.operator_cut_percent) / Decimal("100")
        share = gross_markup * keep
        return share.quantize(Decimal("0.01"), rounding=ROUND_DOWN) if share > 0 else Decimal("0")

    async def partner_markup(self, bot_id: int) -> Decimal:
        """The bot's markup; 0 for the main bot or unknown bots."""
        cached = self._markup_cache.get(bot_id)
        if cached is not None:
            return cached
        async with self._db.session() as session:
            bot = await PartnerBotRepository(session).get_by_bot_id(bot_id)
        markup = bot.markup_percent if bot else Decimal("0")
        self._markup_cache[bot_id] = markup
        return markup

    async def set_markup(self, bot_id: int, markup_percent: Decimal) -> PartnerBot | None:
        clamped = max(Decimal("0"), min(markup_percent, self.max_partner_markup))
        async with self._db.session() as session:
            bot = await PartnerBotRepository(session).set_markup(bot_id, clamped)
        if bot is not None:
            self._markup_cache[bot_id] = clamped
        return bot

    async def create_bot(
        self, *, owner_tg_id: int, bot_id: int, username: str | None, token: str
    ) -> PartnerBot:
        self._markup_cache[bot_id] = Decimal("0")
        async with self._db.session() as session:
            return await PartnerBotRepository(session).create(
                owner_tg_id=owner_tg_id,
                bot_id=bot_id,
                username=username,
                token=token,
                markup_percent=Decimal("0"),
            )

    async def list_for_owner(self, owner_tg_id: int) -> list[PartnerBot]:
        async with self._db.session() as session:
            return await PartnerBotRepository(session).list_for_owner(owner_tg_id)

    async def list_active(self) -> list[PartnerBot]:
        async with self._db.session() as session:
            return await PartnerBotRepository(session).list_active()

    async def owner_of(self, bot_id: int) -> int | None:
        async with self._db.session() as session:
            bot = await PartnerBotRepository(session).get_by_bot_id(bot_id)
        return bot.owner_tg_id if bot else None

    async def get_bot(self, bot_id: int) -> PartnerBot | None:
        async with self._db.session() as session:
            return await PartnerBotRepository(session).get_by_bot_id(bot_id)

    async def has_bot(self, owner_tg_id: int) -> bool:
        """True if the owner already has a partner bot (one bot per owner)."""
        async with self._db.session() as session:
            bots = await PartnerBotRepository(session).list_for_owner(owner_tg_id)
        return bool(bots)

    async def toggle_bot(self, bot_id: int) -> PartnerBot | None:
        """Flip a partner bot's active flag. Returns the updated bot."""
        async with self._db.session() as session:
            repo = PartnerBotRepository(session)
            bot = await repo.get_by_bot_id(bot_id)
            if bot is None:
                return None
            return await repo.set_active(bot_id, not bot.active)

    async def delete_bot(self, bot_id: int) -> bool:
        """Remove a partner bot (e.g. its token was revoked). Returns True if deleted.

        Order history keeps its own ``partner_owner_tg_id``/``partner_earning``, so
        already-earned payouts are unaffected; the owner may create a new bot again.
        """
        self._markup_cache.pop(bot_id, None)
        async with self._db.session() as session:
            return await PartnerBotRepository(session).delete(bot_id)

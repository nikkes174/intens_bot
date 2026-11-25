import asyncio
import logging
import os
from contextlib import suppress
from decimal import Decimal
from typing import Dict, Optional, Set, Tuple

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from dotenv import load_dotenv
from yookassa import Configuration, Payment

from config import PAYMENT_AMOUNT
from tgbot.db.crud_users import UserCrud
from tgbot.db.analytics import AnalyticsService
from tgbot.db.db import AsyncSessionLocal
from tgbot.db.models import FunnelEventModel, PaymentAnalyticsModel, PaymentInviteModel, utcnow
from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError


load_dotenv()

logger = logging.getLogger(__name__)


class PaymentService:

    def __init__(self) -> None:
        self.shop_id = os.getenv("YOOKASSA_SHOP_ID")
        self.secret_key = os.getenv("YOOKASSA_SECRET_KEY")
        self.return_url = os.getenv("YOOKASSA_RETURN_URL")
        self.channel_id = os.getenv("CHANNEL_CHAT_ID")
        self.amount = PAYMENT_AMOUNT
        self.vat_code = os.getenv("YOOKASSA_VAT_CODE")
        self._tasks: Set[asyncio.Task] = set()
        self._payment_tasks: Dict[str, asyncio.Task] = {}
        self._recovery_task: Optional[asyncio.Task] = None
        self.recovery_interval = 60

    def _configure_yookassa(self) -> None:
        if not self.shop_id or not self.secret_key:
            raise RuntimeError("YooKassa credentials are not configured")
        Configuration.account_id = self.shop_id
        Configuration.secret_key = self.secret_key

    def _get_channel_id(self) -> int:
        if not self.channel_id:
            raise RuntimeError("CHANNEL_CHAT_ID is not configured")
        return int(self.channel_id)

    def is_payment_channel(self, chat_id: int) -> bool:
        return chat_id == self._get_channel_id()

    async def create_payment(
        self,
        user_id: int,
        username: Optional[str],
        email: str,
        visit_id: Optional[int] = None,
    ) -> Tuple[str, str]:
        self._configure_yookassa()
        if not self.vat_code:
            raise RuntimeError("YOOKASSA_VAT_CODE is not configured")
        try:
            vat_code = int(self.vat_code)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("YOOKASSA_VAT_CODE must be an integer") from exc
        payload = {
            "amount": {"value": self.amount, "currency": "RUB"},
            "confirmation": {
                "type": "redirect",
                "return_url": self.return_url or "https://t.me/",
            },
            "capture": True,
            "description": f"Подписка для пользователя {user_id}",
            "metadata": {"user_id": str(user_id)},
            "receipt": {
                "customer": {"email": email},
                "items": [{
                    "description": "Подписка на закрытый канал на 1 месяц",
                    "quantity": "1.00",
                    "amount": {"value": self.amount, "currency": "RUB"},
                    "payment_mode": "full_payment",
                    "payment_subject": "service",
                    "vat_code": vat_code,
                }],
            },
        }
        payment = await asyncio.to_thread(Payment.create, payload)

        # Keep the existing users/payment_id flow for compatibility.
        async with AsyncSessionLocal() as session:
            await UserCrud(session).set_payment_pending(
                user_id, username, payment.id
            )

        # This write is intentionally isolated: once YooKassa has created a
        # payment, an analytics outage must not make the handler create
        # another payment on retry.

        try:
            amount, currency = self._payment_amount(payment)
            async with AsyncSessionLocal() as session:
                await AnalyticsService(session).create_payment_record(
                    payment_id=payment.id,
                    user_id=user_id,
                    amount=amount,
                    currency=currency,
                    visit_id=visit_id,
                )
        except Exception:
            logger.exception(
                "Не удалось сохранить аналитику платежа %s; платёж уже создан",
                payment.id,
            )

        return payment.id, payment.confirmation.confirmation_url

    @staticmethod
    def _payment_amount(payment) -> Tuple[Decimal, str]:
        amount_object = getattr(payment, "amount", None)
        value = getattr(amount_object, "value", None)
        currency = getattr(amount_object, "currency", None) or "RUB"
        if value is None:
            raise RuntimeError("YooKassa did not return the created payment amount")
        return Decimal(str(value)), str(currency)

    def start_payment_monitor(
        self,
        payment_id: str,
        user_id: int,
        bot: Bot,
        visit_id: Optional[int] = None,
    ) -> None:
        existing = self._payment_tasks.get(payment_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(
            self._wait_for_payment(payment_id, user_id, bot, visit_id)
        )
        self._tasks.add(task)
        self._payment_tasks[payment_id] = task

        def cleanup(completed_task: asyncio.Task) -> None:
            self._tasks.discard(completed_task)
            if self._payment_tasks.get(payment_id) is completed_task:
                self._payment_tasks.pop(payment_id, None)

        task.add_done_callback(cleanup)

    def schedule_recovery(self, bot: Bot) -> None:
        if self._recovery_task is not None and not self._recovery_task.done():
            return
        task = asyncio.create_task(self._recovery_loop(bot))
        self._recovery_task = task
        self._tasks.add(task)

        def cleanup(completed_task: asyncio.Task) -> None:
            self._tasks.discard(completed_task)
            if self._recovery_task is completed_task:
                self._recovery_task = None

        task.add_done_callback(cleanup)

    async def _recovery_loop(self, bot: Bot) -> None:
        while True:
            await self.recover_pending_payments(bot)
            await asyncio.sleep(self.recovery_interval)

    async def _wait_for_payment(
        self,
        payment_id: str,
        user_id: int,
        bot: Bot,
        visit_id: Optional[int] = None,
    ) -> None:

        for _ in range(120):
            try:
                self._configure_yookassa()
                payment = await asyncio.to_thread(Payment.find_one, payment_id)
            except Exception:
                await asyncio.sleep(15)
                continue

            if payment.status == "succeeded":
                analytics_first = False
                try:
                    async with AsyncSessionLocal() as session:
                        analytics = AnalyticsService(session)
                        record = await analytics.get_payment(payment_id)
                        if record is None:
                            amount, currency = self._payment_amount(payment)
                            await analytics.create_payment_record(
                                payment_id=payment_id,
                                user_id=user_id,
                                amount=amount,
                                currency=currency,
                                visit_id=visit_id,
                            )
                        analytics_first = await analytics.mark_payment_success(
                            payment_id
                        )
                except Exception:
                    logger.exception(
                        "Не удалось обновить аналитику успешного платежа %s",
                        payment_id,
                    )

                try:
                    async with AsyncSessionLocal() as session:
                        saved = await UserCrud(session).mark_payment_succeeded(
                            user_id, payment_id
                        )
                except Exception:
                    logger.exception(
                        "Не удалось обновить legacy-статус платежа %s", payment_id
                    )
                    saved = False

                if saved or analytics_first:
                    await self._send_join_request_link(user_id, bot, payment_id)
                return

            if payment.status == "canceled":
                try:
                    async with AsyncSessionLocal() as session:
                        analytics = AnalyticsService(session)
                        record = await analytics.get_payment(payment_id)
                        if record is None:
                            amount, currency = self._payment_amount(payment)
                            await analytics.create_payment_record(
                                payment_id=payment_id,
                                user_id=user_id,
                                amount=amount,
                                currency=currency,
                                visit_id=visit_id,
                                status="canceled",
                            )
                        else:
                            await analytics.mark_payment_canceled(payment_id)
                except Exception:
                    logger.exception(
                        "Не удалось обновить аналитику отменённого платежа %s",
                        payment_id,
                    )
                return
            await asyncio.sleep(15)

    async def _save_payment_invite(
        self, payment_id: str, user_id: int, invite_link: str
    ) -> PaymentInviteModel:
        async with AsyncSessionLocal() as session:
            saved = PaymentInviteModel(
                payment_id=payment_id,
                user_id=user_id,
                invite_link=invite_link,
            )
            session.add(saved)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                result = await session.execute(
                    select(PaymentInviteModel).where(
                        PaymentInviteModel.payment_id == payment_id
                    )
                )
                existing = result.scalar_one_or_none()
                if existing is None:
                    raise
                if existing.user_id != user_id:
                    raise RuntimeError(
                        "Payment invite belongs to a different Telegram user"
                    )
                return existing
            return saved

    async def _mark_payment_invite_sent(self, payment_id: str) -> None:
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(PaymentInviteModel)
                .where(
                    PaymentInviteModel.payment_id == payment_id,
                    PaymentInviteModel.sent_at.is_(None),
                )
                .values(sent_at=utcnow())
            )
            await session.commit()

    async def _record_access_link_sent(self, user_id: int, payment_id: str) -> None:
        async with AsyncSessionLocal() as session:
            analytics = AnalyticsService(session)
            payment = await analytics.get_payment(payment_id)
            await analytics.log_event(
                user_id=user_id,
                event="access_link_sent",
                visit_id=payment.visit_id if payment else None,
                payment_id=payment_id,
            )

    async def _send_join_request_link(
        self, user_id: int, bot: Bot, payment_id: Optional[str] = None
    ) -> None:
        # Keep the old direct-call behavior for legacy callers that do not
        # have a payment id.  New payments always use the durable mapping.
        if payment_id is None:
            invite = await bot.create_chat_invite_link(
                chat_id=self._get_channel_id(),
                name=f"payment-{user_id}",
                creates_join_request=True,
            )
            await bot.send_message(
                user_id,
                "✅Оплата подтверждена✅\n 👇🏾Перейдите по ссылке и отправьте заявку на вступление в канал:\n"
                f"{invite.invite_link}",
            )
            return

        try:
            async with AsyncSessionLocal() as session:
                analytics = AnalyticsService(session)
                if await analytics.has_event(
                    user_id, "access_link_sent", payment_id
                ):
                    return
                result = await session.execute(
                    select(PaymentInviteModel).where(
                        PaymentInviteModel.payment_id == payment_id
                    )
                )
                saved = result.scalar_one_or_none()
        except Exception:
            logger.exception("Не удалось проверить invite для %s", payment_id)
            raise

        if saved is not None:
            if saved.user_id != user_id:
                raise RuntimeError(
                    "Payment invite belongs to a different Telegram user"
                )
            if saved.sent_at is not None:
                await self._record_access_link_sent(user_id, payment_id)
                return
            invite_link = saved.invite_link
        else:
            invite = await bot.create_chat_invite_link(
                chat_id=self._get_channel_id(),
                name=f"payment-{user_id}",
                creates_join_request=True,
            )
            # Persist before send.  If Telegram delivery fails, recovery can
            # reuse this exact link instead of creating another invite.
            saved = await self._save_payment_invite(
                payment_id, user_id, invite.invite_link
            )
            invite_link = saved.invite_link

        await bot.send_message(
            user_id,
            "✅Оплата подтверждена✅\n 👇🏾Перейдите по ссылке и отправьте заявку на вступление в канал:\n"
            f"{invite_link}",
        )
        try:
            await self._mark_payment_invite_sent(payment_id)
        except Exception:
            logger.exception("Не удалось отметить отправку invite для %s", payment_id)
        try:
            await self._record_access_link_sent(user_id, payment_id)
        except Exception:
            logger.exception(
                "Не удалось записать access_link_sent для %s", payment_id
            )

    async def approve_join_request(self, join_request, bot: Bot) -> bool:
        async with AsyncSessionLocal() as session:
            is_paid = await UserCrud(session).has_successful_payment(join_request.from_user.id)

        if is_paid:
            await join_request.approve()
            try:
                async with AsyncSessionLocal() as session:
                    analytics = AnalyticsService(session)
                    payment_id = None
                    visit_id = None
                    invite_url = join_request.invite_link.invite_link
                    result = await session.execute(
                        select(PaymentInviteModel).where(
                            PaymentInviteModel.invite_link == invite_url,
                            PaymentInviteModel.user_id
                            == join_request.from_user.id,
                        )
                    )
                    payment_invite = result.scalar_one_or_none()
                    if payment_invite is not None:
                        payment = await analytics.get_payment(
                            payment_invite.payment_id
                        )
                        if (
                            payment is not None
                            and payment.user_id == join_request.from_user.id
                            and payment.status == "succeeded"
                        ):
                            payment_id = payment.payment_id
                            visit_id = payment.visit_id

                    # A legacy invite has no durable payment mapping.  Keep
                    # the existing approval behavior, but never attribute it
                    # to an arbitrary latest payment.
                    if payment_id is None:
                        already_logged = await analytics.has_event(
                            join_request.from_user.id, "access_granted"
                        )
                        if not already_logged:
                            await analytics.log_event(
                                user_id=join_request.from_user.id,
                                event="access_granted",
                                visit_id=visit_id,
                            )
                    elif not await analytics.has_event(
                        join_request.from_user.id,
                        "access_granted",
                        payment_id,
                    ):
                        await analytics.log_event(
                            user_id=join_request.from_user.id,
                            event="access_granted",
                            visit_id=visit_id,
                            payment_id=payment_id,
                        )
            except Exception:
                logger.exception("Не удалось записать access_granted")
            invite_link = join_request.invite_link.invite_link
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Перейти", url=invite_link)
            ]])
            await bot.send_message(
                join_request.from_user.id,
                "🎉Вы были приняты в канал🎉",
                reply_markup=keyboard,
            )
            return True

        with suppress(Exception):
            await join_request.decline()
        return False

    async def recover_pending_payments(self, bot: Bot) -> None:
        """Recover pending monitors and successful payments without delivery."""

        payment_users = {}
        undelivered = []
        tracked_payment_ids = set()
        try:
            async with AsyncSessionLocal() as session:
                tracked_result = await session.execute(
                    select(PaymentAnalyticsModel.payment_id)
                )
                tracked_payment_ids = set(tracked_result.scalars().all())
                result = await session.execute(
                    select(PaymentAnalyticsModel)
                    .where(PaymentAnalyticsModel.status == "pending")
                )
                for payment in result.scalars().all():
                    payment_users.setdefault(
                        payment.payment_id,
                        (payment.user_id, payment.visit_id),
                    )

                undelivered_result = await session.execute(
                    select(PaymentAnalyticsModel).where(
                        PaymentAnalyticsModel.status == "succeeded",
                        ~exists().where(
                            FunnelEventModel.payment_id
                            == PaymentAnalyticsModel.payment_id,
                            FunnelEventModel.event == "access_link_sent",
                        ),
                    )
                )
                undelivered = undelivered_result.scalars().all()
                tracked_payment_ids.update(
                    payment.payment_id for payment in undelivered
                )

                # Payments created by an older version have no analytics row.
                # Keep their legacy monitor behavior instead of abandoning
                # them after the migration.
                for user in await UserCrud(session).get_users_list():
                    if (
                        user.payment_id
                        and not user.payment_ok
                        and user.payment_id not in tracked_payment_ids
                    ):
                        payment_users.setdefault(user.payment_id, (user.user_id, None))
        except Exception:
            logger.exception("Не удалось восстановить pending-платежи")
            return

        for payment_id, (user_id, visit_id) in payment_users.items():
            self.start_payment_monitor(
                payment_id,
                user_id,
                bot,
                visit_id=visit_id,
            )

        for payment in undelivered:
            try:
                # Restore the legacy flag when this payment still is the
                # payment tracked by the original users table.  The invite is
                # sent from the immutable analytics record either way.
                async with AsyncSessionLocal() as session:
                    await UserCrud(session).mark_payment_succeeded(
                        payment.user_id, payment.payment_id
                    )
                await self._send_join_request_link(
                    payment.user_id,
                    bot,
                    payment.payment_id,
                )
            except Exception:
                logger.exception(
                    "Не удалось восстановить invite для платежа %s",
                    payment.payment_id,
                )


payment_service = PaymentService()

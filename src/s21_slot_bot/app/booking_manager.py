import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, tzinfo
from typing import Literal

import cashews
from pydantic import AwareDatetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Job

from s21_slot_bot.app.consts import (
    BOOKING_REFRESHER_JOB_NAME,
    CURRENT_BOOKINGS_SEARCH_WINDOW,
    UPCOMING_REVIEW_REMINDER_WINDOW,
)
from s21_slot_bot.app.errors import AppNotInitializedError, BookingRefresherError
from s21_slot_bot.app.flows.actions import BookFlowAction
from s21_slot_bot.app.messenger import Messenger
from s21_slot_bot.app.models import (
    App,
    BotInstance,
    CustomContext,
    FlowCategory,
    IntervalSec,
    Lifecycle,
)
from s21_slot_bot.app.utils import get_tzinfo
from s21_slot_bot.client.errors import School21Error, School21NoPointsError, School21SlotNotFoundError
from s21_slot_bot.client.models import (
    ActualBooking,
    BookingBase,
    BookingChanges,
    BookingDirection,
    DryRevieweeBooking,
    NotificationKey,
    RevieweeBooking,
    VerifierBooking,
)
from s21_slot_bot.client.s21_client import School21Client
from s21_slot_bot.common import markdown
from s21_slot_bot.common.id import hash_id
from s21_slot_bot.common.logger import LogEntity, LoggerLike, get_id_logger
from s21_slot_bot.common.time import dt_to_markdown, dt_to_pretty, safe_isoz_to_dt


class BookingManager:
    def __init__(
        self,
        s21_client: School21Client,
        messenger: Messenger,
        app: App,
        refresh_interval: IntervalSec,
        chat_id: int,
    ):
        self._s21_client = s21_client
        self._messenger = messenger
        self._state = Lifecycle.STOPPED
        self._dry_reviewee_bookings: dict[str, DryRevieweeBooking] = {}
        self._reviewee_bookings: dict[str, RevieweeBooking] = {}
        self._verifier_bookings: dict[str, VerifierBooking] = {}
        self._booking_lock = asyncio.Lock()
        self._notifications_sent: set[NotificationKey] = set()
        self._app = app
        self._refresh_interval = refresh_interval
        self._chat_id = chat_id
        self._job: Job[CustomContext] | None = None

    @property
    def reviewee_bookings(self) -> dict[str, RevieweeBooking]:
        return self._reviewee_bookings.copy()

    @property
    def dry_reviewee_bookings(self) -> dict[str, DryRevieweeBooking]:
        return self._dry_reviewee_bookings.copy()

    @property
    def verifier_bookings(self) -> dict[str, VerifierBooking]:
        return self._verifier_bookings.copy()

    @property
    def state(self) -> Lifecycle:
        return self._state

    @property
    def is_refreshing(self) -> bool:
        return self._job is not None

    async def initialize_verifier_bookings(self, app: App, logger: LoggerLike) -> None:
        logger.info("Initializing verifier bookings")
        now = datetime.now(tz=get_tzinfo(app))
        search_to = now + CURRENT_BOOKINGS_SEARCH_WINDOW
        bookings = await self._s21_client.get_verifier_bookings(now, search_to, logger)
        async with self._booking_lock:
            self._verifier_bookings = bookings

    async def start_refreshing(self, logger: LoggerLike, run_immediately: bool = True) -> None:
        if not self._app.job_queue:
            raise AppNotInitializedError("очередь задач не инициализирована")
        if self._job is not None:
            logger.info("Booking refresher is already running")
            return
        logger.info("Starting the booking refresher job")
        self._job = self._app.job_queue.run_repeating(
            self._refresh_bookings,
            self._refresh_interval,
            chat_id=self._chat_id,
            name=BOOKING_REFRESHER_JOB_NAME,
        )
        self._state = Lifecycle.RUNNING
        if run_immediately:
            await self._job.run(self._app)

    def stop_refreshing(
        self,
        logger: LoggerLike,
        state: Literal[Lifecycle.STOPPED, Lifecycle.FAILED] = Lifecycle.STOPPED,
    ) -> None:
        if self._job is not None:
            logger.info("Stopping the booking refresher job")
            self._job.schedule_removal()
            self._job = None
        else:
            logger.info("Booking refresher job is already stopped")
        self._state = state

    async def refresh_now(self, context: CustomContext, logger: LoggerLike) -> None:
        if self._state == Lifecycle.RUNNING:
            await self._refresh_bookings(context)
        else:
            logger.info("Booking refresher job is not running, no refreshing will be done until it's started")

    async def book_dry(
        self,
        inst: BotInstance,
        answer_id: str,
        start_time: datetime,
        end_time: datetime,
        context: CustomContext,
    ) -> None:
        cfg = inst.cfg
        dry_run_id = hash_id(f"{cfg.project_id}|{start_time}")
        dry_booking = DryRevieweeBooking(
            id=dry_run_id,
            answer_id=answer_id,
            project_id=cfg.project_id,
            project_name=cfg.project_name,
            start=start_time,
            end=end_time,
        )
        async with self._booking_lock:
            self._dry_reviewee_bookings[dry_run_id] = dry_booking
        inst.stats.attempts_success += 1
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "📝 записаться",
                        callback_data=f"{FlowCategory.BOOK}:{BookFlowAction.BOOK_ATTEMPT_MANUAL}"
                        f":{cfg.bot_id}:{dry_run_id}",
                    )
                ]
            ]
        )
        await self._messenger.send(
            context,
            f"🔔 бот #{cfg.bot_id} ({markdown.backtick_wrap(cfg.project_name)}) остановлен: найден слот\n"
            f"начало: {dt_to_markdown(start_time, tz=get_tzinfo(context))}",
            kb=kb,
            parse_mode=ParseMode.MARKDOWN_V2,
        )

    @cashews.invalidate("get_review_info:*")
    async def book(
        self,
        inst: BotInstance,
        answer_id: str,
        start_time: datetime,
        end_time: datetime,
        logger: LoggerLike,
        context: CustomContext,
    ) -> bool:
        are_review_points_left = True
        cfg = inst.cfg
        tz = get_tzinfo(context)
        try:
            await self.refresh_now(context, logger)
            async with self._booking_lock:
                booking_id = await self._s21_client.book(
                    answer_id=answer_id,
                    start_time=start_time,
                    logger=logger,
                )
                booking = RevieweeBooking(
                    id=booking_id,
                    answer_id=answer_id,
                    project_id=cfg.project_id,
                    project_name=cfg.project_name,
                    start=start_time,
                    end=end_time,
                    is_online=True,
                )
                self._reviewee_bookings[booking_id] = booking
            inst.stats.currently_booked += 1
            inst.stats.attempts_success += 1
            await self._messenger.send(
                context,
                f"✅ бот #{cfg.bot_id} ({markdown.backtick_wrap(cfg.project_name)}): записан\n"
                f"начало: {dt_to_markdown(start_time, tz=tz)}\n"
                f"проверок: {inst.stats.currently_booked}/{cfg.required_reviews}",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except School21NoPointsError:
            logger.info("Not enough points to book")
            are_review_points_left = False
            await self._messenger.send(
                context,
                f"⛔ бот #{cfg.bot_id} ({cfg.project_name}): остановлен, недостаточно PRP",
            )
        except School21SlotNotFoundError as e:
            logger.info("Slot is no longer available")
            inst.stats.attempts_failed += 1
            isoz = e.location.get("input", {}).get("startTime") if e.location else None
            cancelled_time = safe_isoz_to_dt(
                isoz=isoz,
                tz=UTC,
                logger=logger,
            )
            cancelled_slot_message = (
                f"слот на {dt_to_pretty(cancelled_time, tz=tz)} недоступен" if cancelled_time else "слот недоступен"
            )
            await self._messenger.send(
                context,
                f"⚠️ бот #{cfg.bot_id} ({cfg.project_name}): {cancelled_slot_message}",
            )
        return are_review_points_left

    def pop_dry(self, dry_run_id: str) -> DryRevieweeBooking | None:
        dry_booking = self._dry_reviewee_bookings.pop(dry_run_id, None)
        return dry_booking

    async def _refresh_bookings(self, context: CustomContext) -> None:
        logger = get_id_logger(LogEntity.BOOKING_REFRESHER)
        logger.info("Refreshing bookings")
        now = datetime.now(tz=get_tzinfo(context))
        search_to = now + CURRENT_BOOKINGS_SEARCH_WINDOW
        try:
            fresh_reviewee_bookings, fresh_verifier_bookings = await asyncio.gather(
                self._s21_client.get_reviewee_bookings(now, search_to, logger),
                self._s21_client.get_verifier_bookings(now, search_to, logger),
            )
            reviewee_changes, verifier_changes = await self._update_bookings(
                fresh_reviewee_bookings, fresh_verifier_bookings, now, logger
            )
            context.ensured_chat_data.last_booking_refresh_time = now
            if verifier_changes.new:
                await self._notify_on_new_verifier_reviews(verifier_changes.new, context, logger)
            for changes in (reviewee_changes, verifier_changes):
                if changes.cancelled:
                    await self._notify_on_cancelled_reviews(changes.cancelled, context, logger)
                await self._notify_on_upcoming_reviews(changes.active, context, now, logger)
        except School21Error as e:
            logger.exception("Failed to refresh bookings")
            self.stop_refreshing(logger, state=Lifecycle.FAILED)
            raise BookingRefresherError(
                f"ошибка при получении актуальных проверок, задача остановлена: {e.message}"
            ) from e

    async def _update_bookings(
        self,
        fresh_reviewee_bookings: dict[str, RevieweeBooking],
        fresh_verifier_bookings: dict[str, VerifierBooking],
        now: AwareDatetime,
        logger: LoggerLike,
    ) -> tuple[BookingChanges[RevieweeBooking], BookingChanges[VerifierBooking]]:
        async with self._booking_lock:
            stale_reviewee_bookings = self._reviewee_bookings
            stale_verifier_bookings = self._verifier_bookings
            reviewee_changes = self._get_booking_changes(fresh_reviewee_bookings, stale_reviewee_bookings, now, logger)
            verifier_changes = self._get_booking_changes(fresh_verifier_bookings, stale_verifier_bookings, now, logger)
            self._reviewee_bookings = fresh_reviewee_bookings
            self._verifier_bookings = fresh_verifier_bookings
            self._remove_expired_dry_bookings(now, logger)
        return reviewee_changes, verifier_changes

    def _get_booking_changes[T: ActualBooking](
        self,
        fresh_bookings: dict[str, T],
        stale_bookings: dict[str, T],
        now: AwareDatetime,
        logger: LoggerLike,
    ) -> BookingChanges[T]:
        new_ids = fresh_bookings.keys() - stale_bookings.keys()
        removed_ids = stale_bookings.keys() - fresh_bookings.keys()
        new_bookings = [fresh_bookings[booking_id] for booking_id in new_ids]
        cancelled_bookings: list[T] = []
        expired_bookings: list[T] = []
        for booking_id in removed_ids:
            booking = stale_bookings[booking_id]
            self._notifications_sent.discard(self._get_notification_key(booking))
            if is_expired_booking(booking, now):
                expired_bookings.append(booking)
            else:
                cancelled_bookings.append(booking)
        if expired_bookings:
            logger.info("Expired bookings: %s", {booking.id: booking.start for booking in expired_bookings})
        changes: BookingChanges[T] = BookingChanges(
            new=new_bookings,
            cancelled=cancelled_bookings,
            active=list(fresh_bookings.values()),
        )
        return changes

    async def _notify_on_new_verifier_reviews(
        self,
        bookings: list[VerifierBooking],
        context: CustomContext,
        logger: LoggerLike,
    ) -> None:
        logger.info(
            "Notifying on new verifier bookings: %s",
            {
                booking.id: {
                    "project": booking.project_name,
                    "student": booking.student_login,
                    "start": booking.start,
                }
                for booking in bookings
            },
        )
        header = "📝 на твою проверку записались!" if len(bookings) == 1 else "📝 на твои проверки записались!"
        tz = get_tzinfo(context)
        text = self._format_bookings_message(bookings, header, tz)
        await self._messenger.send(context, text, parse_mode=ParseMode.MARKDOWN_V2)
        now = datetime.now(tz=tz)
        for booking in bookings:
            if is_booking_coming_soon(booking, now):
                notification_key = self._get_notification_key(booking)
                self._notifications_sent.add(notification_key)

    async def _notify_on_cancelled_reviews(
        self,
        bookings: Sequence[ActualBooking],
        context: CustomContext,
        logger: LoggerLike,
    ) -> None:
        logger.info(
            "Notifying on cancelled reviews: %s",
            {
                booking.id: {
                    "project": booking.project_name,
                    "student": booking.student_login,
                    "start": booking.start,
                }
                for booking in bookings
            },
        )
        first = bookings[0]
        match first:
            case RevieweeBooking():
                header = "⚠️ проверка отменена!" if len(bookings) == 1 else "⚠️ проверки отменены!"
                await cashews.cache.delete_match("get_review_info:*")
            case VerifierBooking():
                header = (
                    "⚠️ запись на твою проверку отменена!"
                    if len(bookings) == 1
                    else "⚠️ записи на твои проверки отменены!"
                )
        tz = get_tzinfo(context)
        text = self._format_bookings_message(bookings, header, tz)
        await self._messenger.send(context, text, parse_mode=ParseMode.MARKDOWN_V2)

    async def _notify_on_upcoming_reviews(
        self,
        bookings: Sequence[ActualBooking],
        context: CustomContext,
        now: AwareDatetime,
        logger: LoggerLike,
    ) -> None:
        tz = get_tzinfo(context)
        for booking in bookings:
            notification_key = self._get_notification_key(booking)
            if notification_key in self._notifications_sent or not is_booking_coming_soon(booking, now):
                continue
            logger.info("Sending upcoming review notification for booking %s at %s", booking.id, booking.start)
            match booking:
                case RevieweeBooking():
                    header = "🔔 скоро начинается проверка твоего проекта!"
                case VerifierBooking():
                    header = "🔔 скоро начинается проверка, которую ты проводишь!"
            text = self._format_bookings_message([booking], header, tz)
            await self._messenger.send(context, text, parse_mode=ParseMode.MARKDOWN_V2)
            self._notifications_sent.add(notification_key)

    def _remove_expired_dry_bookings(self, now: AwareDatetime, logger: LoggerLike) -> None:
        expired: dict[str, DryRevieweeBooking] = {}
        non_expired: dict[str, DryRevieweeBooking] = {}
        for booking_id, booking in self._dry_reviewee_bookings.items():
            if is_expired_booking(booking, now):
                expired[booking_id] = booking
            else:
                non_expired[booking_id] = booking
        if expired:
            logger.info(
                "Removing %d expired dry bookings: %s",
                len(expired),
                {
                    booking.id: {
                        "project": booking.project_name,
                        "start": booking.start,
                    }
                    for booking in expired.values()
                },
            )
        self._dry_reviewee_bookings = non_expired

    def _get_notification_key(self, booking: ActualBooking) -> NotificationKey:
        match booking:
            case RevieweeBooking():
                direction = BookingDirection.REVIEWEE
            case VerifierBooking():
                direction = BookingDirection.VERIFIER
        key = NotificationKey(id=booking.id, direction=direction)
        return key

    def _format_bookings_message(self, bookings: Sequence[ActualBooking], header: str, tz: tzinfo) -> str:
        sections = [header]
        for booking in sorted(bookings, key=lambda item: item.start):
            sections.append("\n".join(format_booking_details(booking, tz)))
        return "\n\n".join(sections)


# TODO: extended flag to not pass in cancelled notifications
def format_booking_details(booking: ActualBooking, tz: tzinfo) -> list[str]:
    lines = [f"🕒 {dt_to_markdown(booking.start, tz=tz)} → {dt_to_markdown(booking.end, tz=tz)}"]
    if booking.project_name:
        lines.append(f"📚 проект: {markdown.backtick_wrap(booking.project_name)}")
    if booking.student_login:
        lines.append(f"👤 студент: {booking.student_login}")
    if booking.url:
        lines.append(f"🔗 {markdown.format_inline_link('ссылка для подключения', booking.url)}")
    return lines


def is_expired_booking(booking: BookingBase, now: AwareDatetime) -> bool:
    return booking.start <= now


def is_booking_coming_soon(booking: BookingBase, now: AwareDatetime) -> bool:
    return booking.start > now and booking.start - now <= UPCOMING_REVIEW_REMINDER_WINDOW

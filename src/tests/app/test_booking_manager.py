from collections.abc import Callable
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from telegram.ext import Application, Job, JobQueue

from s21_slot_bot.app.booking_manager import BookingManager, is_expired_booking
from s21_slot_bot.app.errors import AppNotInitializedError, BookingRefresherError
from s21_slot_bot.app.messenger import Messenger
from s21_slot_bot.app.models import BotInstance, CustomContext, Lifecycle
from s21_slot_bot.client.errors import School21Error, School21NoPointsError, School21SlotNotFoundError
from s21_slot_bot.client.models import (
    BookingDirection,
    DryRevieweeBooking,
    NotificationKey,
    RevieweeBooking,
    VerifierBooking,
)
from s21_slot_bot.client.s21_client import School21Client
from s21_slot_bot.common.logger import LoggerLike


class TestBookingManager:
    async def test_start_and_stop_refreshing(
        self,
        booking_manager: BookingManager,
        tg_app_mock: Application,
        job_queue_mock: JobQueue,
        job_mock: Job,
        logger_mock: LoggerLike,
    ) -> None:
        job_queue_mock.run_repeating.return_value = job_mock
        await booking_manager.start_refreshing(logger_mock)
        assert booking_manager.state == Lifecycle.RUNNING
        assert booking_manager.is_refreshing
        job_mock.run.assert_awaited_once_with(tg_app_mock)

        await booking_manager.start_refreshing(logger_mock)
        job_queue_mock.run_repeating.assert_called_once()

        booking_manager.stop_refreshing(logger_mock)
        assert booking_manager.state == Lifecycle.STOPPED
        assert not booking_manager.is_refreshing

        booking_manager.stop_refreshing(logger_mock, state=Lifecycle.FAILED)
        assert booking_manager.state == Lifecycle.FAILED

    async def test_start_refreshing_requires_job_queue(
        self,
        booking_manager: BookingManager,
        tg_app_mock: Application,
        logger_mock: LoggerLike,
    ) -> None:
        tg_app_mock.job_queue = None
        with pytest.raises(AppNotInitializedError):
            await booking_manager.start_refreshing(logger_mock)

    async def test_refresh_now_only_runs_when_enabled(
        self,
        booking_manager: BookingManager,
        context: CustomContext,
        logger_mock: LoggerLike,
    ) -> None:
        booking_manager._refresh_bookings = AsyncMock()
        await booking_manager.refresh_now(context, logger_mock)
        booking_manager._refresh_bookings.assert_not_awaited()

        booking_manager._state = Lifecycle.RUNNING
        await booking_manager.refresh_now(context, logger_mock)
        booking_manager._refresh_bookings.assert_awaited_once_with(context)

    async def test_book_dry_and_pop(
        self,
        booking_manager: BookingManager,
        messenger: Messenger,
        bot_instance_factory: Callable[..., BotInstance],
        context: CustomContext,
        now: datetime,
    ) -> None:
        inst = bot_instance_factory()
        messenger.send = AsyncMock()
        end = now + timedelta(minutes=30)
        await booking_manager.book_dry(inst, "answer", now, end, context)
        assert inst.stats.attempts_success == 1
        dry = next(iter(booking_manager.dry_reviewee_bookings.values()))
        assert booking_manager.pop_dry(dry.id) == dry
        assert booking_manager.pop_dry(dry.id) is None

    async def test_book_success(
        self,
        booking_manager: BookingManager,
        s21_client: School21Client,
        messenger: Messenger,
        bot_instance_factory: Callable[..., BotInstance],
        context: CustomContext,
        logger_mock: LoggerLike,
        now: datetime,
    ) -> None:
        inst = bot_instance_factory()
        s21_client.book = AsyncMock(return_value="booking-1")
        messenger.send = AsyncMock()
        end = now + timedelta(minutes=30)
        assert await booking_manager.book(inst, "answer-1", now, end, logger_mock, context) is True
        assert inst.stats.currently_booked == 1
        assert inst.stats.attempts_success == 1
        assert "booking-1" in booking_manager.reviewee_bookings

    async def test_book_no_points(
        self,
        booking_manager: BookingManager,
        s21_client: School21Client,
        messenger: Messenger,
        bot_instance_factory: Callable[..., BotInstance],
        context: CustomContext,
        logger_mock: LoggerLike,
        now: datetime,
    ) -> None:
        inst = bot_instance_factory()
        s21_client.book = AsyncMock(side_effect=School21NoPointsError("no points"))
        messenger.send = AsyncMock()
        end = now + timedelta(minutes=30)
        assert await booking_manager.book(inst, "answer", now, end, logger_mock, context) is False

    @pytest.mark.parametrize("with_location", [True, False])
    async def test_book_slot_not_found(
        self,
        booking_manager: BookingManager,
        s21_client: School21Client,
        messenger: Messenger,
        bot_instance_factory: Callable[..., BotInstance],
        context: CustomContext,
        logger_mock: LoggerLike,
        now: datetime,
        with_location: bool,
    ) -> None:
        inst = bot_instance_factory()
        location = {"input": {"startTime": "2026-08-19T17:30:00.000Z"}} if with_location else None
        s21_client.book = AsyncMock(side_effect=School21SlotNotFoundError("gone", location=location))
        messenger.send = AsyncMock()
        end = now + timedelta(minutes=30)
        assert await booking_manager.book(inst, "answer", now, end, logger_mock, context) is True
        assert inst.stats.attempts_failed == 1
        messenger.send.assert_awaited_once()

    def test_remove_expired_dry_bookings(
        self,
        booking_manager: BookingManager,
        dry_reviewee_booking_factory: Callable[..., DryRevieweeBooking],
        now: datetime,
        logger_mock: LoggerLike,
    ) -> None:
        booking_manager._dry_reviewee_bookings = {
            "expired": dry_reviewee_booking_factory(
                booking_id="expired",
                start=now,
                end=now + timedelta(minutes=30),
            ),
            "future": dry_reviewee_booking_factory(
                booking_id="future", start=now + timedelta(hours=1), end=now + timedelta(hours=2)
            ),
        }
        booking_manager._remove_expired_dry_bookings(now, logger_mock)
        assert set(booking_manager.dry_reviewee_bookings) == {"future"}

    def test_get_booking_changes(
        self,
        booking_manager: BookingManager,
        reviewee_booking_factory: Callable[..., RevieweeBooking],
        logger_mock: LoggerLike,
        now: datetime,
    ) -> None:
        expired = reviewee_booking_factory(booking_id="expired", start=now)
        cancelled = reviewee_booking_factory(booking_id="cancelled", start=now + timedelta(hours=1))
        new = reviewee_booking_factory(booking_id="new", start=now + timedelta(hours=2))
        existing = reviewee_booking_factory(booking_id="existing", start=now + timedelta(hours=3))
        expired_key = NotificationKey(id="expired", direction=BookingDirection.REVIEWEE)
        cancelled_key = NotificationKey(id="cancelled", direction=BookingDirection.REVIEWEE)
        booking_manager._notifications_sent = {expired_key, cancelled_key}

        result = booking_manager._get_booking_changes(
            fresh_bookings={
                "new": new,
                "existing": existing,
            },
            stale_bookings={
                "expired": expired,
                "cancelled": cancelled,
                "existing": existing,
            },
            now=now,
            logger=logger_mock,
        )

        assert result.new == [new]
        assert result.cancelled == [cancelled]
        assert result.active == [new, existing]
        assert not booking_manager._notifications_sent

    def test_get_booking_changes_keeps_notification_for_active_booking(
        self,
        booking_manager: BookingManager,
        reviewee_booking_factory: Callable[..., RevieweeBooking],
        logger_mock: LoggerLike,
        now: datetime,
    ) -> None:
        booking = reviewee_booking_factory(booking_id="booking", start=now + timedelta(minutes=10))
        notification_key = NotificationKey(id=booking.id, direction=BookingDirection.REVIEWEE)
        booking_manager._notifications_sent = {notification_key}

        result = booking_manager._get_booking_changes(
            fresh_bookings={"booking": booking},
            stale_bookings={"booking": booking},
            now=now,
            logger=logger_mock,
        )

        assert result.new == []
        assert result.cancelled == []
        assert result.active == [booking]
        assert booking_manager._notifications_sent == {notification_key}

    @pytest.mark.parametrize("with_url", [True, False])
    async def test_notify_upcoming_reviewee_review(
        self,
        booking_manager: BookingManager,
        messenger: Messenger,
        reviewee_booking_factory: Callable[..., RevieweeBooking],
        context: CustomContext,
        logger_mock: LoggerLike,
        now: datetime,
        with_url: bool,
    ) -> None:
        booking = reviewee_booking_factory(
            booking_id="booking",
            start=now + timedelta(minutes=10),
            url="https://call" if with_url else None,
            student_login="verifier",
        )
        messenger.send = AsyncMock()

        await booking_manager._notify_on_upcoming_reviews(
            [booking],
            context,
            now,
            logger_mock,
        )

        messenger.send.assert_awaited_once()
        text = messenger.send.await_args.args[1]
        assert "скоро начинается проверка твоего проекта" in text
        assert booking.project_name in text
        assert "verifier" in text
        assert ("ссылка для подключения" in text) is with_url
        assert booking_manager._notifications_sent == {
            NotificationKey(id=booking.id, direction=BookingDirection.REVIEWEE)
        }

    async def test_notify_upcoming_verifier_review(
        self,
        booking_manager: BookingManager,
        messenger: Messenger,
        verifier_booking_factory: Callable[..., VerifierBooking],
        context: CustomContext,
        logger_mock: LoggerLike,
        now: datetime,
    ) -> None:
        booking = verifier_booking_factory(
            booking_id="booking",
            start=now + timedelta(minutes=10),
            project_name="SQLB9_OLAP",
            student_login="student",
            url="https://call",
        )
        messenger.send = AsyncMock()

        await booking_manager._notify_on_upcoming_reviews(
            [booking],
            context,
            now,
            logger_mock,
        )

        messenger.send.assert_awaited_once()
        text = messenger.send.await_args.args[1]
        assert "скоро начинается проверка, которую ты проводишь" in text
        assert "SQLB9_OLAP" in text
        assert "student" in text
        assert "https://call" in text
        assert booking_manager._notifications_sent == {
            NotificationKey(
                id=booking.id,
                direction=BookingDirection.VERIFIER,
            )
        }

    async def test_notify_upcoming_reviews_skips_already_notified(
        self,
        booking_manager: BookingManager,
        messenger: Messenger,
        reviewee_booking_factory: Callable[..., RevieweeBooking],
        context: CustomContext,
        logger_mock: LoggerLike,
        now: datetime,
    ) -> None:
        booking = reviewee_booking_factory(booking_id="booking", start=now + timedelta(minutes=10))
        notification_key = NotificationKey(id=booking.id, direction=BookingDirection.REVIEWEE)
        booking_manager._notifications_sent = {notification_key}
        messenger.send = AsyncMock()

        await booking_manager._notify_on_upcoming_reviews(
            [booking],
            context,
            now,
            logger_mock,
        )

        messenger.send.assert_not_awaited()

    async def test_notify_upcoming_reviews_skips_booking_outside_window(
        self,
        booking_manager: BookingManager,
        messenger: Messenger,
        reviewee_booking_factory: Callable[..., RevieweeBooking],
        context: CustomContext,
        logger_mock: LoggerLike,
        now: datetime,
    ) -> None:
        booking = reviewee_booking_factory(start=now + timedelta(hours=1))
        messenger.send = AsyncMock()

        await booking_manager._notify_on_upcoming_reviews(
            [booking],
            context,
            now,
            logger_mock,
        )

        messenger.send.assert_not_awaited()
        assert not booking_manager._notifications_sent

    async def test_notify_cancelled_reviewee_reviews(
        self,
        booking_manager: BookingManager,
        messenger: Messenger,
        reviewee_booking_factory: Callable[..., RevieweeBooking],
        context: CustomContext,
        logger_mock: LoggerLike,
        now: datetime,
    ) -> None:
        messenger.send = AsyncMock()
        bookings = [
            reviewee_booking_factory(
                booking_id="1",
                project_name="P",
                student_login="student1",
                start=now + timedelta(hours=1),
            ),
            reviewee_booking_factory(
                booking_id="2",
                project_name="P",
                student_login="student2",
                start=now + timedelta(hours=2),
            ),
        ]

        with patch("s21_slot_bot.app.booking_manager.cashews.cache.delete_match") as delete_match_mock:
            await booking_manager._notify_on_cancelled_reviews(
                bookings,
                context,
                logger_mock,
            )

        messenger.send.assert_awaited_once()
        delete_match_mock.assert_awaited_once_with("get_review_info:*")
        text = messenger.send.await_args.args[1]
        assert "проверки отменены" in text
        assert text.count("🕒") == 2
        assert "student1" in text
        assert "student2" in text

    async def test_notify_cancelled_verifier_reviews(
        self,
        booking_manager: BookingManager,
        messenger: Messenger,
        verifier_booking_factory: Callable[..., VerifierBooking],
        context: CustomContext,
        logger_mock: LoggerLike,
        now: datetime,
    ) -> None:
        messenger.send = AsyncMock()
        bookings = [
            verifier_booking_factory(
                booking_id="1",
                start=now + timedelta(hours=1),
                student_login="student1",
            ),
            verifier_booking_factory(
                booking_id="2",
                start=now + timedelta(hours=2),
                student_login="student2",
            ),
        ]

        with patch("s21_slot_bot.app.booking_manager.cashews.cache.delete_match") as delete_match_mock:
            await booking_manager._notify_on_cancelled_reviews(
                bookings,
                context,
                logger_mock,
            )

        messenger.send.assert_awaited_once()
        delete_match_mock.assert_not_awaited()
        text = messenger.send.await_args.args[1]
        assert "записи на твои проверки отменены" in text
        assert text.count("🕒") == 2
        assert "student1" in text
        assert "student2" in text

    async def test_notify_new_verifier_reviews(
        self,
        booking_manager: BookingManager,
        messenger: Messenger,
        verifier_booking_factory: Callable[..., VerifierBooking],
        context: CustomContext,
        logger_mock: LoggerLike,
        now: datetime,
    ) -> None:
        messenger.send = AsyncMock()
        bookings = [
            verifier_booking_factory(
                booking_id="1",
                start=now + timedelta(minutes=5),
                student_login="student1",
            ),
            verifier_booking_factory(
                booking_id="2",
                start=now + timedelta(hours=2),
                student_login="student2",
            ),
        ]

        with patch("s21_slot_bot.app.booking_manager.datetime") as datetime_mock:
            datetime_mock.now.return_value = now
            await booking_manager._notify_on_new_verifier_reviews(bookings, context, logger_mock)

        messenger.send.assert_awaited_once()
        text = messenger.send.await_args.args[1]
        assert "на твои проверки записались" in text
        assert text.count("🕒") == 2
        assert "student1" in text
        assert "student2" in text
        assert NotificationKey(id="1", direction=BookingDirection.VERIFIER) in booking_manager._notifications_sent
        assert NotificationKey(id="2", direction=BookingDirection.VERIFIER) not in booking_manager._notifications_sent

    async def test_refresh_bookings(
        self,
        booking_manager: BookingManager,
        s21_client: School21Client,
        messenger: Messenger,
        reviewee_booking_factory: Callable[..., RevieweeBooking],
        verifier_booking_factory: Callable[..., VerifierBooking],
        context: CustomContext,
        now: datetime,
    ) -> None:
        stale_reviewee = reviewee_booking_factory(booking_id="stale-reviewee", start=now + timedelta(hours=2))
        upcoming_reviewee = reviewee_booking_factory(booking_id="upcoming-reviewee", start=now + timedelta(minutes=10))
        new_verifier = verifier_booking_factory(booking_id="new-verifier", start=now + timedelta(hours=1))
        booking_manager._reviewee_bookings = {stale_reviewee.id: stale_reviewee}
        booking_manager._verifier_bookings = {}
        s21_client.get_reviewee_bookings = AsyncMock(return_value={upcoming_reviewee.id: upcoming_reviewee})
        s21_client.get_verifier_bookings = AsyncMock(return_value={new_verifier.id: new_verifier})
        messenger.send = AsyncMock()

        with patch("s21_slot_bot.app.booking_manager.datetime") as datetime_mock:
            datetime_mock.now.return_value = now
            await booking_manager._refresh_bookings(context)

        assert booking_manager.reviewee_bookings == {upcoming_reviewee.id: upcoming_reviewee}
        assert booking_manager.verifier_bookings == {new_verifier.id: new_verifier}
        assert context.ensured_chat_data.last_booking_refresh_time == now
        assert booking_manager._notifications_sent == {
            NotificationKey(id=upcoming_reviewee.id, direction=BookingDirection.REVIEWEE)
        }
        # 1 new verifier booking
        # 1 cancelled reviewee booking
        # 1 upcoming reviewee reminder
        assert messenger.send.await_count == 3

    async def test_refresh_bookings_notifies_new_verifier_bookings_in_one_message(
        self,
        booking_manager: BookingManager,
        s21_client: School21Client,
        messenger: Messenger,
        verifier_booking_factory: Callable[..., VerifierBooking],
        context: CustomContext,
        now: datetime,
    ) -> None:
        verifier_1 = verifier_booking_factory(booking_id="1", start=now + timedelta(hours=1))
        verifier_2 = verifier_booking_factory(booking_id="2", start=now + timedelta(hours=2))
        s21_client.get_reviewee_bookings = AsyncMock(return_value={})
        s21_client.get_verifier_bookings = AsyncMock(
            return_value={
                verifier_1.id: verifier_1,
                verifier_2.id: verifier_2,
            }
        )
        messenger.send = AsyncMock()

        with patch("s21_slot_bot.app.booking_manager.datetime") as datetime_mock:
            datetime_mock.now.return_value = now
            await booking_manager._refresh_bookings(context)

        messenger.send.assert_awaited_once()
        text = messenger.send.await_args.args[1]
        assert "на твои проверки записались" in text
        assert text.count("🕒") == 2

    async def test_refresh_failure(
        self,
        booking_manager: BookingManager,
        s21_client: School21Client,
        context: CustomContext,
        job_mock: Job,
    ) -> None:
        booking_manager._job = job_mock
        booking_manager._state = Lifecycle.RUNNING
        s21_client.get_reviewee_bookings = AsyncMock(side_effect=School21Error("oops"))
        s21_client.get_verifier_bookings = AsyncMock(return_value={})

        with pytest.raises(BookingRefresherError):
            await booking_manager._refresh_bookings(context)

        assert booking_manager.state == Lifecycle.FAILED
        assert not booking_manager.is_refreshing

    @pytest.mark.parametrize(
        ("offset", "expected"),
        [(-1, True), (0, True), (1, False)],
    )
    def test_is_expired_booking(
        self,
        reviewee_booking_factory: Callable[..., RevieweeBooking],
        now: datetime,
        offset: int,
        expected: bool,
    ) -> None:
        booking = reviewee_booking_factory(
            start=now + timedelta(seconds=offset),
        )

        assert is_expired_booking(booking, now) is expected

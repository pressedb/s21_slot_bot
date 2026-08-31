from collections.abc import Callable
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import CallbackQuery

from s21_slot_bot.app.booking_manager import BookingManager
from s21_slot_bot.app.bot_manager import BotManager
from s21_slot_bot.app.errors import BotRuntimeError, InvalidCallbackDataError
from s21_slot_bot.app.flows.actions import BookFlowAction
from s21_slot_bot.app.flows.book import BookFlow
from s21_slot_bot.app.messenger import Messenger
from s21_slot_bot.app.models import BotInstance, CustomContext
from s21_slot_bot.client.errors import School21Error
from s21_slot_bot.client.models import DryRevieweeBooking


class TestBookFlow:
    async def test_manual_booking(
        self,
        book_flow: BookFlow,
        bot_manager: BotManager,
        booking_manager: BookingManager,
        messenger: Messenger,
        bot_instance_factory: Callable[..., BotInstance],
        dry_reviewee_booking_factory: Callable[..., DryRevieweeBooking],
        query_mock: CallbackQuery,
        context: CustomContext,
    ) -> None:
        inst = bot_instance_factory()
        bot_manager.get_bot = MagicMock(return_value=inst)
        booking_manager._dry_reviewee_bookings["dry-1"] = dry_reviewee_booking_factory(
            booking_id="dry-1",
            project_id=inst.cfg.project_id,
        )
        booking_manager.book = AsyncMock(return_value=True)
        messenger.safe_delete = AsyncMock()
        await book_flow.parse_callback(
            ["dry-1", inst.cfg.bot_id, BookFlowAction.BOOK_ATTEMPT_MANUAL],
            query_mock,
            context,
        )
        booking_manager.book.assert_awaited_once()
        assert inst.stats.attempts_total == 1

    async def test_manual_booking_stops_when_points_exhausted(
        self,
        book_flow: BookFlow,
        bot_manager: BotManager,
        booking_manager: BookingManager,
        messenger: Messenger,
        bot_instance_factory: Callable[..., BotInstance],
        dry_reviewee_booking_factory: Callable[..., DryRevieweeBooking],
        query_mock: CallbackQuery,
        context: CustomContext,
        now: datetime,
    ) -> None:
        inst = bot_instance_factory()
        bot_manager.get_bot = MagicMock(return_value=inst)
        bot_manager.stop_bot = MagicMock()
        booking_manager._dry_reviewee_bookings["dry-1"] = dry_reviewee_booking_factory(
            booking_id="dry-1",
            project_id=inst.cfg.project_id,
        )
        booking_manager.book = AsyncMock(return_value=False)
        messenger.safe_delete = AsyncMock()
        await book_flow.parse_callback(
            ["dry-1", inst.cfg.bot_id, BookFlowAction.BOOK_ATTEMPT_MANUAL], query_mock, context
        )
        bot_manager.stop_bot.assert_called_once()

    async def test_manual_booking_requires_saved_slot(
        self,
        book_flow: BookFlow,
        bot_manager: BotManager,
        bot_instance_factory: Callable[..., BotInstance],
        query_mock: CallbackQuery,
        context: CustomContext,
    ) -> None:
        inst = bot_instance_factory()
        bot_manager.get_bot = MagicMock(return_value=inst)
        with pytest.raises(BotRuntimeError):
            await book_flow.parse_callback(
                ["missing", inst.cfg.bot_id, BookFlowAction.BOOK_ATTEMPT_MANUAL],
                query_mock,
                context,
            )

    async def test_manual_booking_wraps_school21_error(
        self,
        book_flow: BookFlow,
        bot_manager: BotManager,
        booking_manager: BookingManager,
        bot_instance_factory: Callable[..., BotInstance],
        dry_reviewee_booking_factory: Callable[..., DryRevieweeBooking],
        query_mock: CallbackQuery,
        context: CustomContext,
        now: datetime,
    ) -> None:
        inst = bot_instance_factory()
        bot_manager.get_bot = MagicMock(return_value=inst)
        booking_manager._dry_reviewee_bookings["dry-1"] = dry_reviewee_booking_factory(
            booking_id="dry-1",
            project_id=inst.cfg.project_id,
        )
        booking_manager.book = AsyncMock(side_effect=School21Error("boom"))
        with pytest.raises(BotRuntimeError):
            await book_flow.parse_callback(
                ["dry-1", inst.cfg.bot_id, BookFlowAction.BOOK_ATTEMPT_MANUAL],
                query_mock,
                context,
            )

    async def test_unknown_action(
        self,
        book_flow: BookFlow,
        query_mock: CallbackQuery,
        context: CustomContext,
    ) -> None:
        with pytest.raises(InvalidCallbackDataError):
            await book_flow.parse_callback(["bad"], query_mock, context)

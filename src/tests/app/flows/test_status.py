from collections.abc import Callable
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import CallbackQuery, Update

from s21_slot_bot.app.booking_manager import BookingManager
from s21_slot_bot.app.bot_manager import BotManager
from s21_slot_bot.app.errors import BookingRefresherError, InvalidCallbackDataError
from s21_slot_bot.app.flows.actions import StatusFlowAction
from s21_slot_bot.app.flows.status import StatusFlow
from s21_slot_bot.app.messenger import Messenger
from s21_slot_bot.app.models import BotInstance, CustomContext, Lifecycle
from s21_slot_bot.client.models import DryRevieweeBooking


class TestStatusFlow:
    @pytest.mark.parametrize("state", [Lifecycle.RUNNING, Lifecycle.STOPPED, Lifecycle.FAILED])
    async def test_status_refresh(
        self,
        status_flow: StatusFlow,
        booking_manager: BookingManager,
        bot_manager: BotManager,
        messenger: Messenger,
        update_mock: Update,
        context: CustomContext,
        state: Lifecycle,
    ) -> None:
        booking_manager._state = state
        booking_manager.refresh_now = AsyncMock()
        bot_manager.list_all = MagicMock(return_value=[])
        messenger.render_menu_message = AsyncMock()
        await status_flow.status_refresh(update_mock, context)
        booking_manager.refresh_now.assert_awaited_once()
        assert "📌 статус" in messenger.render_menu_message.await_args.args[1]

    async def test_status_refresh_survives_refresher_error(
        self,
        status_flow: StatusFlow,
        booking_manager: BookingManager,
        bot_manager: BotManager,
        messenger: Messenger,
        update_mock: Update,
        context: CustomContext,
    ) -> None:
        booking_manager.refresh_now = AsyncMock(side_effect=BookingRefresherError("boom"))
        bot_manager.list_all = MagicMock(return_value=[])
        messenger.render_menu_message = AsyncMock()
        await status_flow.status_refresh(update_mock, context)
        messenger.render_menu_message.assert_awaited_once()

    @pytest.mark.parametrize(
        ("action", "method"),
        [
            (StatusFlowAction.START_BOOKING_REFRESHER, "start"),
            (StatusFlowAction.STOP_BOOKING_REFRESHER, "stop"),
            (StatusFlowAction.REFRESH, "refresh"),
        ],
    )
    async def test_callbacks(
        self,
        status_flow: StatusFlow,
        booking_manager: BookingManager,
        query_mock: CallbackQuery,
        context: CustomContext,
        action: StatusFlowAction,
        method: str,
    ) -> None:
        booking_manager.start_refreshing = AsyncMock()
        booking_manager.stop_refreshing = MagicMock()
        status_flow.status_refresh = AsyncMock()
        await status_flow.parse_callback([action], query_mock, context)
        if method == "start":
            booking_manager.start_refreshing.assert_awaited_once()
        elif method == "stop":
            booking_manager.stop_refreshing.assert_called_once()
        status_flow.status_refresh.assert_awaited_once()

    async def test_unknown_action(
        self,
        status_flow: StatusFlow,
        query_mock: CallbackQuery,
        context: CustomContext,
    ) -> None:
        with pytest.raises(InvalidCallbackDataError):
            await status_flow.parse_callback(["bad"], query_mock, context)

    def test_status_lines_with_bot_and_bookings(
        self,
        status_flow: StatusFlow,
        bot_manager: BotManager,
        booking_manager: BookingManager,
        bot_instance_factory: Callable[..., BotInstance],
        reviewee_booking_factory,
        context: CustomContext,
        now: datetime,
    ) -> None:
        inst = bot_instance_factory(interval_sec=30, state=Lifecycle.RUNNING)
        inst.stats.last_ping = now
        inst.stats.attempts_total = 5
        inst.stats.attempts_success = 2
        inst.stats.attempts_failed = 1
        inst.stats.currently_booked = 1
        bot_manager._bots = {inst.cfg.bot_id: inst}
        booking_manager._state = Lifecycle.RUNNING
        booking_manager._reviewee_bookings = {
            "b1": reviewee_booking_factory(project_name=inst.cfg.project_name, start=now + timedelta(minutes=30))
        }
        booking_manager._dry_reviewee_bookings = {
            "d1": DryRevieweeBooking(
                id="d1",
                answer_id="a1",
                project_id=inst.cfg.project_id,
                project_name=inst.cfg.project_name,
                start=now + timedelta(minutes=45),
                end=now + timedelta(hours=1),
            )
        }
        context.ensured_chat_data.last_booking_refresh_time = now
        text = "\n".join(status_flow._get_status_lines(context))
        assert inst.cfg.project_name in text
        assert "📝 запись" in text
        assert "🔍 найден слот" in text
        assert "интервал: 30" in text

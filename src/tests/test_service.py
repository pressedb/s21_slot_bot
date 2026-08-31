from unittest.mock import AsyncMock

from telegram.ext import Application

from s21_slot_bot.app.booking_manager import BookingManager
from s21_slot_bot.app.bot_manager import BotManager
from s21_slot_bot.app.flows.collector import FlowCollector
from s21_slot_bot.app.input_handler import InputHandler
from s21_slot_bot.app.messenger import Messenger
from s21_slot_bot.app.models import ChatData
from s21_slot_bot.client.s21_client import School21Client
from s21_slot_bot.config import SlotBotServiceConfig
from s21_slot_bot.service import SlotBotService


class TestSlotBotService:
    def test_component_wiring(
        self,
        service: SlotBotService,
        s21_client: School21Client,
        messenger: Messenger,
        booking_manager: BookingManager,
        bot_manager: BotManager,
        flow_collector: FlowCollector,
        input_handler: InputHandler,
    ) -> None:
        assert service._s21_client is s21_client
        assert service._messenger is messenger
        assert service._booking_manager is booking_manager
        assert service._bot_manager is bot_manager
        assert service._flows is flow_collector
        assert service._input_handler is input_handler

    def test_start(self, service: SlotBotService, tg_app_mock: Application) -> None:
        service.start()
        tg_app_mock.run_polling.assert_called_once_with()

    async def test_post_init(
        self,
        service: SlotBotService,
        s21_client: School21Client,
        booking_manager: BookingManager,
        config: SlotBotServiceConfig,
        tg_app_mock: Application,
    ) -> None:
        s21_client.start = AsyncMock()
        s21_client.get_verifier_bookings = AsyncMock()
        booking_manager.start_refreshing = AsyncMock()
        config.bot.should_refresh_bookings_only_on_active_bots = False
        await service._post_init(tg_app_mock)
        s21_client.start.assert_awaited_once()
        s21_client.get_verifier_bookings.assert_awaited_once()
        booking_manager.start_refreshing.assert_awaited_once()

    async def test_post_init_active_bot_mode(
        self,
        service: SlotBotService,
        s21_client: School21Client,
        booking_manager: BookingManager,
        config: SlotBotServiceConfig,
        tg_app_mock: Application,
    ) -> None:
        s21_client.start = AsyncMock()
        s21_client.get_verifier_bookings = AsyncMock()
        booking_manager.start_refreshing = AsyncMock()
        config.bot.should_refresh_bookings_only_on_active_bots = True
        await service._post_init(tg_app_mock)
        s21_client.get_verifier_bookings.assert_awaited_once()
        booking_manager.start_refreshing.assert_not_awaited()

    async def test_post_stop(
        self,
        service: SlotBotService,
        s21_client: School21Client,
        messenger: Messenger,
        config: SlotBotServiceConfig,
        tg_app_mock: Application,
    ) -> None:
        s21_client.stop = AsyncMock()
        messenger.safe_delete = AsyncMock()
        chat_id = config.bot.tg_chat_id.get_secret_value()
        tg_app_mock.chat_data = {chat_id: ChatData(menu_msg_id=10, menu_error_msg_id=11)}
        await service._post_stop(tg_app_mock)
        assert [c.args[0] for c in messenger.safe_delete.await_args_list] == [11, 10]
        s21_client.stop.assert_awaited_once()

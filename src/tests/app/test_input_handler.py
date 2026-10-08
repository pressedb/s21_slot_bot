from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import telegram
from telegram import CallbackQuery, Update

from s21_slot_bot.app.bot_manager import BotManager
from s21_slot_bot.app.consts import BOOKING_REFRESHER_JOB_NAME
from s21_slot_bot.app.errors import ForbiddenError, InternalError, InvalidCallbackDataError, MenuError
from s21_slot_bot.app.flows.collector import FlowCollector
from s21_slot_bot.app.input_handler import InputHandler
from s21_slot_bot.app.messenger import Messenger
from s21_slot_bot.app.models import CustomContext, FlowCategory, Lifecycle, MenuButton, Screen
from s21_slot_bot.common.error import Error


class TestInputHandler:
    async def test_start(
        self,
        input_handler: InputHandler,
        messenger: Messenger,
        update_mock: Update,
        context: CustomContext,
    ) -> None:
        messenger.start_menu = AsyncMock()
        await input_handler.on_cmd_start(update_mock, context)
        messenger.start_menu.assert_awaited_once()

    async def test_text_routes_menu_button(
        self,
        input_handler: InputHandler,
        messenger: Messenger,
        update_mock: Update,
        context: CustomContext,
    ) -> None:
        update_mock.message.text = MenuButton.STATUS
        method = AsyncMock()
        input_handler._button_to_method[MenuButton.STATUS] = method
        messenger.safe_delete = AsyncMock()
        messenger.render_menu_message = AsyncMock()
        input_handler.on_success = AsyncMock()
        await input_handler.on_text(update_mock, context)
        method.assert_awaited_once_with(update_mock, context)

    async def test_text_routes_screen(
        self,
        input_handler: InputHandler,
        messenger: Messenger,
        update_mock: Update,
        context: CustomContext,
    ) -> None:
        update_mock.message.text = "12:00"
        context.ensured_chat_data.screen = Screen.START_PICK_FROM
        method = AsyncMock()
        input_handler._screen_to_method[Screen.START_PICK_FROM] = method
        messenger.safe_delete = AsyncMock()
        messenger.render_menu_message = AsyncMock()
        input_handler.on_success = AsyncMock()
        await input_handler.on_text(update_mock, context)
        method.assert_awaited_once_with(update_mock, context)

    async def test_text_unknown(
        self,
        input_handler: InputHandler,
        messenger: Messenger,
        update_mock: Update,
        context: CustomContext,
    ) -> None:
        update_mock.message.text = "bad"
        messenger.safe_delete = AsyncMock()
        with pytest.raises(MenuError):
            await input_handler.on_text(update_mock, context)

    async def test_text_shows_processing_when_menu_missing(
        self,
        input_handler: InputHandler,
        messenger: Messenger,
        update_mock: Update,
        context: CustomContext,
    ) -> None:
        update_mock.message.text = MenuButton.STATUS
        method = AsyncMock()
        input_handler._button_to_method[MenuButton.STATUS] = method
        messenger.safe_delete = AsyncMock()
        messenger.render_menu_message = AsyncMock()
        input_handler.on_success = AsyncMock()
        await input_handler.on_text(update_mock, context)
        assert messenger.render_menu_message.await_args.args[1] == "обработка запроса..."

    async def test_callback_routes_flow(
        self,
        input_handler: InputHandler,
        flow_collector: FlowCollector,
        update_mock: Update,
        query_mock: CallbackQuery,
        context: CustomContext,
    ) -> None:
        query_mock.data = f"{FlowCategory.STATUS}:refresh"
        update_mock.message = None
        update_mock.callback_query = query_mock
        flow = MagicMock()
        flow.parse_callback = AsyncMock()
        flow_collector.get_flow = MagicMock(return_value=flow)
        input_handler.on_success = AsyncMock()
        await input_handler.on_callback(update_mock, context)
        flow.parse_callback.assert_awaited_once_with(["refresh"], query_mock, context)

    async def test_callback_requires_query(
        self,
        input_handler: InputHandler,
        update_mock: Update,
        context: CustomContext,
    ) -> None:
        update_mock.message = None
        update_mock.callback_query = None
        with pytest.raises(InternalError):
            await input_handler.on_callback(update_mock, context)

    async def test_invalid_callback(
        self,
        input_handler: InputHandler,
        update_mock: Update,
        query_mock: CallbackQuery,
        context: CustomContext,
    ) -> None:
        query_mock.data = "bad:data"
        update_mock.message = None
        update_mock.callback_query = query_mock
        with pytest.raises(InvalidCallbackDataError):
            await input_handler.on_callback(update_mock, context)

    @pytest.mark.parametrize("use_callback", [False, True])
    def test_validate_access(
        self,
        input_handler: InputHandler,
        update_mock: Update,
        query_mock: CallbackQuery,
        use_callback: bool,
    ) -> None:
        if use_callback:
            update_mock.message = None
            update_mock.callback_query = query_mock
        input_handler._validate_access(update_mock)

        update_mock.effective_user.id += 1
        with pytest.raises(ForbiddenError):
            input_handler._validate_access(update_mock)

    def test_validate_access_requires_message(
        self,
        input_handler: InputHandler,
        update_mock: Update,
    ) -> None:
        update_mock.message = None
        update_mock.callback_query = None
        with pytest.raises(InternalError):
            input_handler._validate_access(update_mock)

    @pytest.mark.parametrize(
        "error",
        [
            (MenuError("bad")),
            (Error("bad")),
            (RuntimeError("bad")),
            (telegram.error.BadRequest("bad")),
        ],
    )
    async def test_error_routing(
        self,
        input_handler: InputHandler,
        messenger: Messenger,
        update_mock: Update,
        context: CustomContext,
        error: Exception,
    ) -> None:
        messenger.render_menu_error = AsyncMock()
        messenger.send = AsyncMock()
        context.error = error
        await input_handler.on_error(update_mock, context)
        if isinstance(error, MenuError):
            messenger.render_menu_error.assert_awaited_once()
        else:
            messenger.send.assert_awaited_once()

    @pytest.mark.parametrize(
        "error",
        [
            (httpx.ConnectError("bad")),
            (telegram.error.NetworkError("bad")),
        ],
    )
    async def test_network_errors_should_not_send_message_and_stop_bot(
        self,
        input_handler: InputHandler,
        messenger: Messenger,
        bot_manager: BotManager,
        update_mock: Update,
        context: CustomContext,
        error: Exception,
    ) -> None:
        messenger.render_menu_error = AsyncMock()
        messenger.send = AsyncMock()
        bot_manager.stop_bot = MagicMock()
        context.error = error

        await input_handler.on_error(update_mock, context)

        messenger.send.assert_not_awaited()
        bot_manager.stop_bot.assert_not_called()

    async def test_not_modified_error_is_ignored(
        self,
        input_handler: InputHandler,
        messenger: Messenger,
        update_mock: Update,
        context: CustomContext,
    ) -> None:
        context.error = telegram.error.BadRequest("Message is not modified")
        messenger.send = AsyncMock()
        await input_handler.on_error(update_mock, context)
        messenger.send.assert_not_awaited()

    async def test_job_error_marks_bot_failed_but_not_refresher(
        self,
        input_handler: InputHandler,
        bot_manager: BotManager,
        messenger: Messenger,
        update_mock: Update,
        context: CustomContext,
    ) -> None:
        context.error = RuntimeError("bad")
        messenger.send = AsyncMock()
        bot_manager.stop_bot = MagicMock()
        context.job = MagicMock()
        context.job.name = "bot-1"
        await input_handler.on_error(update_mock, context)
        assert bot_manager.stop_bot.call_args.kwargs["state"] == Lifecycle.FAILED

        bot_manager.stop_bot.reset_mock()
        context.job.name = BOOKING_REFRESHER_JOB_NAME
        await input_handler.on_error(update_mock, context)
        bot_manager.stop_bot.assert_not_called()

    async def test_success(
        self,
        input_handler: InputHandler,
        messenger: Messenger,
        update_mock: Update,
        context: CustomContext,
    ) -> None:
        context.ensured_chat_data.menu_error_msg_id = 10
        messenger.safe_delete = AsyncMock()
        await input_handler.on_success(update_mock, context)
        assert context.ensured_chat_data.menu_error_msg_id is None

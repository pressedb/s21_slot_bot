from collections.abc import Callable
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import CallbackQuery, Update

from s21_slot_bot.app.bot_manager import BotManager
from s21_slot_bot.app.errors import InternalError, InvalidCallbackDataError, InvalidUserInputError
from s21_slot_bot.app.flows.actions import EditFlowAction, InputFlowAction
from s21_slot_bot.app.flows.edit import EditFlow
from s21_slot_bot.app.messenger import Messenger
from s21_slot_bot.app.models import BotInstance, CustomContext, Lifecycle, Mode, Screen
from s21_slot_bot.client.models import ProjectExtended
from s21_slot_bot.common.logger import LoggerLike


class TestEditFlow:
    def test_get_project(
        self,
        edit_flow: EditFlow,
        bot_manager: BotManager,
        bot_instance_factory: Callable[..., BotInstance],
        project_extended_factory: Callable[..., ProjectExtended],
        context: CustomContext,
    ) -> None:
        with pytest.raises(InternalError):
            edit_flow._get_project(context)

        inst = bot_instance_factory()
        project = project_extended_factory(project_id=inst.cfg.project_id)
        context.ensured_chat_data.edit_bot_id = inst.cfg.bot_id
        context.ensured_chat_data.projects_map = {project.id: project}
        bot_manager.get_bot = MagicMock(return_value=inst)
        assert edit_flow._get_project(context) is project

    def test_prev_action(self, edit_flow: EditFlow, context: CustomContext) -> None:
        assert edit_flow._get_prev_action(EditFlowAction.SHOW_MENU, context) == EditFlowAction.LIST_BOTS
        assert edit_flow._get_prev_action(EditFlowAction.MENU_FROM, context) == EditFlowAction.SHOW_MENU

    async def test_list_bots(
        self,
        edit_flow: EditFlow,
        bot_manager: BotManager,
        messenger: Messenger,
        bot_instance_factory: Callable[..., BotInstance],
        update_mock: Update,
        context: CustomContext,
    ) -> None:
        messenger.render_menu_message = AsyncMock()
        bot_manager.list_all = MagicMock(return_value=[])
        await edit_flow.list_bots(update_mock, context)
        assert "нет ботов" in messenger.render_menu_message.await_args.args[1]

        messenger.render_menu_message.reset_mock()
        bot_manager.list_all = MagicMock(return_value=[bot_instance_factory()])
        await edit_flow.list_bots(update_mock, context)
        assert messenger.render_menu_message.await_args.args[1] == "выбери бота:"

    async def test_edit_menu(
        self,
        edit_flow: EditFlow,
        bot_manager: BotManager,
        messenger: Messenger,
        bot_instance_factory: Callable[..., BotInstance],
        update_mock: Update,
        context: CustomContext,
    ) -> None:
        inst = bot_instance_factory()
        context.ensured_chat_data.edit_bot_id = inst.cfg.bot_id
        bot_manager.get_bot = MagicMock(return_value=inst)
        messenger.render_menu_message = AsyncMock()
        await edit_flow.edit_menu(update_mock, context, update_text="updated")
        text = messenger.render_menu_message.await_args.args[1]
        assert f"#{inst.cfg.bot_id}" in text
        assert "updated" in text
        assert context.ensured_chat_data.screen == Screen.MENU

    @pytest.mark.parametrize(
        ("action", "target"),
        [
            (EditFlowAction.MENU_FROM, "pick_from"),
            (EditFlowAction.MENU_TO, "pick_to"),
            (EditFlowAction.MENU_MODE, "pick_mode"),
            (EditFlowAction.PICK_INTERVAL, "edit_interval"),
            (EditFlowAction.RESTART, "edit_restart"),
        ],
    )
    async def test_callback_menu_routing(
        self,
        edit_flow: EditFlow,
        query_mock: CallbackQuery,
        context: CustomContext,
        action: EditFlowAction,
        target: str,
    ) -> None:
        method = AsyncMock()
        setattr(edit_flow, target, method)
        await edit_flow.parse_callback([action], query_mock, context)
        method.assert_awaited_once()

    async def test_pick_bot(
        self,
        edit_flow: EditFlow,
        query_mock: CallbackQuery,
        context: CustomContext,
    ) -> None:
        edit_flow.edit_menu = AsyncMock()
        await edit_flow.parse_callback(["bot-1", EditFlowAction.PICK_BOT], query_mock, context)
        assert context.ensured_chat_data.edit_bot_id == "bot-1"
        edit_flow.edit_menu.assert_awaited_once()

    async def test_pick_mode_same(
        self,
        edit_flow: EditFlow,
        bot_manager: BotManager,
        bot_instance_factory: Callable[..., BotInstance],
        query_mock: CallbackQuery,
        context: CustomContext,
    ) -> None:
        inst = bot_instance_factory(mode=Mode.FIND_AND_BOOK)
        context.ensured_chat_data.edit_bot_id = inst.cfg.bot_id
        bot_manager.get_bot = MagicMock(return_value=inst)
        edit_flow.edit_menu = AsyncMock()
        await edit_flow.parse_callback([Mode.FIND_AND_BOOK, InputFlowAction.PICK_MODE], query_mock, context)
        edit_flow.edit_menu.assert_awaited_once()

    async def test_pick_mode_only_find(
        self,
        edit_flow: EditFlow,
        bot_manager: BotManager,
        bot_instance_factory: Callable[..., BotInstance],
        query_mock: CallbackQuery,
        context: CustomContext,
    ) -> None:
        inst = bot_instance_factory(mode=Mode.FIND_AND_BOOK, required_reviews=3)
        context.ensured_chat_data.edit_bot_id = inst.cfg.bot_id
        bot_manager.get_bot = MagicMock(return_value=inst)
        edit_flow.edit_menu = AsyncMock()
        await edit_flow.parse_callback([Mode.ONLY_FIND, InputFlowAction.PICK_MODE], query_mock, context)
        assert inst.cfg.mode == Mode.ONLY_FIND
        assert inst.cfg.required_reviews == 1

    async def test_pick_mode_find_and_book(
        self,
        edit_flow: EditFlow,
        bot_manager: BotManager,
        bot_instance_factory: Callable[..., BotInstance],
        query_mock: CallbackQuery,
        context: CustomContext,
    ) -> None:
        inst = bot_instance_factory(mode=Mode.ONLY_FIND)
        context.ensured_chat_data.edit_bot_id = inst.cfg.bot_id
        bot_manager.get_bot = MagicMock(return_value=inst)
        edit_flow.pick_num_reviews = AsyncMock()
        await edit_flow.parse_callback([Mode.FIND_AND_BOOK, InputFlowAction.PICK_MODE], query_mock, context)
        edit_flow.pick_num_reviews.assert_awaited_once()

    @pytest.mark.parametrize("mode", [Mode.ONLY_FIND, Mode.FIND_AND_BOOK])
    async def test_num_reviews_menu(
        self,
        edit_flow: EditFlow,
        bot_manager: BotManager,
        bot_instance_factory: Callable[..., BotInstance],
        query_mock: CallbackQuery,
        context: CustomContext,
        mode: Mode,
    ) -> None:
        inst = bot_instance_factory(mode=mode)
        context.ensured_chat_data.edit_bot_id = inst.cfg.bot_id
        bot_manager.get_bot = MagicMock(return_value=inst)
        edit_flow.edit_menu = AsyncMock()
        edit_flow.pick_num_reviews = AsyncMock()
        await edit_flow.parse_callback([EditFlowAction.MENU_NUM_REVIEWS], query_mock, context)
        if mode == Mode.ONLY_FIND:
            edit_flow.edit_menu.assert_awaited_once()
        else:
            edit_flow.pick_num_reviews.assert_awaited_once()

    @pytest.mark.parametrize(
        "required_originally, is_updated",
        (
            [2, True],
            [3, False],
        ),
    )
    async def test_pick_num_reviews_to_different_number_than_booked(
        self,
        edit_flow: EditFlow,
        bot_manager: BotManager,
        bot_instance_factory: Callable[..., BotInstance],
        project_extended_factory: Callable[..., ProjectExtended],
        query_mock: CallbackQuery,
        context: CustomContext,
        required_originally: int,
        is_updated: bool,
    ) -> None:
        inst = bot_instance_factory(required_reviews=required_originally)
        project = project_extended_factory(project_id=inst.cfg.project_id, booked=0)
        context.ensured_chat_data.edit_bot_id = inst.cfg.bot_id
        context.ensured_chat_data.projects_map = {project.id: project}
        bot_manager.get_bot = MagicMock(return_value=inst)
        edit_flow.edit_menu = AsyncMock()

        await edit_flow.parse_callback(["3", InputFlowAction.PICK_NUM_REVIEWS], query_mock, context)

        assert inst.cfg.required_reviews == 3
        assert ("обновлено" in edit_flow.edit_menu.await_args.kwargs["update_text"]) is is_updated

    async def test_pick_num_reviews_to_same_number_as_booked(
        self,
        edit_flow: EditFlow,
        bot_manager: BotManager,
        bot_instance_factory: Callable[..., BotInstance],
        project_extended_factory: Callable[..., ProjectExtended],
        query_mock: CallbackQuery,
        context: CustomContext,
    ) -> None:
        inst = bot_instance_factory(required_reviews=3)
        project = project_extended_factory(project_id=inst.cfg.project_id, booked=2)
        context.ensured_chat_data.edit_bot_id = inst.cfg.bot_id
        context.ensured_chat_data.projects_map = {project.id: project}
        bot_manager.get_bot = MagicMock(return_value=inst)
        edit_flow.edit_menu = AsyncMock()
        edit_flow.confirm_num_reviews = AsyncMock()

        await edit_flow.parse_callback(["2", InputFlowAction.PICK_NUM_REVIEWS], query_mock, context)

        assert inst.cfg.required_reviews == 3
        assert context.ensured_chat_data.selected_required_reviews == 2
        edit_flow.edit_menu.assert_not_awaited()
        edit_flow.confirm_num_reviews.assert_awaited_once()

    async def test_parse_callback_confirm_num_reviews_updates_reviews(
        self,
        edit_flow: EditFlow,
        bot_manager: BotManager,
        query_mock: CallbackQuery,
        bot_instance_factory: Callable[..., BotInstance],
        context: CustomContext,
    ) -> None:
        inst = bot_instance_factory(required_reviews=2)
        bot_manager.get_bot = MagicMock(return_value=inst)
        context.ensured_chat_data.edit_bot_id = inst.cfg.bot_id
        context.ensured_chat_data.selected_required_reviews = 3
        edit_flow.edit_menu = AsyncMock()

        await edit_flow.parse_callback([InputFlowAction.CONFIRM_NUM_REVIEWS], query_mock, context)

        assert inst.cfg.required_reviews == 3
        edit_flow.edit_menu.assert_awaited_once_with(
            query_mock, context, update_text="✅ количество проверок обновлено"
        )

    async def test_parse_callback_confirm_unchanged_num_reviews(
        self,
        edit_flow: EditFlow,
        bot_manager: BotManager,
        query_mock: CallbackQuery,
        bot_instance_factory: Callable[..., BotInstance],
        context: CustomContext,
    ) -> None:
        inst = bot_instance_factory(required_reviews=2)
        bot_manager.get_bot = MagicMock(return_value=inst)
        context.ensured_chat_data.edit_bot_id = inst.cfg.bot_id
        context.ensured_chat_data.selected_required_reviews = 2

        edit_flow.edit_menu = AsyncMock()

        await edit_flow.parse_callback([InputFlowAction.CONFIRM_NUM_REVIEWS], query_mock, context)

        assert inst.cfg.required_reviews == 2
        edit_flow.edit_menu.assert_awaited_once_with(query_mock, context, update_text="")

    async def test_parse_callback_confirm_num_reviews_without_selected_reviews(
        self,
        edit_flow: EditFlow,
        query_mock: CallbackQuery,
        context: CustomContext,
    ) -> None:
        context.ensured_chat_data.selected_required_reviews = None

        with pytest.raises(InternalError, match="ошибка при подтверждении количества проверок"):
            await edit_flow.parse_callback([InputFlowAction.CONFIRM_NUM_REVIEWS], query_mock, context)

    async def test_set_interval_changed_and_unchanged(
        self,
        edit_flow: EditFlow,
        bot_manager: BotManager,
        bot_instance_factory: Callable[..., BotInstance],
        query_mock: CallbackQuery,
        context: CustomContext,
    ) -> None:
        inst = bot_instance_factory(interval_sec=60)
        context.ensured_chat_data.edit_bot_id = inst.cfg.bot_id
        bot_manager.get_bot = MagicMock(return_value=inst)
        bot_manager.stop_bot = MagicMock()
        bot_manager.start_bot = AsyncMock()
        edit_flow.edit_menu = AsyncMock()
        await edit_flow.parse_callback(["30", EditFlowAction.SET_INTERVAL], query_mock, context)
        assert inst.cfg.interval_sec == 30
        bot_manager.stop_bot.assert_called_once()
        bot_manager.start_bot.assert_awaited_once()

        bot_manager.stop_bot.reset_mock()
        bot_manager.start_bot.reset_mock()
        await edit_flow.parse_callback(["30", EditFlowAction.SET_INTERVAL], query_mock, context)
        bot_manager.stop_bot.assert_not_called()
        bot_manager.start_bot.assert_not_awaited()

    async def test_back(
        self,
        edit_flow: EditFlow,
        query_mock: CallbackQuery,
        context: CustomContext,
    ) -> None:
        edit_flow.list_bots = AsyncMock()
        await edit_flow.parse_callback([EditFlowAction.LIST_BOTS, InputFlowAction.BACK], query_mock, context)
        edit_flow.list_bots.assert_awaited_once()
        with pytest.raises(InvalidCallbackDataError):
            await edit_flow.parse_callback(["missing", InputFlowAction.BACK], query_mock, context)

    async def test_unknown_action(
        self,
        edit_flow: EditFlow,
        query_mock: CallbackQuery,
        context: CustomContext,
    ) -> None:
        with pytest.raises(InvalidCallbackDataError):
            await edit_flow.parse_callback(["bad"], query_mock, context)

    def test_set_from(
        self,
        edit_flow: EditFlow,
        bot_manager: BotManager,
        bot_instance_factory: Callable[..., BotInstance],
        context: CustomContext,
        logger_mock: LoggerLike,
        now: datetime,
    ) -> None:
        inst = bot_instance_factory(from_dt=now, to_dt=now + timedelta(hours=2))
        context.ensured_chat_data.edit_bot_id = inst.cfg.bot_id
        bot_manager.get_bot = MagicMock(return_value=inst)
        with patch("s21_slot_bot.app.flows.edit.datetime") as datetime_mock:
            datetime_mock.now.return_value = now
            edit_flow._set_from("PT30M", context, logger_mock)
        assert inst.cfg.from_dt == now + timedelta(minutes=30)

        with patch("s21_slot_bot.app.flows.edit.datetime") as datetime_mock:
            datetime_mock.now.return_value = now
            with pytest.raises(InvalidUserInputError):
                edit_flow._set_from("PT3H", context, logger_mock)

    def test_set_to(
        self,
        edit_flow: EditFlow,
        bot_manager: BotManager,
        bot_instance_factory: Callable[..., BotInstance],
        context: CustomContext,
        logger_mock: LoggerLike,
        now: datetime,
    ) -> None:
        inst = bot_instance_factory(from_dt=now, to_dt=now + timedelta(hours=2))
        context.ensured_chat_data.edit_bot_id = inst.cfg.bot_id
        bot_manager.get_bot = MagicMock(return_value=inst)
        edit_flow._set_to("PT3H", context, logger_mock)
        assert inst.cfg.to_dt == now + timedelta(hours=3)

        with pytest.raises(InvalidUserInputError):
            edit_flow._set_to("PT0S", context, logger_mock)

    async def test_custom_from_to(
        self,
        edit_flow: EditFlow,
        bot_manager: BotManager,
        bot_instance_factory: Callable[..., BotInstance],
        update_mock: Update,
        context: CustomContext,
        now: datetime,
    ) -> None:
        inst = bot_instance_factory(from_dt=now, to_dt=now + timedelta(hours=5))
        context.ensured_chat_data.edit_bot_id = inst.cfg.bot_id
        bot_manager.get_bot = MagicMock(return_value=inst)
        edit_flow.edit_menu = AsyncMock()

        update_mock.message.text = "PT30M"
        with patch("s21_slot_bot.app.flows.edit.datetime") as datetime_mock:
            datetime_mock.now.return_value = now
            await edit_flow.edit_custom_from(update_mock, context)
        assert inst.cfg.from_dt == now + timedelta(minutes=30)

        update_mock.message.text = "PT2H"
        await edit_flow.edit_custom_to(update_mock, context)
        assert inst.cfg.to_dt == inst.cfg.from_dt + timedelta(hours=2)

    async def test_edit_interval(
        self,
        edit_flow: EditFlow,
        bot_manager: BotManager,
        messenger: Messenger,
        bot_instance_factory: Callable[..., BotInstance],
        update_mock: Update,
        context: CustomContext,
    ) -> None:
        inst = bot_instance_factory()
        context.ensured_chat_data.edit_bot_id = inst.cfg.bot_id
        bot_manager.get_bot = MagicMock(return_value=inst)
        messenger.render_menu_message = AsyncMock()
        await edit_flow.edit_interval(update_mock, context)
        assert context.ensured_chat_data.screen == Screen.EDIT_WAIT_INTERVAL
        assert "интервал" in messenger.render_menu_message.await_args.args[1]

    async def test_custom_interval(
        self,
        edit_flow: EditFlow,
        bot_manager: BotManager,
        bot_instance_factory: Callable[..., BotInstance],
        update_mock: Update,
        context: CustomContext,
    ) -> None:
        inst = bot_instance_factory()
        context.ensured_chat_data.edit_bot_id = inst.cfg.bot_id
        bot_manager.get_bot = MagicMock(return_value=inst)
        bot_manager.stop_bot = MagicMock()
        bot_manager.start_bot = AsyncMock()
        edit_flow.edit_menu = AsyncMock()

        update_mock.message.text = "30"
        await edit_flow.edit_custom_interval(update_mock, context)
        assert inst.cfg.interval_sec == 30
        bot_manager.start_bot.assert_awaited_once()

        update_mock.message.text = "1"
        with pytest.raises(InvalidUserInputError):
            await edit_flow.edit_custom_interval(update_mock, context)

    async def test_restart(
        self,
        edit_flow: EditFlow,
        bot_manager: BotManager,
        bot_instance_factory: Callable[..., BotInstance],
        query_mock: CallbackQuery,
        context: CustomContext,
    ) -> None:
        inst = bot_instance_factory(state=Lifecycle.RUNNING)
        context.ensured_chat_data.edit_bot_id = inst.cfg.bot_id
        bot_manager.get_bot = MagicMock(return_value=inst)
        with pytest.raises(InvalidUserInputError):
            await edit_flow.edit_restart(query_mock, context)

        inst.state = Lifecycle.STOPPED
        bot_manager.start_bot = AsyncMock()
        edit_flow.edit_menu = AsyncMock()
        await edit_flow.edit_restart(query_mock, context)
        bot_manager.start_bot.assert_awaited_once()

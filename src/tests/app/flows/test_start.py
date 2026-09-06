from collections.abc import Callable
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import CallbackQuery, Update

from s21_slot_bot.app.bot_manager import BotManager
from s21_slot_bot.app.errors import InternalError, InvalidCallbackDataError, InvalidUserInputError, MenuError
from s21_slot_bot.app.flows.actions import InputFlowAction, StartFlowAction
from s21_slot_bot.app.flows.start import StartFlow
from s21_slot_bot.app.messenger import Messenger
from s21_slot_bot.app.models import BotInstance, CustomContext, Mode, Screen
from s21_slot_bot.client.errors import School21Error
from s21_slot_bot.client.models import Project, ProjectExtended, ReviewInfo
from s21_slot_bot.client.s21_client import School21Client


class TestStartFlow:
    def test_get_project_requires_selection(self, start_flow: StartFlow, context: CustomContext) -> None:
        with pytest.raises(InternalError):
            start_flow._get_project(context)

    def test_prev_action_special_cases(
        self,
        start_flow: StartFlow,
        project_extended_factory: Callable[..., ProjectExtended],
        context: CustomContext,
    ) -> None:
        project = project_extended_factory()
        context.ensured_chat_data.projects_map = {project.id: project}
        assert start_flow._get_prev_action(InputFlowAction.PICK_MODE, context) is None
        context.ensured_chat_data.start_mode = Mode.ONLY_FIND
        assert start_flow._get_prev_action(InputFlowAction.PICK_FROM, context) == InputFlowAction.PICK_MODE
        assert start_flow._get_prev_action(InputFlowAction.PICK_TO, context) == InputFlowAction.PICK_FROM

    async def test_list_projects_empty(
        self,
        start_flow: StartFlow,
        s21_client: School21Client,
        bot_manager: BotManager,
        messenger: Messenger,
        update_mock: Update,
        context: CustomContext,
    ) -> None:
        bot_manager.check_bot_limits = MagicMock()
        s21_client.get_user_and_student_id = AsyncMock(return_value=("user-1", "student-1"))
        s21_client.get_reviewed_projects = AsyncMock(return_value=[])
        messenger.render_menu_message = AsyncMock()
        await start_flow.list_projects(update_mock, context)
        assert "нет активных проектов" in messenger.render_menu_message.await_args.args[1]

    async def test_list_projects_single_auto_selects(
        self,
        start_flow: StartFlow,
        s21_client: School21Client,
        bot_manager: BotManager,
        project_factory: Callable[..., Project],
        update_mock: Update,
        context: CustomContext,
    ) -> None:
        project = project_factory()
        bot_manager.check_bot_limits = MagicMock()
        s21_client.get_user_and_student_id = AsyncMock(return_value=("u", "s"))
        s21_client.get_reviewed_projects = AsyncMock(return_value=[project])
        s21_client.get_review_info = AsyncMock(return_value=ReviewInfo(required=3, booked=1))
        start_flow.pick_mode = AsyncMock()
        await start_flow.list_projects(update_mock, context)
        assert context.ensured_chat_data.start_project_id == project.id
        start_flow.pick_mode.assert_awaited_once()

    async def test_list_projects_multiple(
        self,
        start_flow: StartFlow,
        s21_client: School21Client,
        bot_manager: BotManager,
        messenger: Messenger,
        project_factory: Callable[..., Project],
        update_mock: Update,
        context: CustomContext,
    ) -> None:
        projects = [project_factory(project_id="p1"), project_factory(project_id="p2")]
        bot_manager.check_bot_limits = MagicMock()
        s21_client.get_user_and_student_id = AsyncMock(return_value=("u", "s"))
        s21_client.get_reviewed_projects = AsyncMock(return_value=projects)
        s21_client.get_review_info = AsyncMock(
            side_effect=[ReviewInfo(required=2, booked=0), ReviewInfo(required=3, booked=1)]
        )
        messenger.render_menu_message = AsyncMock()
        await start_flow.list_projects(update_mock, context)
        assert set(context.ensured_chat_data.projects_map) == {"p1", "p2"}
        assert messenger.render_menu_message.await_args.args[1] == "выбери проект:"

    async def test_list_projects_rejects_project_without_id(
        self,
        start_flow: StartFlow,
        s21_client: School21Client,
        bot_manager: BotManager,
        project_factory: Callable[..., Project],
        update_mock: Update,
        context: CustomContext,
    ) -> None:
        project = project_factory()
        project.id = None
        bot_manager.check_bot_limits = MagicMock()
        s21_client.get_user_and_student_id = AsyncMock(return_value=("u", "s"))
        s21_client.get_reviewed_projects = AsyncMock(return_value=[project])
        with pytest.raises(MenuError):
            await start_flow.list_projects(update_mock, context)

    async def test_list_projects_wraps_school21_error(
        self,
        start_flow: StartFlow,
        s21_client: School21Client,
        bot_manager: BotManager,
        update_mock: Update,
        context: CustomContext,
    ) -> None:
        bot_manager.check_bot_limits = MagicMock()
        s21_client.get_user_and_student_id = AsyncMock(side_effect=School21Error("boom"))
        with pytest.raises(MenuError):
            await start_flow.list_projects(update_mock, context)

    async def test_pick_mode_renders_and_sets_screen(
        self,
        start_flow: StartFlow,
        messenger: Messenger,
        project_extended_factory: Callable[..., ProjectExtended],
        update_mock: Update,
        context: CustomContext,
    ) -> None:
        project = project_extended_factory()
        context.ensured_chat_data.projects_map = {project.id: project}
        context.ensured_chat_data.start_project_id = project.id
        messenger.render_menu_message = AsyncMock()
        await start_flow.pick_mode(update_mock, context)
        assert "выбери режим" in messenger.render_menu_message.await_args.args[1]

    async def test_pick_num_reviews_renders(
        self,
        start_flow: StartFlow,
        messenger: Messenger,
        project_extended_factory: Callable[..., ProjectExtended],
        update_mock: Update,
        context: CustomContext,
    ) -> None:
        project = project_extended_factory(required=3, booked=1)
        context.ensured_chat_data.projects_map = {project.id: project}
        context.ensured_chat_data.start_project_id = project.id
        context.ensured_chat_data.start_mode = Mode.FIND_AND_BOOK
        messenger.render_menu_message = AsyncMock()
        await start_flow.pick_num_reviews(update_mock, context)
        assert "количество проверок" in messenger.render_menu_message.await_args.args[1]

    @pytest.mark.parametrize(
        ("method_name", "screen"),
        [
            ("pick_from", Screen.START_PICK_FROM),
            ("pick_to", Screen.START_PICK_TO),
        ],
    )
    async def test_time_picker_renders(
        self,
        start_flow: StartFlow,
        messenger: Messenger,
        project_extended_factory: Callable[..., ProjectExtended],
        update_mock: Update,
        context: CustomContext,
        method_name: str,
        screen: Screen,
        now: datetime,
    ) -> None:
        project = project_extended_factory()
        context.ensured_chat_data.projects_map = {project.id: project}
        context.ensured_chat_data.start_project_id = project.id
        context.ensured_chat_data.start_mode = Mode.FIND_AND_BOOK
        context.ensured_chat_data.start_required_reviews = 2
        context.ensured_chat_data.start_from = now
        messenger.render_menu_message = AsyncMock()
        await getattr(start_flow, method_name)(update_mock, context)
        assert context.ensured_chat_data.screen == screen
        messenger.render_menu_message.assert_awaited_once()

    async def test_parse_callback_mode_branches(
        self,
        start_flow: StartFlow,
        query_mock: CallbackQuery,
        context: CustomContext,
    ) -> None:
        start_flow.pick_from = AsyncMock()
        await start_flow.parse_callback([Mode.ONLY_FIND, InputFlowAction.PICK_MODE], query_mock, context)
        assert context.ensured_chat_data.start_mode == Mode.ONLY_FIND
        assert context.ensured_chat_data.start_required_reviews == 1
        start_flow.pick_from.assert_awaited_once()

        start_flow.pick_num_reviews = AsyncMock()
        await start_flow.parse_callback([Mode.FIND_AND_BOOK, InputFlowAction.PICK_MODE], query_mock, context)
        start_flow.pick_num_reviews.assert_awaited_once()

    async def test_parse_callback_project_reviews_and_times(
        self,
        start_flow: StartFlow,
        query_mock: CallbackQuery,
        context: CustomContext,
        now: datetime,
    ) -> None:
        start_flow.pick_mode = AsyncMock()
        await start_flow.parse_callback(["p1", StartFlowAction.PICK_PROJECT], query_mock, context)
        assert context.ensured_chat_data.start_project_id == "p1"

        start_flow.pick_from = AsyncMock()
        await start_flow.parse_callback(["2", InputFlowAction.PICK_NUM_REVIEWS], query_mock, context)
        assert context.ensured_chat_data.start_required_reviews == 2

        start_flow.pick_to = AsyncMock()
        with patch("s21_slot_bot.app.flows.start.datetime") as datetime_mock:
            datetime_mock.now.return_value = now
            await start_flow.parse_callback(["PT30M", InputFlowAction.PICK_FROM], query_mock, context)
        assert context.ensured_chat_data.start_from == now + timedelta(minutes=30)

        start_flow.confirm = AsyncMock()
        await start_flow.parse_callback(["PT2H", InputFlowAction.PICK_TO], query_mock, context)
        assert context.ensured_chat_data.start_to == context.ensured_chat_data.start_from + timedelta(hours=2)

    async def test_parse_callback_to_requires_from(
        self,
        start_flow: StartFlow,
        query_mock: CallbackQuery,
        context: CustomContext,
    ) -> None:
        with pytest.raises(InternalError):
            await start_flow.parse_callback(["PT2H", InputFlowAction.PICK_TO], query_mock, context)

    async def test_back_and_invalid_actions(
        self,
        start_flow: StartFlow,
        query_mock: CallbackQuery,
        context: CustomContext,
    ) -> None:
        start_flow.pick_from = AsyncMock()
        await start_flow.parse_callback([InputFlowAction.PICK_FROM, InputFlowAction.BACK], query_mock, context)
        start_flow.pick_from.assert_awaited_once()

        with pytest.raises(InvalidCallbackDataError):
            await start_flow.parse_callback(["missing", InputFlowAction.BACK], query_mock, context)
        with pytest.raises(InvalidCallbackDataError):
            await start_flow.parse_callback(["bad"], query_mock, context)

    async def test_custom_from_and_to(
        self,
        start_flow: StartFlow,
        update_mock: Update,
        context: CustomContext,
        now: datetime,
    ) -> None:
        update_mock.message.text = "PT30M"
        start_flow.pick_to = AsyncMock()
        with patch("s21_slot_bot.app.flows.start.datetime") as datetime_mock:
            datetime_mock.now.return_value = now
            await start_flow.custom_from(update_mock, context)
        assert context.ensured_chat_data.start_from == now + timedelta(minutes=30)

        update_mock.message.text = "PT2H"
        start_flow.confirm = AsyncMock()
        await start_flow.custom_to(update_mock, context)
        assert context.ensured_chat_data.start_to == context.ensured_chat_data.start_from + timedelta(hours=2)

    async def test_custom_to_validation(
        self,
        start_flow: StartFlow,
        update_mock: Update,
        context: CustomContext,
        now: datetime,
    ) -> None:
        update_mock.message.text = "PT2H"
        with pytest.raises(InternalError):
            await start_flow.custom_to(update_mock, context)

        context.ensured_chat_data.start_from = now
        update_mock.message.text = "PT0S"
        with pytest.raises(InvalidUserInputError):
            await start_flow.custom_to(update_mock, context)

    async def test_confirm(
        self,
        start_flow: StartFlow,
        bot_manager: BotManager,
        messenger: Messenger,
        project_extended_factory: Callable[..., ProjectExtended],
        update_mock: Update,
        context: CustomContext,
        now: datetime,
    ) -> None:
        project = project_extended_factory()
        context.ensured_chat_data.projects_map = {project.id: project}
        context.ensured_chat_data.start_project_id = project.id
        context.ensured_chat_data.start_mode = Mode.FIND_AND_BOOK
        context.ensured_chat_data.start_required_reviews = 2
        context.ensured_chat_data.start_from = now
        context.ensured_chat_data.start_to = now + timedelta(hours=2)
        bot_manager.list_all = MagicMock(return_value=[])
        messenger.render_menu_message = AsyncMock()
        await start_flow.confirm(update_mock, context)
        assert "всего ботов" in messenger.render_menu_message.await_args.args[1]

    async def test_finalize(
        self,
        start_flow: StartFlow,
        bot_manager: BotManager,
        messenger: Messenger,
        project_extended_factory: Callable[..., ProjectExtended],
        update_mock: Update,
        context: CustomContext,
        now: datetime,
    ) -> None:
        project = project_extended_factory()
        data = context.ensured_chat_data
        data.projects_map = {project.id: project}
        data.start_project_id = project.id
        data.start_required_reviews = 2
        data.start_from = now
        data.start_to = now + timedelta(hours=2)
        data.start_mode = Mode.FIND_AND_BOOK
        bot_manager.check_bot_limits = MagicMock()
        bot_manager.start_bot = AsyncMock()
        messenger.render_menu_message = AsyncMock()
        with patch("s21_slot_bot.app.flows.start.random_id", return_value="bot-1"):
            await start_flow.finalize(update_mock, context)
        inst = bot_manager.start_bot.await_args.args[0]
        assert isinstance(inst, BotInstance)
        assert inst.cfg.bot_id == "bot-1"

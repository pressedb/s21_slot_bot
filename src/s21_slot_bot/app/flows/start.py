from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any, cast, override

from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from s21_slot_bot.app.errors import InternalError, InvalidCallbackDataError, InvalidUserInputError, MenuError
from s21_slot_bot.app.flows.actions import FlowAction, InputFlowAction, StartFlowAction
from s21_slot_bot.app.flows.base import CustomInputFlow
from s21_slot_bot.app.models import (
    BotInstance,
    CustomContext,
    Mode,
    RequiredReviewsAdapter,
    Screen,
    SearchConfig,
)
from s21_slot_bot.app.utils import get_message_text, get_tzinfo
from s21_slot_bot.client.consts import MIN_REQUIRED_REVIEWS
from s21_slot_bot.client.errors import School21Error
from s21_slot_bot.client.models import ProjectExtended
from s21_slot_bot.common import markdown
from s21_slot_bot.common.id import random_id
from s21_slot_bot.common.logger import get_user_input_logger
from s21_slot_bot.common.strings import ensure_str
from s21_slot_bot.common.time import dt_to_pretty, parse_to_datetime


class StartFlow(CustomInputFlow):
    @property
    def _action_to_method(
        self,
    ) -> dict[FlowAction, Callable[[Update | CallbackQuery, CustomContext], Coroutine[Any, Any, None]]]:
        return {
            StartFlowAction.LIST_PROJECTS: self.list_projects,
            InputFlowAction.PICK_MODE: self.pick_mode,
            InputFlowAction.PICK_NUM_REVIEWS: self.pick_num_reviews,
            InputFlowAction.PICK_FROM: self.pick_from,
            InputFlowAction.PICK_TO: self.pick_to,
            StartFlowAction.CONFIRM: self.confirm,
            StartFlowAction.FINALIZE: self.finalize,
        }

    @override
    @property
    def _action_to_screen(self) -> dict[FlowAction, Screen]:
        return {InputFlowAction.PICK_FROM: Screen.START_PICK_FROM, InputFlowAction.PICK_TO: Screen.START_PICK_TO}

    @property
    def _ordered_actions(self) -> list[FlowAction]:
        return list(self._action_to_method.keys())

    @override
    async def parse_callback(self, callback_data: list[str], query: CallbackQuery, context: CustomContext) -> None:
        logger = get_user_input_logger(query)
        tz = get_tzinfo(context)
        action = callback_data.pop()
        match action:
            case StartFlowAction.PICK_PROJECT:
                proj_id = callback_data.pop()
                context.ensured_chat_data.start_project_id = proj_id
                await self.pick_mode(query, context)
            case InputFlowAction.PICK_MODE:
                mode = Mode(callback_data.pop())
                context.ensured_chat_data.start_mode = mode
                match mode:
                    case Mode.ONLY_FIND:
                        context.ensured_chat_data.start_required_reviews = MIN_REQUIRED_REVIEWS
                        await self.pick_from(query, context)
                    case Mode.FIND_AND_BOOK:
                        await self.pick_num_reviews(query, context)
            case InputFlowAction.PICK_NUM_REVIEWS:
                num_reviews = RequiredReviewsAdapter.validate_strings(callback_data.pop())
                context.ensured_chat_data.start_required_reviews = num_reviews
                await self.pick_from(query, context)
            case InputFlowAction.PICK_FROM:
                now = datetime.now(tz=tz)
                from_choice = parse_to_datetime(callback_data.pop(), tz, now, logger)
                context.ensured_chat_data.start_from = from_choice
                await self.pick_to(query, context)
            case InputFlowAction.PICK_TO:
                from_dt = context.ensured_chat_data.start_from
                if not from_dt:
                    raise InternalError(
                        "начальное время поиска не задано", location=context.ensured_chat_data.model_dump()
                    )
                to_choice = parse_to_datetime(callback_data.pop(), tz, from_dt, logger)
                context.ensured_chat_data.start_to = to_choice
                await self.confirm(query, context)
            case StartFlowAction.CONFIRM:
                await self.finalize(query, context)
            case StartFlowAction.FINALIZE:
                return
            case InputFlowAction.BACK:
                prev_action = cast(FlowAction, callback_data.pop())
                prev_method = self._action_to_method.get(prev_action)
                if not prev_method:
                    raise InvalidCallbackDataError("предыдущее действие не задано")
                await prev_method(query, context)
            case _:
                raise InvalidCallbackDataError(f"неподдерживаемое действие '{action}' при настройке поиска")

    async def list_projects(self, user_input: Update | CallbackQuery, context: CustomContext) -> None:
        logger = get_user_input_logger(user_input)
        logger.info("Listing projects...")
        self._bot_manager.check_bot_limits()
        action = StartFlowAction.PICK_PROJECT
        self._set_screen(action, context)
        try:
            projects_extended = await self._s21_client.get_all_reviewed_projects_with_review_info(logger)
            if not projects_extended:
                await self._messenger.render_menu_message(context, "📭 нет активных проектов на проверке", logger)
                return
        except School21Error as e:
            raise MenuError(f"не удалось получить проекты: {e.message}") from e
        context.ensured_chat_data.projects_map = {project.id: project for project in projects_extended}
        if len(projects_extended) == 1:
            project = projects_extended[0]
            context.ensured_chat_data.start_project_id = project.id
            await self.pick_mode(user_input, context)
            return
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"{project.name} ({project.review_info.booked} / {project.review_info.required} проверок)",
                        callback_data=f"{self._category}:{action}:{project.id}",
                    )
                ]
                for project in projects_extended[:20]
            ]
        )
        await self._messenger.render_menu_message(context, "выбери проект:", logger, kb=kb)

    async def custom_from(self, update: Update, context: CustomContext) -> None:
        logger = get_user_input_logger(update)
        logger.info("Parsing custom search start time...")
        tz = get_tzinfo(context)
        now = datetime.now(tz=tz)
        text = get_message_text(update)
        start_from = parse_to_datetime(text, tz, now, logger)
        context.ensured_chat_data.start_from = start_from
        await self.pick_to(update, context)

    async def custom_to(self, update: Update, context: CustomContext) -> None:
        logger = get_user_input_logger(update)
        logger.info("Parsing custom search end time...")
        start_from = context.ensured_chat_data.start_from
        tz = get_tzinfo(context)
        if not start_from:
            raise InternalError("начальное время поиска не задано", location=context.ensured_chat_data.model_dump())
        text = get_message_text(update)
        start_to = parse_to_datetime(text, tz, start_from, logger)
        if start_to <= start_from:
            raise InvalidUserInputError(
                f"конечное время должно быть позже начального ({dt_to_pretty(start_to, tz=tz)})"
            )
        context.ensured_chat_data.start_to = start_to
        await self.confirm(update, context)

    async def confirm(self, user_input: Update | CallbackQuery, context: CustomContext) -> None:
        logger = get_user_input_logger(user_input)
        logger.info("Confirming the chosen bot search parameters...")
        action = StartFlowAction.CONFIRM
        self._set_screen(action, context)
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🚀 старт", callback_data=f"{self._category}:{action}")],
                [
                    InlineKeyboardButton(
                        "⏪ Назад",
                        callback_data=f"{self._category}:{InputFlowAction.BACK}:{InputFlowAction.PICK_TO}",
                    )
                ],
            ]
        )
        num_total_bots = len(self._bot_manager.list_all())
        text = (
            self._get_chosen_project_info_text(context, action, is_markdown=True)
            + f"всего ботов: {num_total_bots} / максимум {self._bot_manager.max_bots}"
        )
        await self._messenger.render_menu_message(context, text, logger, kb=kb, parse_mode=ParseMode.MARKDOWN_V2)

    async def finalize(self, user_input: Update | CallbackQuery, context: CustomContext) -> None:
        logger = get_user_input_logger(user_input)
        logger.info("Finalizing the chosen bot search parameters...")
        self._bot_manager.check_bot_limits()
        action = StartFlowAction.CONFIRM
        self._set_screen(action, context)
        text = self._get_chosen_project_info_text(context, action, is_markdown=True)
        project = self._get_project(context)

        bot_id = random_id()
        cfg = SearchConfig(
            bot_id=bot_id,
            project_id=project.id,
            project_name=project.name,
            required_reviews=context.ensured_chat_data.start_required_reviews,
            from_dt=context.ensured_chat_data.start_from,
            to_dt=context.ensured_chat_data.start_to,
            interval_sec=self._bot_manager.poll_interval_sec,
            mode=context.ensured_chat_data.start_mode,
        )

        inst = BotInstance(cfg=cfg)
        text += f"✅ Запускаю бота #{bot_id}"
        await self._messenger.render_menu_message(context, text, logger, parse_mode=ParseMode.MARKDOWN_V2)
        await self._bot_manager.start_bot(inst, context, logger)

    @override
    def _get_project(self, context: CustomContext) -> ProjectExtended:
        start_project_id = context.ensured_chat_data.start_project_id
        if not start_project_id or not (project := context.ensured_chat_data.projects_map.get(start_project_id)):
            raise InternalError("проект не найден")
        return project

    @override
    def _get_prev_action(self, action: FlowAction, context: CustomContext) -> FlowAction | None:
        match action:
            case InputFlowAction.PICK_MODE if len(context.ensured_chat_data.projects_map) == 1:
                return None
            case InputFlowAction.PICK_FROM if context.ensured_chat_data.start_mode == Mode.ONLY_FIND:
                return InputFlowAction.PICK_MODE
            case _:
                cur_idx = self._get_action_idx(action)
                prev_action = self._ordered_actions[cur_idx - 1] if 1 <= cur_idx < len(self._ordered_actions) else None
                return prev_action

    def _get_action_idx(self, action: FlowAction) -> int:
        idx = self._ordered_actions.index(action)
        return idx

    @override
    def _get_chosen_project_info_text(
        self, context: CustomContext, action: FlowAction | None = None, is_markdown: bool = False
    ) -> str:
        project = self._get_project(context)
        project_name = ensure_str(
            project, getter=lambda proj: markdown.backtick_wrap(proj.name) if is_markdown else proj.name
        )
        currently_booked = ensure_str(project, getter=lambda proj: proj.review_info.booked)
        tz = get_tzinfo(context)
        lines = [
            f"проект: {project_name} (ID {ensure_str(context.ensured_chat_data.start_project_id)})",
            f"режим: {ensure_str(context.ensured_chat_data.start_mode, getter=lambda mode: ' '.join(mode.to_emoji_text()))}",
            f"количество проверок: {currently_booked}/{ensure_str(context.ensured_chat_data.start_required_reviews)}",
            f"начало поиска: {ensure_str(context.ensured_chat_data.start_from, getter=dt_to_pretty, tz=tz)}",
            f"конец поиска: {ensure_str(context.ensured_chat_data.start_to, getter=dt_to_pretty, tz=tz)}",
        ]
        line_idx = self._get_action_idx(action) if action else len(lines)
        project_info_text = "\n".join(lines[:line_idx]) + "\n\n"
        return project_info_text

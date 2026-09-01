from abc import ABC, abstractmethod

from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from s21_slot_bot.app.booking_manager import BookingManager
from s21_slot_bot.app.bot_manager import BotManager
from s21_slot_bot.app.consts import PYDANTIC_DATETIME_DOCS_URL
from s21_slot_bot.app.flows.actions import FlowAction, InputFlowAction
from s21_slot_bot.app.messenger import Messenger
from s21_slot_bot.app.models import CustomContext, FlowCategory, Mode, Screen
from s21_slot_bot.client.consts import MIN_REQUIRED_REVIEWS
from s21_slot_bot.client.models import ProjectExtended
from s21_slot_bot.client.s21_client import School21Client
from s21_slot_bot.common import markdown
from s21_slot_bot.common.logger import get_user_input_logger


class Flow(ABC):
    def __init__(
        self,
        s21_client: School21Client,
        bot_manager: BotManager,
        booking_manager: BookingManager,
        messenger: Messenger,
        category: FlowCategory,
    ):
        self._s21_client = s21_client
        self._bot_manager = bot_manager
        self._booking_manager = booking_manager
        self._messenger = messenger
        self._category = category

    @abstractmethod
    async def parse_callback(self, callback_data: list[str], query: CallbackQuery, context: CustomContext) -> None:
        raise NotImplementedError


class CustomInputFlow(Flow, ABC):
    @property
    @abstractmethod
    def _action_to_screen(self) -> dict[FlowAction, Screen]:
        raise NotImplementedError

    async def pick_mode(
        self,
        user_input: Update | CallbackQuery,
        context: CustomContext,
    ) -> None:
        logger = get_user_input_logger(user_input)
        logger.info("Picking mode in category `%s`...", self._category)
        action = InputFlowAction.PICK_MODE
        prev_action = self._get_prev_action(action, context)
        self._set_screen(action, context)
        buttons = [
            [
                InlineKeyboardButton(
                    "🔎 Искать слоты",
                    callback_data=f"{self._category}:{action}:{Mode.ONLY_FIND}",
                )
            ],
            [
                InlineKeyboardButton(
                    "✅ Записаться",
                    callback_data=f"{self._category}:{action}:{Mode.FIND_AND_BOOK}",
                )
            ],
        ]
        if prev_action:
            buttons.append(
                [
                    InlineKeyboardButton(
                        "⏪ Назад",
                        callback_data=f"{self._category}:{InputFlowAction.BACK}:{prev_action}",
                    )
                ]
            )
        kb = InlineKeyboardMarkup(buttons)
        text = self._get_chosen_project_info_text(context, action, is_markdown=True) + "выбери режим:"
        await self._messenger.render_menu_message(context, text, logger, kb=kb, parse_mode=ParseMode.MARKDOWN_V2)

    async def pick_num_reviews(
        self,
        user_input: Update | CallbackQuery,
        context: CustomContext,
    ) -> None:
        logger = get_user_input_logger(user_input)
        logger.info("Picking number of reviews in category `%s`...", self._category)
        action = InputFlowAction.PICK_NUM_REVIEWS
        prev_action = self._get_prev_action(action, context)
        project = self._get_project(context)
        self._set_screen(action, context)
        buttons = [
            [
                InlineKeyboardButton(str(num), callback_data=f"{self._category}:{action}:{num}")
                for num in range(MIN_REQUIRED_REVIEWS, project.review_info.required + 1)
            ],
        ]
        if prev_action:
            buttons.append(
                [
                    InlineKeyboardButton(
                        "⏪ Назад",
                        callback_data=f"{self._category}:{InputFlowAction.BACK}:{prev_action}",
                    )
                ],
            )
        kb = InlineKeyboardMarkup(buttons)
        text = (
            self._get_chosen_project_info_text(context, action, is_markdown=True)
            + f"выбери количество проверок (текущих {project.review_info.booked}): "
        )
        await self._messenger.render_menu_message(context, text, logger, kb=kb, parse_mode=ParseMode.MARKDOWN_V2)

    async def pick_from(
        self,
        user_input: Update | CallbackQuery,
        context: CustomContext,
    ) -> None:
        logger = get_user_input_logger(user_input)
        logger.info("Picking search start time in category `%s`...", self._category)
        action = InputFlowAction.PICK_FROM
        prev_action = self._get_prev_action(action, context)
        self._set_screen(action, context)
        buttons = [
            [
                InlineKeyboardButton("сейчас", callback_data=f"{self._category}:{action}:PT0S"),
                InlineKeyboardButton("+30м", callback_data=f"{self._category}:{action}:PT30M"),
                InlineKeyboardButton("+1ч", callback_data=f"{self._category}:{action}:PT1H"),
            ],
        ]
        if prev_action:
            buttons.append(
                [
                    InlineKeyboardButton(
                        "⏪ Назад",
                        callback_data=f"{self._category}:{InputFlowAction.BACK}:{prev_action}",
                    )
                ],
            )
        kb = InlineKeyboardMarkup(buttons)
        link = markdown.format_inline_link("поддерживаемые строковые форматы", PYDANTIC_DATETIME_DOCS_URL)
        text = (
            self._get_chosen_project_info_text(context, action, is_markdown=True) + "выбери начальное время поиска\n"
            f"(или введи вручную в формате [YYYY-MM-DD] HH:MM[:SS] - {link}):"
        )
        await self._messenger.render_menu_message(context, text, logger, kb=kb, parse_mode=ParseMode.MARKDOWN_V2)

    async def pick_to(
        self,
        user_input: Update | CallbackQuery,
        context: CustomContext,
    ) -> None:
        logger = get_user_input_logger(user_input)
        logger.info("Picking search end time in category `%s`...", self._category)
        action = InputFlowAction.PICK_TO
        prev_action = self._get_prev_action(action, context)
        self._set_screen(action, context)
        buttons = [
            [
                InlineKeyboardButton(f"+{hour}ч", callback_data=f"{self._category}:{action}:PT{hour}H")
                for hour in [1, 2, 4, 8, 12]
            ],
        ]
        if prev_action:
            buttons.append(
                [
                    InlineKeyboardButton(
                        "⏪ Назад",
                        callback_data=f"{self._category}:{InputFlowAction.BACK}:{prev_action}",
                    )
                ],
            )
        kb = InlineKeyboardMarkup(buttons)
        text = (
            self._get_chosen_project_info_text(context, action, is_markdown=True)
            + "выбери конечное время поиска относительно начала\n"
            "(или введи вручную в формате [YYYY-MM-DD] HH:MM[:SS] - "
            f"[поддерживаемые строковые форматы]({PYDANTIC_DATETIME_DOCS_URL})):"
        )
        await self._messenger.render_menu_message(context, text, logger, kb=kb, parse_mode=ParseMode.MARKDOWN_V2)

    def _set_screen(self, action: FlowAction, context: CustomContext) -> None:
        screen = self._action_to_screen.get(action) or Screen.MENU
        context.ensured_chat_data.screen = screen

    @abstractmethod
    def _get_project(self, context: CustomContext) -> ProjectExtended:
        raise NotImplementedError

    @abstractmethod
    def _get_prev_action(self, action: FlowAction, context: CustomContext) -> FlowAction | None:
        raise NotImplementedError

    @abstractmethod
    def _get_chosen_project_info_text(
        self, context: CustomContext, action: FlowAction | None = None, is_markdown: bool = False
    ) -> str:
        raise NotImplementedError

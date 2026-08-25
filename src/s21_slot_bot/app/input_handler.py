import httpx
import telegram
from pydantic import ValidationError
from telegram import Message, Update

from s21_slot_bot.app.bot_manager import BotManager
from s21_slot_bot.app.consts import BOOKING_REFRESHER_JOB_NAME
from s21_slot_bot.app.errors import (
    ForbiddenError,
    InternalError,
    InvalidCallbackDataError,
    MenuError,
    is_not_modified_tg_error,
)
from s21_slot_bot.app.flows.collector import FlowCollector
from s21_slot_bot.app.messenger import Messenger
from s21_slot_bot.app.models import CustomContext, FlowCategory, Lifecycle, MenuButton, Screen
from s21_slot_bot.app.utils import get_message_text
from s21_slot_bot.common.error import Error, get_error_description
from s21_slot_bot.common.logger import get_user_input_logger


class InputHandler:
    def __init__(
        self,
        bot_manager: BotManager,
        messenger: Messenger,
        flows: FlowCollector,
        chat_id: int,
    ):
        self._bot_manager = bot_manager
        self._messenger = messenger
        self._flows = flows
        self._chat_id = chat_id
        self._button_to_method = {
            MenuButton.START: self._flows.start.list_projects,
            MenuButton.STOP: self._flows.stop.stop_menu,
            MenuButton.DELETE: self._flows.delete.delete_menu,
            MenuButton.EDIT: self._flows.edit.list_bots,
            MenuButton.STATUS: self._flows.status.status_refresh,
        }
        self._screen_to_method = {
            Screen.START_PICK_FROM: self._flows.start.custom_from,
            Screen.START_PICK_TO: self._flows.start.custom_to,
            Screen.EDIT_WAIT_FROM: self._flows.edit.edit_custom_from,
            Screen.EDIT_WAIT_TO: self._flows.edit.edit_custom_to,
            Screen.EDIT_WAIT_INTERVAL: self._flows.edit.edit_custom_interval,
        }

    async def on_cmd_start(self, update: Update, _: CustomContext) -> None:
        logger = get_user_input_logger(update)
        logger.info("Processing /start")
        self._validate_access(update)
        await self._messenger.start_menu(update, logger)

    async def on_text(self, update: Update, context: CustomContext) -> None:
        self._validate_access(update)
        text = get_message_text(update)
        screen = context.ensured_chat_data.screen
        logger = get_user_input_logger(update)
        logger.info("Handling text `%s` with screen `%s`", text, screen)

        if update.message:
            await self._messenger.safe_delete(update.message.message_id, logger)
        button_method = text in MenuButton and self._button_to_method.get(MenuButton(text))
        screen_method = self._screen_to_method.get(screen)
        method = button_method or screen_method
        if not method:
            raise MenuError("выбери действие в меню")

        if not context.ensured_chat_data.menu_msg_id:
            await self._messenger.render_menu_message(context, "обработка запроса...", logger)
        await method(update, context)
        await self.on_success(update, context)

    async def on_callback(self, update: Update, context: CustomContext) -> None:
        self._validate_access(update)
        logger = get_user_input_logger(update)
        query = update.callback_query
        if not query:
            raise InternalError("не удалось обработать команду")
        await query.answer()
        data = query.data or ""
        logger.info("Processing callback `%s`", data)

        callback_data = data.split(":")
        callback_data.reverse()
        try:
            category = FlowCategory(callback_data.pop())
            flow = self._flows.get_flow(category)
            await flow.parse_callback(callback_data, query, context)
            await self.on_success(update, context)
        # TODO: catch errors in flows and reraise them as only InvalidCallbackDataError?
        except (IndexError, ValueError, ValidationError, InvalidCallbackDataError) as e:
            raise InvalidCallbackDataError("не удалось обработать команду", location={"data": data}) from e

    async def on_error(self, update: Update | object, context: CustomContext) -> None:
        logger = get_user_input_logger(update)
        error = context.error
        error_description = get_error_description(error) if error else "отсутствует информация об ошибке"
        match error:
            case telegram.error.BadRequest():
                if is_not_modified_tg_error(error):
                    logger.info("No update has taken place in error handle: %s", error)
                    return
                await self._messenger.send(context, f"❌ ошибка обработки запроса телеграма - {error_description}")
            case MenuError():
                await self._messenger.render_menu_error(context, error_description, logger)
            case Error():
                await self._messenger.send(context, error_description)
            case httpx.TransportError(), telegram.error.NetworkError():
                logger.exception("Error with connection, likely while trying to fetch updates, suppressing the error")
            case _:
                await self._messenger.send(context, f"❌ неизвестная ошибка - {error_description}")
        if (job := context.job) and job.name and job.name != BOOKING_REFRESHER_JOB_NAME:
            self._bot_manager.stop_bot(job.name, context, logger, state=Lifecycle.FAILED)
        logger.error("Exception while handling an update: %s", error, exc_info=error)

    async def on_success(self, update: Update, context: CustomContext) -> None:
        logger = get_user_input_logger(update)
        await self._messenger.safe_delete(context.ensured_chat_data.menu_error_msg_id, logger)
        context.ensured_chat_data.menu_error_msg_id = None

    def _validate_access(self, update: Update) -> None:
        message = update.message or (update.callback_query and update.callback_query.message)
        if not message:
            raise InternalError("не удалось обработать сообщение")
        user_id = update.effective_user.id if update.effective_user else None
        chat_id = message.chat_id if isinstance(message, Message) else message.chat.id
        if not (self._chat_id == chat_id == user_id):
            raise ForbiddenError("отсутствует доступ к боту")

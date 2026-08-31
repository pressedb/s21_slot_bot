from collections.abc import Callable
from zoneinfo import ZoneInfo

import cashews
from cashews.backends.interface import Backend
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    JobQueue,
    MessageHandler,
    filters,
)

from s21_slot_bot.app.booking_manager import BookingManager
from s21_slot_bot.app.bot_manager import BotManager
from s21_slot_bot.app.flows.collector import FlowCollector
from s21_slot_bot.app.input_handler import InputHandler
from s21_slot_bot.app.messenger import Messenger
from s21_slot_bot.app.models import App, AppBuilder, BotData, ChatData, CustomContext
from s21_slot_bot.client.middleware.auth import School21AuthMiddleware
from s21_slot_bot.client.middleware.retry import School21RetryMiddleware
from s21_slot_bot.client.s21_client import School21Client
from s21_slot_bot.common.logger import LogEntity, get_id_logger
from s21_slot_bot.config import SlotBotServiceConfig


class SlotBotService:
    def __init__(
        self,
        config: SlotBotServiceConfig,
        cache_setup: Callable[..., Backend] = cashews.setup,
        s21_auth_middleware_factory: type[School21AuthMiddleware] = School21AuthMiddleware,
        s21_retry_middleware_factory: type[School21RetryMiddleware] = School21RetryMiddleware,
        s21_client_factory: type[School21Client] = School21Client,
        tg_app_builder: type[AppBuilder] = ApplicationBuilder,
        messenger_factory: type[Messenger] = Messenger,
        bot_manager_factory: type[BotManager] = BotManager,
        booking_manager_factory: type[BookingManager] = BookingManager,
        flow_collector_factory: type[FlowCollector] = FlowCollector,
        input_handler_factory: type[InputHandler] = InputHandler,
    ):
        self._config = config
        self._cache_setup = cache_setup
        s21_auth_middleware = s21_auth_middleware_factory(config=config.s21)
        s21_retry_middleware = s21_retry_middleware_factory(config=config.s21)
        self._s21_client = s21_client_factory(
            config=config.s21,
            auth_middleware=s21_auth_middleware,
            retry_middleware=s21_retry_middleware,
        )
        self._chat_id = config.bot.tg_chat_id.get_secret_value()
        self._tg_app = self._build_tg_app(
            tg_app_builder=tg_app_builder, token=config.tg_token.get_secret_value(), timezone=config.timezone
        )
        self._messenger = messenger_factory(chat_id=self._chat_id, bot=self._tg_app.bot)
        self._booking_manager = booking_manager_factory(
            s21_client=self._s21_client,
            messenger=self._messenger,
            app=self._tg_app,
            refresh_interval=config.bot.refresh_bookings_interval_sec,
            chat_id=self._chat_id,
        )
        self._bot_manager = bot_manager_factory(
            bot_config=config.bot,
            chat_id=self._chat_id,
            s21_client=self._s21_client,
            messenger=self._messenger,
            booking_manager=self._booking_manager,
        )
        self._flows = flow_collector_factory(
            s21_client=self._s21_client,
            bot_manager=self._bot_manager,
            booking_manager=self._booking_manager,
            messenger=self._messenger,
        )
        self._input_handler = input_handler_factory(
            bot_manager=self._bot_manager,
            messenger=self._messenger,
            flows=self._flows,
            chat_id=self._chat_id,
        )

        self._wire_app_handlers()

    def start(self) -> None:
        self._tg_app.run_polling()

    def _build_tg_app(self, tg_app_builder: type[AppBuilder], token: str, timezone: ZoneInfo) -> App:
        defaults = Defaults(tzinfo=timezone)
        context_types = ContextTypes(context=CustomContext, bot_data=BotData, chat_data=ChatData)
        job_queue: JobQueue[CustomContext] = JobQueue()
        app = tg_app_builder().token(token).context_types(context_types).job_queue(job_queue).defaults(defaults).build()
        return app

    def _wire_app_handlers(self) -> None:
        # TODO: check in a new chat
        self._tg_app.add_handler(CommandHandler("start", self._input_handler.on_cmd_start))
        self._tg_app.add_handler(CallbackQueryHandler(self._input_handler.on_callback))
        self._tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._input_handler.on_text))
        self._tg_app.add_error_handler(self._input_handler.on_error)
        self._tg_app.post_init = self._post_init
        self._tg_app.post_stop = self._post_stop

    async def _post_init(self, app: App) -> None:
        logger = get_id_logger(LogEntity.SERVICE_HOOK)
        logger.info("Running custom post-init application hook...")
        self._cache_setup("mem://")
        await self._s21_client.start()
        await self._booking_manager.initialize_verifier_bookings(app, logger)
        if not self._config.bot.should_refresh_bookings_only_on_active_bots:
            await self._booking_manager.start_refreshing(logger, run_immediately=False)

    async def _post_stop(self, app: App) -> None:
        logger = get_id_logger(LogEntity.SERVICE_HOOK)
        logger.info("Running custom post-stop application hook...")
        chat_data = app.chat_data.get(self._chat_id)
        if chat_data:
            await self._messenger.safe_delete(chat_data.menu_error_msg_id, logger)
            await self._messenger.safe_delete(chat_data.menu_msg_id, logger)
            logger.info("Deleted menu messages")
        await self._s21_client.stop()

from collections.abc import AsyncGenerator, Callable
from datetime import datetime, timedelta
from http import HTTPStatus
from logging import Logger
from typing import Any
from unittest.mock import AsyncMock, MagicMock, create_autospec
from zoneinfo import ZoneInfo

import aiohttp
import cashews
import pytest
import pytest_asyncio
from _pytest.monkeypatch import MonkeyPatch
from telegram import CallbackQuery, Message, Update, User
from telegram.ext import Application, ApplicationBuilder, Defaults, ExtBot, Job, JobQueue

from s21_slot_bot.app.booking_manager import BookingManager
from s21_slot_bot.app.bot_manager import BotManager
from s21_slot_bot.app.flows.book import BookFlow
from s21_slot_bot.app.flows.collector import FlowCollector
from s21_slot_bot.app.flows.delete import DeleteFlow
from s21_slot_bot.app.flows.edit import EditFlow
from s21_slot_bot.app.flows.start import StartFlow
from s21_slot_bot.app.flows.status import StatusFlow
from s21_slot_bot.app.flows.stop import StopFlow
from s21_slot_bot.app.input_handler import InputHandler
from s21_slot_bot.app.messenger import Messenger
from s21_slot_bot.app.models import (
    BotData,
    BotInstance,
    ChatData,
    CustomContext,
    FlowCategory,
    Lifecycle,
    Mode,
    SearchConfig,
)
from s21_slot_bot.client.middleware.auth import School21AuthMiddleware
from s21_slot_bot.client.middleware.retry import School21RetryMiddleware
from s21_slot_bot.client.models import (
    DryRevieweeBooking,
    Project,
    ProjectExtended,
    ProjectStatus,
    RevieweeBooking,
    ReviewInfo,
    SlotsInfo,
    TimeSlot,
    VerifierBooking,
)
from s21_slot_bot.client.s21_client import School21Client
from s21_slot_bot.common.logger import LoggerLike
from s21_slot_bot.config import SlotBotServiceConfig
from s21_slot_bot.logging_config import LogConfig
from s21_slot_bot.service import SlotBotService

# ------------- HELPERS -------------


def response_context(response: aiohttp.ClientResponse) -> MagicMock:
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=response)
    return context


# ------------- CACHE -------------


@pytest.fixture(scope="session", autouse=True)
def setup_cache() -> None:
    cashews.cache.setup("mem://")


@pytest_asyncio.fixture(autouse=True)
async def clear_cache() -> AsyncGenerator[None, Any]:
    yield
    await cashews.cache.clear()


# ------------- EXTERNAL ENTITY MOCKS -------------


@pytest.fixture
def timezone() -> ZoneInfo:
    return ZoneInfo("Europe/Moscow")


@pytest.fixture
def now(timezone: ZoneInfo) -> datetime:
    return datetime(2026, 8, 19, 20, 10, 15, tzinfo=timezone)


@pytest.fixture
def logger_mock() -> LoggerLike:
    return create_autospec(Logger, instance=True, spec_set=True)


@pytest.fixture
def bot_mock(timezone: ZoneInfo) -> ExtBot:
    bot = create_autospec(ExtBot, instance=True, spec_set=True)
    bot.defaults = Defaults(tzinfo=timezone)
    return bot


@pytest.fixture
def session_mock() -> aiohttp.ClientSession:
    session = create_autospec(aiohttp.ClientSession, instance=True, spec_set=True)
    session.closed = False
    session.close = AsyncMock()
    return session


@pytest.fixture
def request_mock(session_mock: aiohttp.ClientSession) -> aiohttp.ClientRequest:
    request = MagicMock(spec=aiohttp.ClientRequest)
    request.session = session_mock
    request.url = aiohttp.client_reqrep.URL("https://platform.21-school.ru/services/graphql")
    request.headers = {}
    return request


@pytest.fixture
def job_queue_mock() -> JobQueue:
    return MagicMock(spec=JobQueue)


@pytest.fixture
def job_mock() -> Job:
    job = MagicMock(spec=Job)
    job.run = AsyncMock()
    return job


@pytest.fixture
def tg_app_mock(bot_mock: ExtBot, job_queue_mock: JobQueue) -> Application:
    app = MagicMock(spec=Application)
    app.bot = bot_mock
    app.job_queue = job_queue_mock
    app.chat_data = {}
    app.bot_data = BotData()
    return app


@pytest.fixture
def tg_app_builder_mock(tg_app_mock: Application) -> MagicMock:
    builder = MagicMock(spec=ApplicationBuilder)
    builder.token.return_value = builder
    builder.context_types.return_value = builder
    builder.job_queue.return_value = builder
    builder.defaults.return_value = builder
    builder.build.return_value = tg_app_mock
    return MagicMock(return_value=builder)


@pytest.fixture
def context(
    tg_app_mock: Application,
    bot_mock: ExtBot,
    job_queue_mock: JobQueue,
    job_mock: Job,
    timezone: ZoneInfo,
) -> CustomContext:
    context = MagicMock(spec=CustomContext)
    context.application = tg_app_mock
    context.bot = bot_mock
    context.bot_data = BotData()
    context.ensured_chat_data = ChatData()
    context.ensured_job_queue = job_queue_mock
    context.job = job_mock
    context.error = None
    return context


@pytest.fixture
def message(config: SlotBotServiceConfig) -> Message:
    message = MagicMock(spec=Message)
    message.message_id = 10
    message.chat_id = config.bot.tg_chat_id.get_secret_value()
    message.text = ""
    message.reply_text = AsyncMock()
    return message


@pytest.fixture
def update_mock(config: SlotBotServiceConfig, message: Message) -> Update:
    update = MagicMock(spec=Update)
    update.update_id = 100
    update.message = message
    user = MagicMock(spec=User)
    user.id = config.bot.tg_chat_id.get_secret_value()
    update.effective_user = user
    update.callback_query = None
    return update


@pytest.fixture
def query_mock(message: Message) -> CallbackQuery:
    query = MagicMock(spec=CallbackQuery)
    query.id = "query-100"
    query.data = ""
    query.answer = AsyncMock()
    query.message = message
    return query


# ------------- CONFIGS -------------


@pytest.fixture
def config(monkeypatch: MonkeyPatch) -> SlotBotServiceConfig:
    env = {
        "S21_USERNAME": "user1",
        "S21_PASSWORD": "password1",
        "S21_CAMPUS": "Moscow",
        "TG_BOT_TOKEN": "123456:TEST_TOKEN",
        "TG_CHAT_ID": "12345",
        "MAX_BOTS": "3",
        "POLL_INTERVAL_SEC": "60",
        "REFRESH_BOOKINGS_INTERVAL_SEC": "60",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return SlotBotServiceConfig()


@pytest.fixture
def log_config(monkeypatch: MonkeyPatch) -> LogConfig:
    env = {
        "LOG_LEVEL": "debug",
        "LOG_SILENCE_LIBRARIES": "telegram, httpx",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return LogConfig()


# ------------- SERVICE COMPONENTS -------------


@pytest.fixture
def s21_auth_middleware(config: SlotBotServiceConfig) -> School21AuthMiddleware:
    return School21AuthMiddleware(config=config.s21)


@pytest.fixture
def s21_retry_middleware(config: SlotBotServiceConfig) -> School21RetryMiddleware:
    return School21RetryMiddleware(config=config.s21)


@pytest.fixture
def s21_client(
    config: SlotBotServiceConfig,
    s21_auth_middleware: School21AuthMiddleware,
    s21_retry_middleware: School21RetryMiddleware,
) -> School21Client:
    return School21Client(
        config=config.s21,
        auth_middleware=s21_auth_middleware,
        retry_middleware=s21_retry_middleware,
    )


@pytest.fixture
def messenger(config: SlotBotServiceConfig, bot_mock: ExtBot) -> Messenger:
    return Messenger(chat_id=config.bot.tg_chat_id.get_secret_value(), bot=bot_mock)


@pytest.fixture
def booking_manager(
    config: SlotBotServiceConfig,
    s21_client: School21Client,
    messenger: Messenger,
    tg_app_mock: Application,
) -> BookingManager:
    return BookingManager(
        s21_client=s21_client,
        messenger=messenger,
        app=tg_app_mock,
        refresh_interval=config.bot.refresh_bookings_interval_sec,
        chat_id=config.bot.tg_chat_id.get_secret_value(),
    )


@pytest.fixture
def bot_manager(
    config: SlotBotServiceConfig,
    s21_client: School21Client,
    messenger: Messenger,
    booking_manager: BookingManager,
) -> BotManager:
    return BotManager(
        bot_config=config.bot,
        chat_id=config.bot.tg_chat_id.get_secret_value(),
        messenger=messenger,
        s21_client=s21_client,
        booking_manager=booking_manager,
    )


@pytest.fixture
def start_flow(
    s21_client: School21Client,
    bot_manager: BotManager,
    booking_manager: BookingManager,
    messenger: Messenger,
) -> StartFlow:
    return StartFlow(s21_client, bot_manager, booking_manager, messenger, FlowCategory.START)


@pytest.fixture
def stop_flow(
    s21_client: School21Client,
    bot_manager: BotManager,
    booking_manager: BookingManager,
    messenger: Messenger,
) -> StopFlow:
    return StopFlow(s21_client, bot_manager, booking_manager, messenger, FlowCategory.STOP)


@pytest.fixture
def delete_flow(
    s21_client: School21Client,
    bot_manager: BotManager,
    booking_manager: BookingManager,
    messenger: Messenger,
) -> DeleteFlow:
    return DeleteFlow(s21_client, bot_manager, booking_manager, messenger, FlowCategory.DELETE)


@pytest.fixture
def edit_flow(
    s21_client: School21Client,
    bot_manager: BotManager,
    booking_manager: BookingManager,
    messenger: Messenger,
) -> EditFlow:
    return EditFlow(s21_client, bot_manager, booking_manager, messenger, FlowCategory.EDIT)


@pytest.fixture
def status_flow(
    s21_client: School21Client,
    bot_manager: BotManager,
    booking_manager: BookingManager,
    messenger: Messenger,
) -> StatusFlow:
    return StatusFlow(s21_client, bot_manager, booking_manager, messenger, FlowCategory.STATUS)


@pytest.fixture
def book_flow(
    s21_client: School21Client,
    bot_manager: BotManager,
    booking_manager: BookingManager,
    messenger: Messenger,
) -> BookFlow:
    return BookFlow(s21_client, bot_manager, booking_manager, messenger, FlowCategory.BOOK)


@pytest.fixture
def flow_collector(
    s21_client: School21Client,
    bot_manager: BotManager,
    booking_manager: BookingManager,
    messenger: Messenger,
    start_flow: StartFlow,
    stop_flow: StopFlow,
    delete_flow: DeleteFlow,
    edit_flow: EditFlow,
    status_flow: StatusFlow,
    book_flow: BookFlow,
) -> FlowCollector:
    return FlowCollector(
        s21_client=s21_client,
        bot_manager=bot_manager,
        booking_manager=booking_manager,
        messenger=messenger,
        start_factory=MagicMock(return_value=start_flow),
        stop_factory=MagicMock(return_value=stop_flow),
        delete_factory=MagicMock(return_value=delete_flow),
        edit_factory=MagicMock(return_value=edit_flow),
        status_factory=MagicMock(return_value=status_flow),
        book_factory=MagicMock(return_value=book_flow),
    )


@pytest.fixture
def input_handler(
    config: SlotBotServiceConfig,
    bot_manager: BotManager,
    messenger: Messenger,
    flow_collector: FlowCollector,
) -> InputHandler:
    return InputHandler(
        bot_manager=bot_manager,
        messenger=messenger,
        flows=flow_collector,
        chat_id=config.bot.tg_chat_id.get_secret_value(),
    )


@pytest.fixture
def service(
    config: SlotBotServiceConfig,
    s21_auth_middleware: School21AuthMiddleware,
    s21_retry_middleware: School21RetryMiddleware,
    s21_client: School21Client,
    tg_app_builder_mock: MagicMock,
    messenger: Messenger,
    booking_manager: BookingManager,
    bot_manager: BotManager,
    flow_collector: FlowCollector,
    input_handler: InputHandler,
) -> SlotBotService:
    return SlotBotService(
        config=config,
        s21_auth_middleware_factory=MagicMock(return_value=s21_auth_middleware),
        s21_retry_middleware_factory=MagicMock(return_value=s21_retry_middleware),
        s21_client_factory=MagicMock(return_value=s21_client),
        tg_app_builder=tg_app_builder_mock,
        messenger_factory=MagicMock(return_value=messenger),
        booking_manager_factory=MagicMock(return_value=booking_manager),
        bot_manager_factory=MagicMock(return_value=bot_manager),
        flow_collector_factory=MagicMock(return_value=flow_collector),
        input_handler_factory=MagicMock(return_value=input_handler),
    )


# ------------- FACTORIES -------------


@pytest.fixture
def response_factory() -> Callable[..., aiohttp.ClientResponse]:
    def factory(
        *,
        status: HTTPStatus = HTTPStatus.OK,
        reason: str = "",
        text: str = "",
        json: Any = None,
        history: list | None = None,
    ) -> aiohttp.ClientResponse:
        response = MagicMock(spec=aiohttp.ClientResponse)
        response.status = status
        response.ok = status < HTTPStatus.BAD_REQUEST
        response.reason = reason
        response.text = AsyncMock(return_value=text)
        response.json = AsyncMock(return_value=json or {})
        response.history = history or []

        def _raise_for_status() -> None:
            if not response.ok:
                raise aiohttp.ClientResponseError(
                    request_info=MagicMock(), history=(MagicMock(),), status=response.status
                )

        response.raise_for_status = _raise_for_status
        return response

    return factory


@pytest.fixture
def bot_instance_factory(now: datetime) -> Callable[..., BotInstance]:
    def factory(
        *,
        bot_id: str = "bot-1",
        project_id: str = "project-1",
        project_name: str = "Project 1",
        required_reviews: int = 2,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        interval_sec: int = 60,
        mode: Mode = Mode.FIND_AND_BOOK,
        state: Lifecycle = Lifecycle.STOPPED,
    ) -> BotInstance:
        return BotInstance(
            cfg=SearchConfig(
                bot_id=bot_id,
                project_id=project_id,
                project_name=project_name,
                required_reviews=required_reviews,
                from_dt=from_dt or now,
                to_dt=to_dt or now + timedelta(hours=2),
                interval_sec=interval_sec,
                mode=mode,
            ),
            state=state,
        )

    return factory


@pytest.fixture
def project_factory() -> Callable[..., Project]:
    def factory(
        *,
        project_id: str = "project-1",
        name: str = "Project 1",
        status: ProjectStatus = ProjectStatus.P2P_EVALUATIONS,
        course_id: str | None = None,
        course_status: ProjectStatus | None = None,
    ) -> Project:
        return Project(
            id=project_id,
            name=name,
            status=status,
            course_id=course_id,
            course_status=course_status,
        )

    return factory


@pytest.fixture
def project_extended_factory() -> Callable[..., ProjectExtended]:
    def factory(
        *,
        project_id: str = "project-1",
        name: str = "Project 1",
        booked: int = 0,
        required: int = 3,
    ) -> ProjectExtended:
        return ProjectExtended(
            id=project_id,
            name=name,
            status=ProjectStatus.P2P_EVALUATIONS,
            review_info=ReviewInfo(required=required, booked=booked),
        )

    return factory


@pytest.fixture
def reviewee_booking_factory(now: datetime) -> Callable[..., RevieweeBooking]:
    def factory(
        *,
        booking_id: str = "booking-1",
        project_id: str = "project-1",
        project_name: str = "Project 1",
        student_login: str | None = "student",
        start: datetime | None = None,
        end: datetime | None = None,
        url: str | None = None,
    ) -> RevieweeBooking:
        return RevieweeBooking(
            id=booking_id,
            answer_id="answer-1",
            project_id=project_id,
            project_name=project_name,
            student_login=student_login,
            start=start or now + timedelta(minutes=30),
            end=end or now + timedelta(hours=1),
            url=url,
        )

    return factory


@pytest.fixture
def dry_reviewee_booking_factory(now: datetime) -> Callable[..., DryRevieweeBooking]:
    def factory(
        *,
        booking_id: str = "booking-1",
        project_id: str = "project-1",
        project_name: str = "Project 1",
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> DryRevieweeBooking:
        return DryRevieweeBooking(
            id=booking_id,
            answer_id="answer-1",
            project_id=project_id,
            project_name=project_name,
            start=start or now + timedelta(minutes=30),
            end=end or now + timedelta(hours=1),
        )

    return factory


@pytest.fixture
def verifier_booking_factory(now: datetime) -> Callable[..., VerifierBooking]:
    def factory(
        booking_id: str = "verifier-booking",
        start: datetime | None = None,
        end: datetime | None = None,
        project_id: str | None = "project-id",
        project_name: str | None = "Project",
        student_login: str | None = "student",
        url: str | None = None,
    ) -> VerifierBooking:
        actual_start = start or now + timedelta(hours=1)
        return VerifierBooking(
            id=booking_id,
            start=actual_start,
            end=end or actual_start + timedelta(minutes=45),
            project_id=project_id,
            project_name=project_name,
            student_login=student_login,
            url=url,
        )

    return factory


@pytest.fixture
def timeslot_factory(now: datetime) -> Callable[..., TimeSlot]:
    def factory(
        *,
        valid_start_times: list[datetime] | None = None,
        staff_slot: bool = False,
    ) -> TimeSlot:
        values = valid_start_times if valid_start_times is not None else [now + timedelta(minutes=30)]
        return TimeSlot(
            start=min(values) if values else now,
            end=(max(values) if values else now) + timedelta(hours=1),
            valid_start_times=values,
            staff_slot=staff_slot,
        )

    return factory


@pytest.fixture
def slots_info_factory(timeslot_factory: Callable[..., TimeSlot]) -> Callable[..., SlotsInfo]:
    def factory(
        *,
        booked: int = 0,
        required: int = 2,
        time_slots: list[TimeSlot] | None = None,
    ) -> SlotsInfo:
        return SlotsInfo(
            check_duration=45,
            review_info=ReviewInfo(required=required, booked=booked),
            time_slots=time_slots if time_slots is not None else [timeslot_factory()],
        )

    return factory

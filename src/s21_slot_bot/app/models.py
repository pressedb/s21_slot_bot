import enum
from enum import StrEnum
from typing import Annotated, Any

from pydantic import AwareDatetime, BaseModel, Field, NonNegativeInt, PositiveInt, TypeAdapter
from telegram.ext import Application, ApplicationBuilder, CallbackContext, ExtBot, JobQueue

from s21_slot_bot.app.consts import (
    MAX_INTERVAL_SEC,
    MAX_NUM_BOTS,
    MIN_INTERVAL_SEC,
    MIN_NUM_BOTS,
)
from s21_slot_bot.app.errors import AppNotInitializedError
from s21_slot_bot.client.models import BookedReviews, ProjectExtended, RequiredReviews
from s21_slot_bot.common.logger import LogEntity, LoggerAdapterID, get_id_logger

type IntervalSec = Annotated[PositiveInt, Field(ge=MIN_INTERVAL_SEC, le=MAX_INTERVAL_SEC)]
type NumBots = Annotated[PositiveInt, Field(ge=MIN_NUM_BOTS, le=MAX_NUM_BOTS)]

type Bot = ExtBot[None]
type UserData = dict[Any, Any]
type App = Application[Bot, CustomContext, UserData, ChatData, BotData, JobQueue[CustomContext]]
type AppBuilder = ApplicationBuilder[Bot, CustomContext, UserData, ChatData, BotData, JobQueue[CustomContext]]

RequiredReviewsAdapter: TypeAdapter[RequiredReviews] = TypeAdapter(RequiredReviews)
IntervalSecAdapter: TypeAdapter[IntervalSec] = TypeAdapter(IntervalSec)


class MenuButton(StrEnum):
    START = "▶️ Начать"
    STOP = "⛔ Остановить"
    DELETE = "🗑️ Удалить"
    EDIT = "✏️ Изменить"
    STATUS = "📌 Статус"


class Lifecycle(StrEnum):
    RUNNING = enum.auto()
    STOPPED = enum.auto()
    FAILED = enum.auto()

    def to_emoji_text(self) -> tuple[str, str]:
        match self:
            case Lifecycle.RUNNING:
                return "▶️", "активен"
            case Lifecycle.STOPPED:
                return "⏸️", "остановлен"
            case Lifecycle.FAILED:
                return "❌", "ошибка"


class FlowCategory(StrEnum):
    START = enum.auto()
    STOP = enum.auto()
    DELETE = enum.auto()
    EDIT = enum.auto()
    STATUS = enum.auto()
    BOOK = enum.auto()


class Mode(StrEnum):
    ONLY_FIND = enum.auto()
    FIND_AND_BOOK = enum.auto()

    def to_emoji_text(self) -> tuple[str, str]:
        match self:
            case Mode.ONLY_FIND:
                return "🔍", "только найти слот (без записи)"
            case Mode.FIND_AND_BOOK:
                return "📝", "найти слоты и записаться"


class Screen(StrEnum):
    MENU = enum.auto()

    START_PICK_FROM = enum.auto()
    START_PICK_TO = enum.auto()

    EDIT_WAIT_FROM = enum.auto()
    EDIT_WAIT_TO = enum.auto()
    EDIT_WAIT_INTERVAL = enum.auto()


class Stats(BaseModel):
    last_ping: AwareDatetime | None = None
    attempts_total: NonNegativeInt = 0
    attempts_success: NonNegativeInt = 0
    attempts_failed: NonNegativeInt = 0
    currently_booked: BookedReviews = 0


class SearchConfig(BaseModel):
    bot_id: str
    project_id: str
    project_name: str
    required_reviews: RequiredReviews
    from_dt: AwareDatetime
    to_dt: AwareDatetime
    interval_sec: IntervalSec
    mode: Mode


class BotInstance(BaseModel):
    cfg: SearchConfig
    state: Lifecycle = Lifecycle.STOPPED
    stats: Stats = Field(default_factory=Stats)

    def logger(self) -> LoggerAdapterID:
        return get_id_logger(LogEntity.BOT, entity_id=self.cfg.bot_id)


class JobData(BaseModel):
    inst: BotInstance
    task_id: str
    answer_id: str


class ChatData(BaseModel):
    screen: Screen = Screen.MENU
    menu_msg_id: int | None = None
    menu_error_msg_id: int | None = None
    projects_map: dict[str, ProjectExtended] = Field(default_factory=dict)
    last_booking_refresh_time: AwareDatetime | None = None
    start_project_id: str | None = None
    selected_required_reviews: RequiredReviews | None = None
    start_from: AwareDatetime | None = None
    start_to: AwareDatetime | None = None
    start_mode: Mode | None = None
    edit_bot_id: str | None = None


# NOTE: bot_data is not None on unhandled errors, unlike chat_data
class BotData(BaseModel):
    chat_should_move_menu: dict[int, bool] = Field(default_factory=dict)


class CustomContext(CallbackContext[Bot, UserData, ChatData, BotData]):
    """Wrapper around CallbackContext to pass a custom chat data model as a type parameter."""

    def __init__(
        self,
        application: App,
        chat_id: int | None = None,
        user_id: int | None = None,
    ):
        super().__init__(application=application, chat_id=chat_id, user_id=user_id)

    @property
    def ensured_chat_data(self) -> ChatData:
        if not self.chat_data:
            raise AppNotInitializedError("данные чата не инициализированы")
        return self.chat_data

    @property
    def ensured_job_queue(self) -> JobQueue[CustomContext]:
        if self.job_queue is None:
            raise AppNotInitializedError("очередь задач не инициализирована")
        return self.job_queue  # type: ignore [return-value]

from pydantic import Field, Secret
from pydantic_settings import BaseSettings

from s21_slot_bot.app.models import IntervalSec, NumBots


class BotConfig(BaseSettings):
    tg_chat_id: Secret[int] = Field(
        alias="TG_CHAT_ID",
        description="Chat ID used for user authentication and sending messages to",
    )
    max_bots: NumBots = Field(
        alias="MAX_BOTS",
        description="Maximum number of total bots at a time",
        default=5,
    )
    poll_interval_sec: IntervalSec = Field(
        alias="POLL_INTERVAL_SEC",
        description="Interval (in seconds) between slot polling requests",
        default=30,
    )
    refresh_bookings_interval_sec: IntervalSec = Field(
        alias="REFRESH_BOOKINGS_INTERVAL_SEC",
        description="Interval (in seconds) between sending requests to get current bookings",
        default=60,
    )

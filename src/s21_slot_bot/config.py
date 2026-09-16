from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr

from s21_slot_bot.app.config import BotConfig
from s21_slot_bot.client.config import S21ClientConfig
from s21_slot_bot.common.settings import BaseSettingsWithSecrets
from s21_slot_bot.logging_config import LogConfig


class SlotBotServiceConfig(BaseSettingsWithSecrets):
    s21: S21ClientConfig = Field(default_factory=S21ClientConfig)
    bot: BotConfig = Field(default_factory=BotConfig)
    log: LogConfig = Field(default_factory=LogConfig)
    tg_token: SecretStr = Field(
        alias="TG_BOT_TOKEN", description="Token used for managing and accessing the telegram bot"
    )
    timezone: ZoneInfo = Field(
        alias="TIMEZONE",
        description="Timezone used for input processing (unless specified in user prompt) and output",
        default=ZoneInfo("Europe/Moscow"),
    )

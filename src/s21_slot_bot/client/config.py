from pydantic import Field, NonNegativeInt, PositiveInt, SecretStr

from s21_slot_bot.client.models import Campus
from s21_slot_bot.common.settings import BaseSettingsWithSecrets


class S21ClientConfig(BaseSettingsWithSecrets):
    username: str = Field(alias="S21_USERNAME")
    password: SecretStr = Field(alias="S21_PASSWORD")
    campus: Campus = Field(alias="S21_CAMPUS")
    timeout_total_sec: NonNegativeInt = Field(alias="S21_TIMEOUT_TOTAL_SEC", default=90)
    timeout_connect_sec: NonNegativeInt = Field(alias="S21_TIMEOUT_CONNECT_SEC", default=10)
    timeout_read_sec: NonNegativeInt = Field(alias="S21_TIMEOUT_READ_SEC", default=20)
    cache_ttl_sec: NonNegativeInt = Field(alias="S21_CACHE_TTL_SEC", default=5 * 60)
    max_request_retries: PositiveInt = Field(
        alias="S21_MAX_REQUEST_RETRIES",
        description="Maximum number of request retries. Set to 1 to disable retries",
        default=7,
    )
    retry_delay_sec: NonNegativeInt = Field(
        alias="S21_RETRY_DELAY_SEC",
        description="Initial delay (in seconds) between retries",
        default=2,
    )
    retry_backoff: PositiveInt = Field(
        alias="S21_RETRY_BACKOFF",
        description="Multiplier applied to delay between attempts",
        default=2,
    )

import logging.config
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import SettingsConfigDict

from s21_slot_bot.common.logger import LogLevel
from s21_slot_bot.common.settings import BaseSettingsWithSecrets


class LogConfig(BaseSettingsWithSecrets):
    model_config = SettingsConfigDict(enable_decoding=False)

    level: LogLevel = Field(alias="LOG_LEVEL", description="Logging level", default=LogLevel.INFO)
    silence_libraries: set[str] = Field(
        alias="LOG_SILENCE_LIBRARIES",
        description="Libraries whose logging level should be set to WARNING only "
        "(can be useful to tune out noisy ones in debug mode, e.g. 'telegram, urllib3, httpcore')",
        default_factory=set,
    )

    @field_validator("level", mode="before")
    @classmethod
    def _coerce_log_level(cls, value: Any) -> LogLevel:
        match value:
            case LogLevel():
                return value
            case str():
                return LogLevel.from_str(value)
            case _:
                return LogLevel.INFO

    @field_validator("silence_libraries", mode="before")
    @classmethod
    def _parse_comma_separated_set(cls, value: Any) -> set[str] | Any:
        match value:
            case str():
                return {item.strip() for item in value.split(",")}
            case _:
                return value


def setup_logging(log_config: LogConfig) -> None:
    level_name = log_config.level.name
    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "sanitize": {
                "()": "s21_slot_bot.common.logger.LoggerSensitiveFilter",
                "substitution_patterns": {
                    r"(https://api\.telegram\.org/bot)([^/]+)/": r"\1[TOKEN]/",
                },
            },
        },
        "formatters": {
            "default": {
                "format": "[%(asctime)s %(levelname)s] %(message)s",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": level_name,
                "formatter": "default",
                "filters": ["sanitize"],
                "stream": "ext://sys.stdout",
            },
        },
        "root": {
            "level": level_name,
            "handlers": ["console"],
        },
    }
    if log_config.silence_libraries:
        loggers = {"loggers": {lib: {"level": "WARNING", "propagate": True} for lib in log_config.silence_libraries}}
        logging_config.update(loggers)
    logging.config.dictConfig(logging_config)

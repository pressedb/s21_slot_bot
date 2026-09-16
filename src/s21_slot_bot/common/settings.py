from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseSettingsWithSecrets(BaseSettings):
    model_config = SettingsConfigDict(hide_input_in_errors=True)

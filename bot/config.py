"""Application settings loaded from environment variables and a ``.env`` file.

Field names map to environment variables case-insensitively, so ``bot_token``
is read from ``BOT_TOKEN``, ``log_level`` from ``LOG_LEVEL``, and so on.
Values come from two sources: the process environment and a ``.env`` file in
the current working directory; the environment takes precedence over the file.
An absent or empty token is an empty string, not an error — a missing or
empty ``.env`` file is fine too.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration of the bot.

    Sources, highest priority first: environment variables, then the ``.env``
    file in the current working directory, then the defaults below; a missing
    or empty ``BOT_TOKEN`` yields an empty string instead of raising.
    """

    model_config = SettingsConfigDict(env_file=".env")

    bot_token: str = ""
    log_level: str = "INFO"


#: Module-level configuration instance.
settings = Settings()

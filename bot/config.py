"""Application settings loaded from environment variables and a ``.env`` file.

The project settings are read from the environment variables and from
a ``.env`` file in the current working directory; the environment takes
precedence over the file, defaults apply when neither source defines
the field. Field names map to environment variables case-insensitively,
so ``bot_token`` is read from ``BOT_TOKEN``, ``log_level`` from
``LOG_LEVEL``, ``locale`` from ``LOCALE``, ``database_url`` from
``DATABASE_URL`` and ``owner_id`` from ``OWNER_ID``. An absent or empty
value is an empty string, not an error — a missing or empty ``.env``
file is fine too (an empty ``OWNER_ID`` reads as ``None``, «no owner
configured»).
"""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Level names ``logging`` itself accepts — the 5 real levels plus the
#: ``WARN``/``FATAL`` aliases and ``NOTSET`` (I-2).
LOG_LEVELS = frozenset(
    {"CRITICAL", "FATAL", "ERROR", "WARN", "WARNING", "INFO", "DEBUG", "NOTSET"}
)


class Settings(BaseSettings):
    """Runtime configuration of the bot.

    Sources, highest priority first: environment variables, then the ``.env``
    file in the current working directory, then the defaults below; a missing
    or empty ``BOT_TOKEN`` yields an empty string instead of raising.
    """

    model_config = SettingsConfigDict(env_file=".env")

    bot_token: str = ""
    log_level: str = "INFO"
    #: Locale applied at startup (``LOCALE``, gettext catalog in ``bot/locales``).
    locale: str = "en"
    #: Database DSN of the bot (``DATABASE_URL``, item 1): empty string →
    #: the startup aborts before ``Bot()`` is built — never a ValidationError.
    database_url: str = ""
    #: Settings-menu owner (``OWNER_ID``): ``None`` when unset or empty —
    #: until it is configured the menu only opens for an admin of a pair chat.
    owner_id: int | None = None

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> str:
        """Normalise ``LOG_LEVEL`` to an UPPER-case level name (I-2).

        ``debug``/``warning``/… arrive uppercased (``DEBUG``,
        ``WARNING``), an already-uppercase value passes through, and a
        value that names no logging level — ``nope``, the empty string —
        is a configuration error: ``ValueError`` (pydantic wraps it into
        its ``ValidationError``, itself a ``ValueError`` subclass).
        """
        if not isinstance(value, str):
            raise ValueError(f"log level must be a string, got {value!r}")
        name = value.strip().upper()
        if name not in LOG_LEVELS:
            raise ValueError(
                f"unknown log level {value!r}; expected one of {sorted(LOG_LEVELS)}"
            )
        return name

    @field_validator("owner_id", mode="before")
    @classmethod
    def _owner_id_from_env(cls, value: object) -> object:
        """Read ``OWNER_ID`` leniently: an empty string means «no owner».

        ``.env`` files routinely carry ``OWNER_ID=`` while an operator
        edits them — an empty value must come out as ``None`` instead of
        a ``ValidationError``, and a digit string becomes the ``int``
        the ACL compares user ids with.
        """
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            return int(stripped)
        return value


#: Module-level configuration instance.
settings = Settings()

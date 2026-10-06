"""Configuration tests: module ``bot.config`` (pydantic-settings).

RED phase (cycle 7): the module exists, but the ``.env`` file support
does not — ``bot.config`` reads the process environment only, so every
test of ``TestDotEnvFile`` fails because a ``.env`` file is ignored
(``assert "" == <value from the file>``).

Specification (wiki, General-idea; AGENTS.md — "Secrets live only in .env"):
- class ``Settings(BaseSettings)`` with fields ``bot_token`` (env
  ``BOT_TOKEN``) and ``log_level`` (default ``"INFO"``);
- values are read from the environment variables and from a ``.env``
  file in the current working directory; the environment takes
  precedence over the file; defaults apply when neither source defines
  the field;
- a missing or empty ``BOT_TOKEN`` is not an exception but an empty value;
- a module-level ``settings`` instance exists;
- there are no web settings at all (the bot is aiogram-only) — see
  ``test_web_fields_are_gone_from_settings``;
- isolation: no test may ever see the project's real ``.env`` (that is
  where the user's token will live) — see the autouse fixture
  ``isolated_cwd`` below.
"""

import pytest
from pydantic_settings import BaseSettings

from bot import config as config_module
from bot.config import Settings

#: Environment variable names that control the project settings.
PROJECT_ENV_VARS = ("BOT_TOKEN", "LOG_LEVEL", "LOCALE", "DATABASE_URL", "OWNER_ID")


@pytest.fixture(autouse=True)
def isolated_cwd(monkeypatch, tmp_path):
    """Empty working directory for every test of this module.

    ``Settings`` reads ``.env`` from the current working directory, and
    the project root will contain the user's real ``.env`` file
    (AGENTS.md: "Secrets live only in .env").  Running every test from a
    fresh empty directory guarantees that no test — the default-values
    tests in particular — can accidentally read that file, whatever the
    user puts into it later.  ``monkeypatch.chdir`` restores the original
    working directory on teardown, so the other test modules are not
    affected either.
    """
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def env(monkeypatch):
    """Environment cleared of the project settings variables.

    Tests of default values and env pickup must be deterministic and must
    not depend on what happens to be exported in the shell.
    """
    for name in PROJECT_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class TestSettings:
    """The ``Settings`` class and its fields."""

    def test_settings_is_pydantic_base_settings(self):
        """Specification: the configuration is built on pydantic-settings."""
        assert issubclass(Settings, BaseSettings)

    def test_defaults_apply_when_env_is_clean(self, env):
        """Specification: default values apply.

        With an empty environment the fields must receive the values from
        the specification instead of failing because BOT_TOKEN is absent.
        """
        settings = Settings()

        assert settings.bot_token == ""
        assert settings.log_level == "INFO"

    def test_bot_token_read_from_env_BOT_TOKEN(self, env):
        """Specification: the token is read from the BOT_TOKEN environment variable."""
        env.setenv("BOT_TOKEN", "7000000001:AATestTokenValue")

        assert Settings().bot_token == "7000000001:AATestTokenValue"

    def test_log_level_read_from_env(self, env):
        """Specification: environment values are picked up by the fields.

        ``LOG_LEVEL`` must reach ``settings.log_level`` exactly as it was
        exported in the environment.
        """
        env.setenv("LOG_LEVEL", "DEBUG")

        assert Settings().log_level == "DEBUG"

    def test_env_overrides_defaults(self, env):
        """Specification: the environment takes precedence over default values."""
        env.setenv("LOG_LEVEL", "WARNING")

        settings = Settings()

        assert settings.log_level == "WARNING"

    def test_locale_default_is_en(self, env):
        """Specification (i18n, backlog 5): the default locale is ``en``."""
        settings = Settings()

        assert settings.locale == "en"

    def test_locale_read_from_env(self, env):
        """Specification: the locale is picked up from the ``LOCALE`` variable."""
        env.setenv("LOCALE", "ru")

        assert Settings().locale == "ru"

    def test_database_url_default_is_empty(self, env):
        """Specification (item 1, DB): ``database_url`` defaults to an empty string.

        The field must NOT be required: the suite constructs
        ``Settings()`` all over the place with no database in the
        environment, and an absent ``DATABASE_URL`` is an empty value
        (like ``BOT_TOKEN``), not a ``ValidationError``.
        """
        settings = Settings()  # must not raise ValidationError

        assert settings.database_url == ""

    def test_database_url_read_from_env(self, env):
        """Specification (item 1, DB): ``DATABASE_URL`` reaches ``settings.database_url``.

        The same pickup pattern as ``LOCALE``: the environment value
        lands in the field exactly as it was exported.
        """
        env.setenv("DATABASE_URL", "postgresql+asyncpg://bot@localhost/tg")

        assert Settings().database_url == "postgresql+asyncpg://bot@localhost/tg"

    def test_owner_id_defaults_to_none(self, env):
        """Specification (cycle B, settings ACL): ``owner_id`` defaults to ``None``.

        No ``OWNER_ID`` in the environment means «the owner is not
        configured» — until ``.env`` carries ``OWNER_ID=<id>`` the
        settings menu can only be opened by an admin of a pair chat.
        The field must NOT be required: the suite builds ``Settings()``
        everywhere without an owner in the environment.
        """
        settings = Settings()  # must not raise ValidationError

        assert settings.owner_id is None

    def test_owner_id_read_from_env_as_an_integer(self, env):
        """Specification: the numeric ``OWNER_ID`` string reaches the field as ``int``.

        ``env`` already removes OWNER_ID, so the value can only come
        from the environment — and the comparison pins the type too
        (the string ``"123456789"`` never equals ``123456789``).
        """
        env.setenv("OWNER_ID", "123456789")

        assert Settings().owner_id == 123456789

    def test_empty_owner_id_is_none_not_a_validation_error(self, env):
        """Specification: an empty ``OWNER_ID`` means «no owner», not an error.

        ``.env`` files routinely carry ``OWNER_ID=`` while an operator
        edits them — an empty string must come out as ``None``, never
        as a ``ValidationError``.
        """
        env.setenv("OWNER_ID", "")

        settings = Settings()  # must not raise ValidationError

        assert settings.owner_id is None

    def test_web_fields_are_gone_from_settings(self, env):
        """Specification change (cycle 6): no web part — no web settings.

        The FastAPI part left the project, so ``Settings`` must no longer
        carry the ``web_host`` and ``web_port`` fields: while the code
        still defines them, this test fails (AttributeError-free
        ``hasattr`` checks are the contract).
        """
        settings = Settings()

        assert hasattr(settings, "web_host") is False, (
            "web_host must not exist on Settings anymore — the FastAPI part "
            "is out of the project"
        )
        assert hasattr(settings, "web_port") is False, (
            "web_port must not exist on Settings anymore — the FastAPI part "
            "is out of the project"
        )

    def test_missing_bot_token_is_empty_value_not_exception(self, env):
        """Specification: a missing token is not an exception but an empty value.

        ``env`` already removes BOT_TOKEN from the environment.
        """
        settings = Settings()  # must not raise ValidationError

        assert settings.bot_token == ""

    def test_empty_bot_token_is_empty_value_not_exception(self, env):
        """Specification: an empty BOT_TOKEN is not an exception but an empty value."""
        env.setenv("BOT_TOKEN", "")

        settings = Settings()  # must not raise ValidationError

        assert settings.bot_token == ""


class TestModuleSettings:
    """The module-level instance ``bot.config.settings``."""

    def test_module_level_settings_is_settings_instance(self):
        """Specification: a module-level ``settings`` instance exists."""
        assert isinstance(config_module.settings, Settings)


class TestDotEnvFile:
    """Reading the settings from a ``.env`` file (cycle 7).

    AGENTS.md: "Secrets live only in .env" — ``BOT_TOKEN`` must be
    loadable from a ``.env`` file placed in the current working
    directory when the variable is not exported.  Each test writes its
    own file into an empty temporary directory, so the expected value
    can only come from that file.
    """

    def test_bot_token_read_from_dotenv_file_when_env_is_absent(self, env, tmp_path, monkeypatch):
        """Specification: without BOT_TOKEN in the environment the token comes from ``.env``."""
        (tmp_path / ".env").write_text("BOT_TOKEN=7000000002:AADotEnvToken\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        assert Settings().bot_token == "7000000002:AADotEnvToken"

    def test_environment_takes_precedence_over_dotenv_file(self, env, tmp_path, monkeypatch):
        """Specification: the environment wins when both sources define BOT_TOKEN.

        The file also carries ``LOG_LEVEL``, which the environment does
        not touch — asserting it proves that the file really was loaded
        and only the conflicting ``BOT_TOKEN`` was overridden by the
        environment.
        """
        (tmp_path / ".env").write_text(
            "BOT_TOKEN=TokenFromFile\nLOG_LEVEL=DEBUG\n", encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)
        env.setenv("BOT_TOKEN", "TokenFromEnvironment")

        settings = Settings()

        assert settings.bot_token == "TokenFromEnvironment"
        assert settings.log_level == "DEBUG"

    def test_log_level_read_from_dotenv_file(self, env, tmp_path, monkeypatch):
        """Specification: ``LOG_LEVEL`` is picked up from ``.env`` as well.

        ``env`` already removes LOG_LEVEL from the environment, so the
        value can only come from the file.
        """
        (tmp_path / ".env").write_text("LOG_LEVEL=DEBUG\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        assert Settings().log_level == "DEBUG"

    def test_locale_read_from_dotenv_file(self, env, tmp_path, monkeypatch):
        """Specification (i18n): ``LOCALE`` is read from ``.env`` too.

        ``env`` already removes LOCALE from the environment, so the
        value can only come from the file.
        """
        (tmp_path / ".env").write_text("LOCALE=ru\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        assert Settings().locale == "ru"


class TestLogLevelValidation:
    """I-2: ``log_level`` is normalised to a logging level name and validated."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            pytest.param("debug", "DEBUG", id="debug"),
            pytest.param("info", "INFO", id="info"),
            pytest.param("warning", "WARNING", id="warning"),
            pytest.param("error", "ERROR", id="error"),
            pytest.param("critical", "CRITICAL", id="critical"),
        ],
    )
    def test_a_lowercase_level_is_uppercased(self, env, value, expected):
        """Every lowercase level name arrives uppercased at ``settings.log_level``."""
        assert Settings(log_level=value).log_level == expected, (
            f"{value!r} must be normalised to {expected!r}"
        )

    def test_an_already_uppercase_level_passes_through(self, env):
        """An uppercased value is kept exactly as it was written."""
        assert Settings(log_level="INFO").log_level == "INFO"

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("nope", id="not-a-level-name"),
            pytest.param("", id="empty-value"),
        ],
    )
    def test_a_junk_level_is_refused(self, env, value):
        """A value that names no logging level is a configuration error."""
        with pytest.raises(ValueError):
            Settings(log_level=value)

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
PROJECT_ENV_VARS = ("BOT_TOKEN", "LOG_LEVEL")


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

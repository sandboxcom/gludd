"""Regression: alembic.ini must carry the logging sections fileConfig() needs."""
from __future__ import annotations

import configparser
from logging.config import fileConfig
from pathlib import Path

from alembic.config import Config

_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def test_alembic_ini_fileconfig_parses() -> None:
    assert _INI.exists(), _INI
    fileConfig(str(_INI))  # must not raise KeyError: 'formatters'


def test_alembic_config_has_script_location() -> None:
    cfg = Config(str(_INI))
    assert cfg.get_main_option("script_location")


def test_alembic_ini_has_logging_sections_and_keys() -> None:
    """Guard the CLI path (`uv run alembic upgrade head`): logging.config.fileConfig()
    requires [loggers]/[handlers]/[formatters] plus a logger_/handler_/formatter_
    section for every key each declares, or it raises KeyError before a single
    migration runs."""
    parser = configparser.ConfigParser()
    parser.read(str(_INI))

    assert parser.has_section("loggers")
    assert parser.has_section("handlers")
    assert parser.has_section("formatters")

    logger_keys = [k.strip() for k in parser.get("loggers", "keys").split(",") if k.strip()]
    handler_keys = [k.strip() for k in parser.get("handlers", "keys").split(",") if k.strip()]
    formatter_keys = [
        k.strip() for k in parser.get("formatters", "keys").split(",") if k.strip()
    ]

    assert logger_keys, "loggers keys must not be empty"
    assert handler_keys, "handlers keys must not be empty"
    assert formatter_keys, "formatters keys must not be empty"

    for key in logger_keys:
        assert parser.has_section(f"logger_{key}"), f"missing [logger_{key}]"
    for key in handler_keys:
        assert parser.has_section(f"handler_{key}"), f"missing [handler_{key}]"
    for key in formatter_keys:
        assert parser.has_section(f"formatter_{key}"), f"missing [formatter_{key}]"


def test_alembic_ini_url_override_documented_in_env_py() -> None:
    """The alembic.ini sqlalchemy.url is a local-dev sqlite default; env.py
    overrides it from DATABASE_URL for real deployments (see D-37). Guard that
    the documented override path still exists so the hardcoded URL in
    alembic.ini never silently becomes the prod migration target."""
    env_py = Path(__file__).resolve().parents[2] / "alembic" / "env.py"
    assert env_py.exists(), env_py
    src = env_py.read_text()
    assert "DATABASE_URL" in src
    assert "set_main_option" in src

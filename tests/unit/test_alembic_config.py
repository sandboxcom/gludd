"""Regression: alembic.ini must carry the logging sections fileConfig() needs."""
from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path

from alembic.config import Config


def test_alembic_ini_fileconfig_parses() -> None:
    ini = Path(__file__).resolve().parents[2] / "alembic.ini"
    assert ini.exists(), ini
    fileConfig(str(ini))  # must not raise KeyError: 'formatters'


def test_alembic_config_has_script_location() -> None:
    ini = Path(__file__).resolve().parents[2] / "alembic.ini"
    cfg = Config(str(ini))
    assert cfg.get_main_option("script_location")

"""Application directories (config, data, logs)."""

from __future__ import annotations

from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

from conduit import APP_NAME


def config_dir() -> Path:
    path = Path(user_config_dir(APP_NAME, appauthor=False, roaming=True))
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    path = Path(user_data_dir(APP_NAME, appauthor=False, roaming=True))
    path.mkdir(parents=True, exist_ok=True)
    return path


def connections_file() -> Path:
    return config_dir() / "connections.json"


def settings_file() -> Path:
    return config_dir() / "settings.json"


def known_hosts_file() -> Path:
    return data_dir() / "known_hosts"

"""Config + environment loading. Single source of truth: config.yaml."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent.parent   # ~/trader


def load_config(path: str | None = None) -> dict:
    load_dotenv(ROOT / ".env")
    cfg_path = Path(path) if path else ROOT / "config.yaml"
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    # Installation locations may differ after copying the release. These
    # overrides do not alter trading policy, credentials or recorded evidence.
    for variable, section, key in (
        ('LUFFY_DASHBOARD_HOST', 'dashboard', 'host'),
        ('LUFFY_OWNER_IPC_DIR', 'owner_interface', 'ipc_dir'),
        ('LUFFY_RECOVERY_ROOT', 'deployment', 'recovery_root'),
    ):
        if variable in os.environ:
            value = os.environ[variable].strip()
            if not value:
                raise ValueError(variable + ' must not be empty')
            cfg.setdefault(section, {})[key] = value
    return cfg


class Env:
    """Secrets from .env — never logged, never journaled."""
    _cache: dict | None = None

    @classmethod
    def get(cls, key: str, default: str = "") -> str:
        if cls._cache is None:
            load_dotenv(ROOT / ".env")
            cls._cache = {}
        return os.environ.get(key, default)

    @classmethod
    def binance_keys(cls) -> tuple[str, str]:
        return (cls.get("BINANCE_API_KEY"), cls.get("BINANCE_SECRET_KEY"))

    @classmethod
    def deepseek_key(cls) -> str:
        return cls.get("DEEPSEEK_API_KEY")

    @classmethod
    def telegram(cls) -> tuple[str, str]:
        return (cls.get("TELEGRAM_TOKEN"), cls.get("TELEGRAM_CHAT_ID"))

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

"""Загрузка и проверка переменных окружения. Секреты только из .env."""

from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    openai_api_key: str
    openai_model: str
    contact_telegram_username: str
    admin_telegram_id: int | None

    @property
    def contact_url(self) -> str:
        u = self.contact_telegram_username.strip().lstrip("@")
        return f"https://t.me/{u}" if u else ""


def _req(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise RuntimeError(f"Отсутствует обязательная переменная окружения: {name}")
    return v


def _optional_admin_telegram_id() -> int | None:
    raw = os.getenv("ADMIN_TELEGRAM_ID", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def load_settings() -> Settings:
    return Settings(
        telegram_bot_token=_req("TELEGRAM_BOT_TOKEN"),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
        contact_telegram_username=os.getenv("CONTACT_TELEGRAM_USERNAME", "").strip(),
        admin_telegram_id=_optional_admin_telegram_id(),
    )

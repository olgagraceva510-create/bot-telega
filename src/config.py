"""Загрузка и проверка переменных окружения. Секреты только из .env."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    openai_api_key: str
    openai_model: str
    contact_telegram_username: str

    @property
    def contact_url(self) -> str:
        u = self.contact_telegram_username.strip().lstrip("@")
        return f"https://t.me/{u}" if u else ""


def _req(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise RuntimeError(f"Отсутствует обязательная переменная окружения: {name}")
    return v


def load_settings() -> Settings:
    return Settings(
        telegram_bot_token=_req("TELEGRAM_BOT_TOKEN"),
        openai_api_key=_req("OPENAI_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
        contact_telegram_username=os.getenv("CONTACT_TELEGRAM_USERNAME", "").strip(),
    )

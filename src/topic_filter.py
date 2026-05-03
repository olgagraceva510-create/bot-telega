"""Локальная проверка: отклонять только явно посторонние темы."""

from __future__ import annotations

import re
from typing import Any

# Короткие ответы в рамках диалога (стили, типы, сроки) — не блокировать
_SHORT_EXACT = frozenset(
    {
        "да",
        "нет",
        "ок",
        "yes",
        "no",
        "ага",
        "угу",
    }
)
_SHORT_SUBSTR = (
    "минимализм",
    "премиум",
    "яркий",
    "спокойный",
    "другой",
    "лендинг",
    "визитка",
    "интернет-магазин",
    "интернет магазин",
    "нужно быстро",
)

# Явно посторонние темы (подстрока в нормализованном тексте с пробелами)
_OFF_TOPIC_PHRASES = (
    " погода ",
    " прогноз погод",
    " рецепт",
    " политик",
    " новости ",
    " математик",
    " медицин",
    " бытов",
    " философ",
)


def _likely_site_discussion(text: str) -> bool:
    """Если в сообщении явно про сайт — не блокировать по маркерам вроде «политика» (политика ПДн)."""
    h = text.casefold()
    hints = (
        "сайт",
        "лендинг",
        "дизайн",
        "верстк",
        "хостинг",
        "домен",
        "seo",
        "страниц",
        "блок",
        "портфолио",
        "магазин",
    )
    return any(x in h for x in hints)


def _normalize_pad(text: str) -> str:
    return " " + re.sub(r"\s+", " ", text.strip().casefold()) + " "


def _short_reply_allowed(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    cf = t.casefold()
    if len(cf) <= 2:
        return True
    if cf in _SHORT_EXACT:
        return True
    for frag in _SHORT_SUBSTR:
        if frag in cf:
            return True
    if re.search(r"\d+\s*страниц", cf):
        return True
    return False


def _explicit_off_topic(text: str) -> bool:
    if _likely_site_discussion(text):
        return False
    pad = _normalize_pad(text)
    return any(p in pad for p in _OFF_TOPIC_PHRASES)


def _last_bot_was_clarifying_question(
    prior_messages: list[dict[str, Any]],
    local_refusal_body: str,
) -> bool:
    if not prior_messages or prior_messages[-1].get("role") != "assistant":
        return False
    content = (prior_messages[-1].get("content") or "").strip()
    if content == local_refusal_body.strip():
        return False
    return "?" in content


def should_block_out_topic(
    user_text: str,
    prior_messages: list[dict[str, Any]],
    local_refusal_body: str,
) -> bool:
    """
    True — показать локальный отказ без OpenAI.
    False — пропустить дальше по цепочке (OpenAI и т.д.).
    """
    if _last_bot_was_clarifying_question(prior_messages, local_refusal_body):
        return False
    if _short_reply_allowed(user_text):
        return False
    return _explicit_off_topic(user_text)

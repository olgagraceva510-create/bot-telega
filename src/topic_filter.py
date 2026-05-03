"""Локальная проверка: сообщение относится к теме создания сайтов."""

from __future__ import annotations

_SITE_KEYWORDS = (
    "сайт",
    "лендинг",
    "дизайн",
    "структура",
    "блоки",
    "разработка",
    "портфолио",
    "интернет-магазин",
    "интернет магазин",
    "форма заявки",
    "домен",
    "хостинг",
    "seo",
    "стоимость сайта",
    "переделать сайт",
    "создать сайт",
)


def is_site_related(text: str) -> bool:
    t = text.strip()
    if len(t) <= 2:
        return True
    hay = t.casefold()
    for kw in _SITE_KEYWORDS:
        if kw.casefold() in hay:
            return True
    return False

"""Вызов OpenAI Chat Completions (асинхронно)."""

from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI

from src.config import Settings


async def chat_completion(
    settings: Settings,
    messages: list[dict[str, Any]],
) -> str:
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    resp = await client.chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        temperature=0.6,
        max_tokens=900,
    )
    choice = resp.choices[0]
    content = choice.message.content or ""
    return content.strip()

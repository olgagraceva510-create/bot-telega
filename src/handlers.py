"""Обработчики Telegram: один ответ на сообщение, контекст, «печатает»."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from src.config import Settings
from src.conversation_store import ConversationStore
from src.llm import chat_completion
from src.system_prompt import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_chat_locks: dict[int, asyncio.Lock] = {}


def _contact_keyboard(settings: Settings) -> InlineKeyboardMarkup | None:
    if not settings.contact_url:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Связаться с Ольгой", url=settings.contact_url)]]
    )


def _get_lock(chat_id: int) -> asyncio.Lock:
    if chat_id not in _chat_locks:
        _chat_locks[chat_id] = asyncio.Lock()
    return _chat_locks[chat_id]


def build_openai_messages(
    settings: Settings,
    store: ConversationStore,
    chat_id: int,
) -> list[dict]:
    base: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if settings.contact_url:
        base.append(
            {
                "role": "system",
                "content": (
                    f"Ссылка для клиента на прямой контакт с Ольгой: {settings.contact_url}. "
                    "Кнопка «Связаться с Ольгой» уже показывается под ответами."
                ),
            }
        )
    return base + store.get_messages(chat_id)


async def cmd_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    settings: Settings = context.bot_data["settings"]
    store: ConversationStore = context.bot_data["store"]
    chat_id = update.effective_chat.id
    store.clear(chat_id)

    text = (
        "Здравствуйте! 👋\n\n"
        "Вы написали после отправки брифа с сайта — заявка уже у нас. "
        "Здесь можно спокойно уточнить детали и задать вопросы по будущему сайту.\n\n"
        "Напишите, что для вас сейчас важнее всего: тип сайта, структура, примеры, материалы — "
        "разберём по шагам. Точные сроки и стоимость согласуете с Ольгой после консультации."
    )
    await update.message.reply_text(
        text,
        reply_markup=_contact_keyboard(settings),
    )


async def cmd_reset(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    store: ConversationStore = context.bot_data["store"]
    chat_id = update.effective_chat.id
    store.clear(chat_id)
    await update.message.reply_text(
        "Контекст диалога сброшен. Можем начать заново — напишите, чем помочь."
    )


async def on_text_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.message or not update.message.text:
        return

    settings: Settings = context.bot_data["settings"]
    store: ConversationStore = context.bot_data["store"]
    chat_id = update.effective_chat.id
    user_text = update.message.text.strip()
    if not user_text:
        return

    lock = _get_lock(chat_id)
    async with lock:
        store.append_user(chat_id, user_text)

        async def _typing() -> None:
            try:
                while True:
                    await context.bot.send_chat_action(
                        chat_id=chat_id,
                        action=ChatAction.TYPING,
                    )
                    await asyncio.sleep(4)
            except asyncio.CancelledError:
                return

        typing_task = asyncio.create_task(_typing())
        try:
            messages = build_openai_messages(settings, store, chat_id)
            reply = await chat_completion(settings, messages)
        except Exception:
            logger.exception("OpenAI request failed for chat_id=%s", chat_id)
            reply = (
                "Сейчас не удалось получить ответ от ассистента. Попробуйте ещё раз через минуту "
                "или напишите Ольге напрямую — ссылка в кнопке ниже."
            )
        finally:
            typing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing_task

        store.append_assistant(chat_id, reply)
        await update.message.reply_text(
            reply,
            reply_markup=_contact_keyboard(settings),
        )

"""Обработчики Telegram: один ответ на сообщение, контекст, «печатает»."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import Application, ContextTypes

from src.config import Settings
from src.conversation_store import ConversationStore
from src.llm import chat_completion
from src.system_prompt import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_chat_locks: dict[int, asyncio.Lock] = {}

_BRIEF_Q1 = (
    "Чтобы я лучше понял задачу, ответьте на несколько вопросов.\n\n"
    "1️⃣ Какой сайт нужен?\n"
    "— лендинг\n"
    "— сайт услуг\n"
    "— интернет-магазин\n"
    "— не знаю, нужна консультация"
)
_BRIEF_Q2 = "2️⃣ Есть ли уже сайт или начинаем с нуля?"
_BRIEF_Q3 = "3️⃣ Когда примерно нужен сайт?"
_BRIEF_DONE = (
    "Спасибо! Я передам ответы Ольге. Если хотите, можно сразу нажать кнопку "
    "«Связаться с Ольгой»."
)

_SCENARIO_BY_CALLBACK: dict[str, tuple[str, str]] = {
    "sc:new": (
        "Создать новый сайт",
        "Отлично. Расскажите, для какого проекта нужен сайт и какая у него главная задача.",
    ),
    "sc:redesign": (
        "Переделать сайт",
        "Понял. Опишите, что сейчас не устраивает в сайте и что хочется улучшить.",
    ),
    "sc:structure": (
        "Структура сайта",
        "Могу помочь со структурой. Напишите нишу или сферу проекта.",
    ),
    "sc:design": (
        "Обсудить дизайн",
        "Расскажите, какой стиль вам ближе: минимализм, премиум, яркий, спокойный или другой.",
    ),
    "sc:price": (
        "Оценить стоимость",
        "Точную стоимость лучше согласовать с Ольгой, но я могу помочь понять примерный объём работ. "
        "Напишите, какой сайт нужен.",
    ),
}


def _scenario_start_keyboard(settings: Settings) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("Создать новый сайт", callback_data="sc:new")],
        [InlineKeyboardButton("Переделать сайт", callback_data="sc:redesign")],
        [InlineKeyboardButton("Структура сайта", callback_data="sc:structure")],
        [InlineKeyboardButton("Обсудить дизайн", callback_data="sc:design")],
        [InlineKeyboardButton("Оценить стоимость", callback_data="sc:price")],
    ]
    if settings.contact_url:
        rows.append(
            [InlineKeyboardButton("Связаться с Ольгой", url=settings.contact_url)]
        )
    return InlineKeyboardMarkup(rows)


def _contact_keyboard(settings: Settings) -> InlineKeyboardMarkup | None:
    if not settings.contact_url:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Связаться с Ольгой", url=settings.contact_url)]]
    )


async def _notify_admin_new_user_message(
    application: Application,
    settings: Settings,
    user,
    text: str,
) -> None:
    if settings.admin_telegram_id is None or user is None:
        return
    if user.id == settings.admin_telegram_id:
        return
    username_line = (
        f"Username: @{user.username}" if user.username else "Username: —"
    )
    admin_text = (
        "Новое обращение в боте\n\n"
        f"Имя: {user.full_name}\n"
        f"{username_line}\n"
        f"User ID: {user.id}\n\n"
        "Сообщение:\n"
        f"{text}"
    )
    try:
        await application.bot.send_message(
            chat_id=settings.admin_telegram_id,
            text=admin_text,
        )
    except Exception:
        logger.exception("Не удалось отправить уведомление администратору")


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
        "Я помогу разобраться с созданием сайта: структура, функции, примеры и основные этапы.\n\n"
        "Напишите, что сейчас важнее всего:\n"
        "— создать новый сайт\n"
        "— переделать существующий\n"
        "— понять структуру и функции\n"
        "— обсудить дизайн\n"
        "— оценить примерный объём работ\n\n"
        "Можно написать в свободной форме — я задам уточняющие вопросы."
    )
    await update.message.reply_text(
        text,
        reply_markup=_scenario_start_keyboard(settings),
    )
    notify_text = update.message.text if update.message and update.message.text else "/start"
    await _notify_admin_new_user_message(
        context.application,
        settings,
        update.effective_user,
        notify_text.strip(),
    )
    context.bot_data.setdefault("brief_step", {})[chat_id] = 0


async def on_scenario_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not query or not query.data or not query.message:
        return
    entry = _SCENARIO_BY_CALLBACK.get(query.data)
    if not entry:
        await query.answer()
        return

    label, reply_body = entry
    settings: Settings = context.bot_data["settings"]
    store: ConversationStore = context.bot_data["store"]
    chat_id = query.message.chat_id
    user = query.from_user

    await query.answer()

    lock = _get_lock(chat_id)
    async with lock:
        store.append_user(chat_id, label)
        brief_steps = context.bot_data.setdefault("brief_step", {})
        if query.data == "sc:new":
            reply_to_user = f"{reply_body}\n\n{_BRIEF_Q1}"
            store.append_assistant(chat_id, reply_to_user)
            brief_steps[chat_id] = 1
        else:
            reply_to_user = reply_body
            store.append_assistant(chat_id, reply_body)
            brief_steps[chat_id] = 0
        await _notify_admin_new_user_message(
            context.application, settings, user, label
        )

    await query.message.reply_text(
        reply_to_user,
        reply_markup=_contact_keyboard(settings),
    )


async def cmd_reset(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    store: ConversationStore = context.bot_data["store"]
    chat_id = update.effective_chat.id
    store.clear(chat_id)
    context.bot_data.setdefault("brief_step", {})[chat_id] = 0
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

    user = update.effective_user

    lock = _get_lock(chat_id)
    async with lock:
        brief_steps = context.bot_data.setdefault("brief_step", {})
        step = brief_steps.get(chat_id, 0)
        if step in (1, 2, 3):
            store.append_user(chat_id, user_text)
            await _notify_admin_new_user_message(
                context.application, settings, user, user_text
            )
            if step == 1:
                await update.message.reply_text(
                    _BRIEF_Q2,
                    reply_markup=_contact_keyboard(settings),
                )
                brief_steps[chat_id] = 2
            elif step == 2:
                await update.message.reply_text(
                    _BRIEF_Q3,
                    reply_markup=_contact_keyboard(settings),
                )
                brief_steps[chat_id] = 3
            else:
                brief_steps[chat_id] = 0
                await update.message.reply_text(
                    _BRIEF_DONE,
                    reply_markup=_contact_keyboard(settings),
                )
            return

        store.append_user(chat_id, user_text)

        if not settings.openai_api_key:
            reply = "OpenAI API key не настроен"
            store.append_assistant(chat_id, reply)
            await update.message.reply_text(
                reply,
                reply_markup=_contact_keyboard(settings),
            )
            await _notify_admin_new_user_message(
                context.application, settings, user, user_text
            )
            return

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
        await _notify_admin_new_user_message(
            context.application, settings, user, user_text
        )

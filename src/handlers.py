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
from src.topic_filter import should_block_out_topic

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
_OFF_TOPIC_LOCAL_REPLY = (
    "Я могу помочь только с вопросами, связанными с созданием сайтов. "
    "Если у вас есть вопрос по проекту или идее сайта — напишите, и я постараюсь помочь."
)
_OPENAI_FAILURE_USER_MESSAGE = (
    "Извините, сейчас не удалось обработать ответ. Попробуйте ещё раз или нажмите кнопку ниже."
)
_BRIEF_OPENAI_HINTS = {
    1: (
        "Сейчас пользователь отвечает на первый вопрос мини-опроса о типе сайта. Учтите ответ, "
        "продолжайте консультацию по созданию сайта, не обрывайте сценарий; при необходимости "
        "уточните детали или предложите варианты."
    ),
    2: (
        "Пользователь отвечает на вопрос: есть ли уже сайт или начинаем с нуля. Учтите ответ, "
        "продолжайте диалог естественно."
    ),
    3: (
        "Пользователь отвечает на вопрос о сроках. Учтите ответ, помогите сориентироваться без "
        "точных обещаний по срокам и стоимости — их согласует Ольга."
    ),
}

_SCENARIO_BY_CALLBACK: dict[str, tuple[str, str]] = {
    "sc:layout": (
        "Оформление сайта",
        "Понял. Расскажите, какой сайт планируется и что именно хотите «оформить»: тексты, структура, блоки, "
        "примеры и референсы?",
    ),
    "sc:design": (
        "Дизайн сайта",
        "Расскажите, какой стиль вам ближе: минимализм, премиум, яркий, спокойный или другой. Есть примеры сайтов, "
        "которые нравятся?",
    ),
    "sc:price": (
        "Стоимость",
        "По стоимости важно уточнить объём. Напишите, какой сайт нужен (визитка, лендинг, многостраничный) и "
        "нужны ли дополнительные функции: формы, SEO, интеграции, бот?",
    ),
    "sc:scope": (
        "Что входит в работу",
        "Обычно это бриф → обсуждение → структура → дизайн → сборка → запуск. Напишите, вам нужен только дизайн "
        "или сайт под ключ?",
    ),
    "sc:project": (
        "Обсудить проект",
        "Отлично. Расскажите, для какого проекта нужен сайт и какая у него главная задача.",
    ),
}

_BRIEF_FORM_URL = "https://anketa-site.ru"


def _scenario_start_keyboard(settings: Settings) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("Оформление сайта", callback_data="sc:layout")],
        [InlineKeyboardButton("Дизайн сайта", callback_data="sc:design")],
        [InlineKeyboardButton("Стоимость", callback_data="sc:price")],
        [InlineKeyboardButton("Что входит в работу", callback_data="sc:scope")],
        [InlineKeyboardButton("Обсудить проект", url="https://anketa-site.ru")],
        [InlineKeyboardButton("Заполнить заявку", url=_BRIEF_FORM_URL)],
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
    brief_step: int = 0,
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
    if brief_step in _BRIEF_OPENAI_HINTS:
        base.append({"role": "system", "content": _BRIEF_OPENAI_HINTS[brief_step]})
    return base + store.get_messages(chat_id)


def _log_openai_failure(settings: Settings, exc: BaseException) -> None:
    typ = type(exc).__name__
    err = str(exc)
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    response = getattr(exc, "response", None)
    logger.error(
        "OpenAI сбой: type=%s status=%s model=%s error=%s body=%s response=%r",
        typ,
        status,
        settings.openai_model,
        err,
        body,
        response,
        exc_info=exc,
    )
    low = err.casefold()
    st = str(status) if status is not None else ""
    if st == "401" or "invalid_api_key" in low or "authentication" in typ.casefold():
        logger.error("Подсказка: проверьте OPENAI_API_KEY.")
    if st == "429" or "rate" in low:
        logger.error("Подсказка: превышен лимит запросов (rate limit).")
    if st == "402" or "insufficient_quota" in low or "quota" in low or "billing" in low:
        logger.error("Подсказка: биллинг или баланс OpenAI.")
    if "model" in low and ("not found" in low or "does not exist" in low or "invalid" in low):
        logger.error("Подсказка: проверьте OPENAI_MODEL.")


async def cmd_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    settings: Settings = context.bot_data["settings"]
    store: ConversationStore = context.bot_data["store"]
    chat_id = update.effective_chat.id
    store.clear(chat_id)

    text = (
        "Здравствуйте! Я помогу сориентироваться по созданию сайта: оформление, дизайн, структура, "
        "стоимость и запуск. Выберите, что вас интересует:"
    )

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Оформление сайта", callback_data="sc:design")],
            [InlineKeyboardButton("Дизайн сайта", callback_data="sc:ui")],
            [InlineKeyboardButton("Стоимость", callback_data="sc:price")],
            [InlineKeyboardButton("Что входит в работу", callback_data="sc:what")],
            [InlineKeyboardButton("Обсудить проект", url="https://anketa-site.ru")],
        ]
    )
    await update.message.reply_text(
        text,
        reply_markup=keyboard,
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
    settings: Settings = context.bot_data["settings"]
    store: ConversationStore = context.bot_data["store"]
    chat_id = update.effective_chat.id
    user = update.effective_user

    if not update.message:
        return

    if not update.message.text:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text="Пожалуйста, отправьте обычное текстовое сообщение — так я смогу помочь.",
                reply_markup=_contact_keyboard(settings),
            )
        except Exception:
            logger.exception("Не удалось ответить на нетекстовое сообщение chat_id=%s", chat_id)
        return

    user_text = update.message.text.strip()
    if not user_text:
        try:
            await update.message.reply_text(
                "Сообщение пустое. Напишите, пожалуйста, ваш вопрос текстом.",
                reply_markup=_contact_keyboard(settings),
            )
        except Exception:
            logger.exception("Не удалось ответить на пустое сообщение chat_id=%s", chat_id)
        return

    lock = _get_lock(chat_id)
    async with lock:
        brief_steps = context.bot_data.setdefault("brief_step", {})
        step = brief_steps.get(chat_id, 0)
        prior_messages = store.get_messages(chat_id)
        store.append_user(chat_id, user_text)

        if should_block_out_topic(user_text, prior_messages, _OFF_TOPIC_LOCAL_REPLY):
            store.append_assistant(chat_id, _OFF_TOPIC_LOCAL_REPLY)
            await update.message.reply_text(
                _OFF_TOPIC_LOCAL_REPLY,
                reply_markup=_contact_keyboard(settings),
            )
            await _notify_admin_new_user_message(
                context.application, settings, user, user_text
            )
            return

        if not settings.openai_api_key:
            if step in (1, 2, 3):
                await _notify_admin_new_user_message(
                    context.application, settings, user, user_text
                )
                if step == 1:
                    store.append_assistant(chat_id, _BRIEF_Q2)
                    await update.message.reply_text(
                        _BRIEF_Q2,
                        reply_markup=_contact_keyboard(settings),
                    )
                    brief_steps[chat_id] = 2
                elif step == 2:
                    store.append_assistant(chat_id, _BRIEF_Q3)
                    await update.message.reply_text(
                        _BRIEF_Q3,
                        reply_markup=_contact_keyboard(settings),
                    )
                    brief_steps[chat_id] = 3
                else:
                    brief_steps[chat_id] = 0
                    store.append_assistant(chat_id, _BRIEF_DONE)
                    await update.message.reply_text(
                        _BRIEF_DONE,
                        reply_markup=_contact_keyboard(settings),
                    )
                return

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
        reply: str
        openai_ok = False
        try:
            messages = build_openai_messages(settings, store, chat_id, brief_step=step)
            reply = await chat_completion(settings, messages)
            openai_ok = True
        except Exception as exc:
            _log_openai_failure(settings, exc)
            reply = _OPENAI_FAILURE_USER_MESSAGE
        finally:
            typing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing_task

        store.append_assistant(chat_id, reply)
        if openai_ok and step in (1, 2, 3):
            brief_steps[chat_id] = 0 if step == 3 else step + 1

        await update.message.reply_text(
            reply,
            reply_markup=_contact_keyboard(settings),
        )
        await _notify_admin_new_user_message(
            context.application, settings, user, user_text
        )

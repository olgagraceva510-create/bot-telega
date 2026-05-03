"""Точка входа: polling, без секретов в коде."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from src.config import load_settings
from src.conversation_store import ConversationStore
from src.handlers import cmd_reset, cmd_start, on_scenario_callback, on_text_message

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main() -> None:
    settings = load_settings()
    if not settings.contact_telegram_username:
        logger.warning(
            "CONTACT_TELEGRAM_USERNAME не задан — кнопка «Связаться с Ольгой» не будет показана."
        )

    application = Application.builder().token(settings.telegram_bot_token).build()
    application.bot_data["settings"] = settings
    application.bot_data["store"] = ConversationStore()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("reset", cmd_reset))
    application.add_handler(CallbackQueryHandler(on_scenario_callback, pattern=r"^sc:"))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_message),
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()

"""История диалога по chat_id (в памяти процесса)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

# Максимум пар сообщений (user+assistant), чтобы не раздувать контекст
_MAX_TURNS = 24


class ConversationStore:
    def __init__(self) -> None:
        self._messages: dict[int, list[dict[str, Any]]] = defaultdict(list)

    def append_user(self, chat_id: int, text: str) -> None:
        self._messages[chat_id].append({"role": "user", "content": text})
        self._trim(chat_id)

    def append_assistant(self, chat_id: int, text: str) -> None:
        self._messages[chat_id].append({"role": "assistant", "content": text})
        self._trim(chat_id)

    def get_messages(self, chat_id: int) -> list[dict[str, Any]]:
        return list(self._messages[chat_id])

    def clear(self, chat_id: int) -> None:
        self._messages[chat_id].clear()

    def _trim(self, chat_id: int) -> None:
        msgs = self._messages[chat_id]
        # Храним не больше _MAX_TURNS * 2 реплик (user/assistant)
        max_len = _MAX_TURNS * 2
        if len(msgs) > max_len:
            self._messages[chat_id] = msgs[-max_len:]

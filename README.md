# bot-telega

Telegram-бот для консультации клиентов после отправки брифа на сайте (контекст диалога, кнопка связи, индикатор набора текста).

## Запуск

```bash
cd bot-telega
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Скопируйте `.env.example` в `.env`, заполните ключи, затем:

```bash
python main.py
```

Команда `/reset` сбрасывает историю диалога в этом чате.

## Структура

| Путь | Назначение |
|------|------------|
| `main.py` | Запуск polling, регистрация хендлеров |
| `src/config.py` | Загрузка `.env`, проверка обязательных переменных |
| `src/conversation_store.py` | История сообщений по `chat_id` (в памяти) |
| `src/system_prompt.py` | Системный промпт (роль, этапы, ограничения) |
| `src/llm.py` | Запрос к OpenAI Chat Completions |
| `src/handlers.py` | `/start`, текстовые сообщения, «печатает», кнопка |
| `.env.example` | Шаблон переменных окружения |

Секреты не храните в коде и не коммитьте файл `.env`.

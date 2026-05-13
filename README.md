# WS Router — Mattermost → OpenClaw (1 user = 1 instance)

WebSocket Router: двусторонний канал между Mattermost и персональными OpenClaw instances.

## Как это работает

```
Пользователь → Mattermost WS → Router → persistent WS → OpenClaw instance
                                                ↑
                        OpenClaw (проактивно) ──┘ → Mattermost (напоминания, алерты)
                        OpenClaw (cron job)   ──┘ → Mattermost (напоминания из cron)
```

**Ключевые принципы:**
- **1 user = 1 OpenClaw instance** — строгая изоляция на уровне WS-соединений
- **Persistent WS** — соединение держится постоянно, не пересоздаётся на каждый запрос
- **Проактивные сообщения** — OpenClaw может сам инициировать отправку (heartbeat, напоминания)
- **Cron-уведомления** — задачи из планировщика OpenClaw доставляются через `payload.summary`
- **Auto-reconnect** — guardian task на каждого пользователя восстанавливает соединение при разрыве
- **Typing indicator** — индикатор "Bot is typing..." в Mattermost обновляется каждые 4 сек во время генерации ответа
- **Statuses & Placeholder** — вместо посимвольного стриминга (который отключен для стабильности) бот моментально присылает заглушку (например, "💭 Думаю..."), которая подменяется на финальный ответ по завершении.
- **Bidirectional Media** — полная поддержка пересылки файлов от пользователя (попадают в `workspace/downloads/`) и доставки сгенерированных агентом файлов из папок `workspace` и `canvas` обратно в Mattermost как нативных вложений.

---

## Быстрый старт (сервер)

### Структура на сервере

```
/root/openclaw/
├── deploy/          ← openclaw-deploy (docker-compose, gateway инстансы)
├── user-configs/    ← данные инстансов пользователей (по UUID)
├── root-config/     ← шаблон openclaw.json
├── postgres/        ← конфиги PostgreSQL
└── ws-router/       ← этот репозиторий
```

### 1. Создать БД (один раз)

```bash
docker exec openclaw-postgres psql -U openclaw -d openclaw_registry -c "CREATE DATABASE ws_router;"
```

### 2. Запустить

```bash
cd ~/openclaw/ws-router
docker compose up -d --build
```

Контейнер автоматически накатит миграции и запустит сервис.

### 3. Проверить

```bash
docker logs ws_router -f         # логи
curl http://localhost:8060/health # health check
```

---

## Добавление пользователя — `provision_instance.py`

**Это основной инструмент для onboarding новых пользователей.** Скрипт делает всё за один запуск:

1. Генерирует Ed25519 ключи + device token
2. Создаёт `devices/paired.json` и `devices/pending.json` в директории инстанса
3. Устанавливает права `1000:1000` (node user внутри контейнера)
4. Записывает строку в PostgreSQL (`user_instance`)

→ [Подробная документация по provision_instance.py](scripts/PROVISION.md)

### Предварительные условия

Перед запуском скрипта контейнер `openclaw-gw-<UUID>` должен быть **уже создан** (но не обязательно запущен), и директория `/root/openclaw/user-configs/<UUID>/` с `openclaw.json` должна существовать.

### Минимальный вызов

```bash
python scripts/provision_instance.py \
    --user-id <MATTERMOST_USER_ID> \
    --uuid <UUID> \
    --gateway-token <OPENCLAW_GATEWAY_TOKEN>
```

После успеха — перезапустить роутер:

```bash
docker restart ws_router
```

---

## Создание нового OpenClaw instance

Каждый пользователь = свой контейнер. Добавить в `docker-compose.yml`:

```yaml
  openclaw-gw-<UUID>:
    image: ghcr.io/openclaw/openclaw:2026.4.15
    container_name: openclaw-gw-<UUID>
    environment:
      HOME: /home/node
      OPENCLAW_GATEWAY_TOKEN: <уникальный-токен>
      OPENCLAW_ALLOW_INSECURE_PRIVATE_WS: 1
      VLLM_API_KEY: sk-local
      VLLM_BASE_URL: http://vllm:8000/v1
      TZ: Europe/Moscow
    volumes:
      - /root/openclaw/user-configs/<UUID>:/home/node/.openclaw
      - /root/openclaw/user-configs/<UUID>/workspace:/home/node/.openclaw/workspace
    command: ["node", "dist/index.js", "gateway", "--bind", "lan", "--port", "18789"]
    restart: unless-stopped
    networks:
      - ai-network
```

> ⚠️ В `openclaw.json` инстанса **убрать секцию `channels.mattermost`** — маршрутизацией занимается WS Router.

**Что уникально на каждый инстанс:**

| Параметр | Пример |
|----------|--------|
| service name | `openclaw-gw-30f2aeff-...` |
| `container_name` | `openclaw-gw-30f2aeff-...` |
| `OPENCLAW_GATEWAY_TOKEN` | уникальный токен |
| volumes path | `/root/openclaw/user-configs/30f2aeff-...` |

**Что не менять:** порт `18789` (внутри контейнера, конфликта нет), `ai-network`, `image`.

---

## Архитектура и модули

### Поток данных

```
Mattermost WS
    │ posted event
    ▼
MattermostClient._ws_listen_loop()
    │ MattermostEvent
    ▼
Router.handle_event()
    ├─ update _user_channels[user_id] = channel_id
    ├─ send_reply("💭 Думаю...")       ← placeholder (рандомизированные статусы)
    ├─ start _typing_loop()            ← "Bot is typing..." каждые 4 сек
    ├─ ws_manager.send_message(session_key="mm:chan:{channel_id}")
    │       └─ OpenClawClient.send_message()
    │               ├─ chat.send → OpenClaw WS
    │               ├─ перехват файлов → извлечение медиа путей и их удаление из текста
    │               └─ asyncio.Future ← chat.final из _listen_loop()
    ├─ file_manager.upload_to_mattermost() ← если агент сгенерировал файлы
    └─ mattermost.update_reply(placeholder_id, response)  ← финальный ответ + файлы

OpenClaw (heartbeat / proactive)
    │ ws_event=chat, state=final, нет активного request
    ▼
OpenClawClient._on_proactive(text)
    ▼
WSConnectionManager → Router.handle_proactive(user_id, text)
    ▼
MattermostClient.send_reply(last_known_channel_id, text)
    │ если channel_id неизвестен → get_or_create_dm_channel(user_id)

OpenClaw (cron scheduler)
    │ ws_event=cron, payload.summary = "текст напоминания"
    ▼
OpenClawClient._dispatch() → _on_proactive(payload.summary)
    ▼
WSConnectionManager → Router.handle_proactive(user_id, summary)
    ▼
MattermostClient.send_reply(last_known_channel_id, summary)
```

---

## ⚠️ Известные баги шлюза OpenClaw и воркараунды

### Баг: Обрезание нулей в числах (Gateway Normalizer Bug)

**Симптом:** Числа в ответах LLM приходят с обрезанными нулями.  
Например: `13 480 000 ₽` → `13 480 0 ₽`, `55 000 000 ₽` → `5 0 ₽`.

**Причина:** OpenClaw Gateway при нормализации `chat` событий применяет внутренний пост-процессинг к тексту, который повреждает числа с повторяющимися символами в Markdown-таблицах. Нативный Mattermost-плагин эту проблему не имеет, так как читает сырой `agent` поток напрямую.

**Дополнительная проблема:** В нормализованный `chat.final` шлюз вклеивает внутренние мысли LLM (reasoning), которые не должны отображаться пользователю.

**Решение (гибридный подход):**

```
agent stream (stream="assistant") ──► _agent_texts[runId] = text (чистый текст)
                                                                         │
chat stream (state="final")       ──► агрегатор ←── подмена text ────────┘
                                           │
                                      on_final → Mattermost (чистый текст)
```

Роутер слушает **оба** потока одновременно:
1. **`agent` поток** (`ws_event=agent, stream=assistant`) — содержит необработанный, точный текст без артефактов. Буферируется в `_agent_texts[runId]`.
2. **`chat` поток** (`ws_event=chat, state=final`) — используется как **сигнал завершения** генерации. Его текст **заменяется** на текст из `_agent_texts`.

Реализовано в `src/openclaw_client.py` → `_dispatch()`.

**Когда это может сломаться:** Если OpenClaw изменит формат `agent` событий или уберёт `stream=assistant`. Проверить можно через `RAW_WS_DUMP=1` (см. ниже).

---

### Изоляция контекста LLM по каналам

**Проблема:** Ранее использовался глобальный ключ сессии `agent:main:main` для всех запросов. Из-за этого история контекста неограниченно росла, и время ответа деградировало с 4 сек до 70+ сек.

**Решение:** Каждый Mattermost-канал получает свой ключ сессии:
```python
session_key = f"mm:chan:{channel_id}"
```

Это даёт каждому чату **изолированную историю** с LLM. Контекст не растёт бесконечно.

---

### Типы WS-событий от OpenClaw

| `ws_event` | `stream` / `state` | Обработка | Описание |
|---|---|---|---|
| `agent` | `stream=assistant` | Буферируется в `_agent_texts` | Сырой текст LLM (без артефактов) |
| `agent` | другие streams | Игнорируется | Lifecycle, tool events |
| `chat` | `state=partial` | Игнорируется (`on_stream=None`) | Стриминг отключен для стабильности UI |
| `chat` | `state=final` | Агрегатор → финальный ответ | Сигнал завершения генерации |
| `cron` | без `action=removed` | `payload.summary` → `_on_proactive` | Уведомление планировщика |
| `cron` | `action=removed/finished` | Игнорируется | Cleanup одноразовой задачи |
| `health` | — | Игнорируется (SKIP) | Ping-ответ инстанса |
| `tick` | — | Игнорируется (SKIP) | Системный тик |
| `heartbeat` | — | Игнорируется (SKIP) | Heartbeat |
| `presence` | — | Игнорируется (SKIP) | Статус присутствия |

---

## Модули (Архитектура)

Проект имеет четкую структуру для разделения ответственности:

```text
src/
├── api/
│   └── trigger.py           # Control-Plane API (POST /api/v1/trigger)
├── core/
│   ├── config.py            # Конфигурация из env vars (Pydantic)
│   ├── database.py          # Async PostgreSQL engine (asyncpg)
│   └── models.py            # SQLAlchemy модель user_instance
├── services/
│   ├── file_manager.py      # Двусторонняя передача файлов (Mattermost ↔ Shared Volume)
│   ├── mapping.py           # Lookup БД: user_id/crm_id → instance_url + credentials
│   ├── mattermost.py        # Mattermost WS listener + HTTP API sender
│   ├── openclaw_client.py   # Persistent WS клиент: Ed25519 auth, dual-stream, cron, aggregator
│   └── ws_manager.py        # Connection pool: guardian tasks, переподключения, proactive callback
├── utils/
│   ├── claw_aggregator.py   # Дебаунс + выбор лучшего chat.final из множества событий
│   ├── health.py            # Health check endpoints (/health)
│   └── metrics.py           # Prometheus метрики (/metrics)
├── main.py                  # FastAPI приложение, lifecycle, сборка компонентов
└── router.py                # Ядро маршрутизации (события MM ↔ WS Manager)
```

### Таблица `user_instance`

| Колонка | Описание |
|---------|----------|
| `user_id` | Mattermost user ID (PK) |
| `instance_url` | WS URL OpenClaw instance (`ws://openclaw-gw-<UUID>:18789/ws`) |
| `device_id` | SHA-256 hex публичного ключа |
| `public_key_b64` | Ed25519 публичный ключ (base64url) |
| `private_key_b64` | Ed25519 приватный ключ (base64url) |
| `device_token` | Токен оператора из paired.json |
| `gateway_token` | OPENCLAW_GATEWAY_TOKEN этого инстанса |

---

## Переменные окружения (.env)

| Переменная | Описание | Default |
|------------|----------|---------|
| `DATABASE_URL` | PostgreSQL DSN (asyncpg) | — |
| `MATTERMOST_URL` | Mattermost server URL | — |
| `MATTERMOST_TOKEN` | Bot token для Mattermost API | — |
| `MATTERMOST_BOT_USERNAME` | Username бота (фильтрация своих сообщений) | `openclaw` |
| `OPENCLAW_RECEIVE_TIMEOUT_SEC` | Таймаут ожидания ответа от OpenClaw | `300` (5 мин) |
| `CLAW_DEBOUNCE_MS` | Окно дебаунса агрегатора (мс) | `400` |
| `WS_RECONNECT_MAX_RETRIES` | Макс. попыток reconnect | `3` |
| `WS_RECONNECT_BASE_DELAY_SEC` | База для exponential backoff | `1.0` |
| `LOG_LEVEL` | Уровень логирования | `INFO` |
| `RAW_WS_DUMP` | Если `1` — дампит все WS сообщения в `/tmp/ws_raw_dump.jsonl` | — |

> `OPENCLAW_GATEWAY_TOKEN` **не хранится** в `.env` — каждый инстанс имеет свой токен, он хранится в БД.

---

## Отладка

### Посмотреть сырые WS-сообщения от OpenClaw

```bash
# Включить полный дамп (перезапуск не нужен, меняется через docker env)
docker compose down ws_router
RAW_WS_DUMP=1 docker compose up ws_router

# Читать дамп в реальном времени
docker exec ws_router tail -f /tmp/ws_raw_dump.jsonl | python3 -m json.tool
```

### Проверить, правильно ли работает dual-stream

В логах при получении `agent` события вы увидите:
```
ws_event=agent  stream=assistant  → _agent_texts буферизован
ws_event=chat   state=final       → text заменён на _agent_texts
```

### Endpoints

| Endpoint | Описание |
|----------|----------|
| `GET /health` | Basic health check |
| `GET /health/detail` | + кол-во активных WS соединений |
| `GET /metrics` | Prometheus метрики |

---

## Гарантии

- ✅ **1 user = 1 instance** — строгая изоляция, утечка сообщений архитектурно невозможна
- ✅ **Persistent WS** — соединение держится постоянно
- ✅ **Изоляция контекста LLM** — `session_key = mm:chan:{channel_id}` на каждый канал
- ✅ **Точные числа** — dual-stream workaround исправляет Gateway баг с обрезкой нулей
- ✅ **Чистые ответы** — reasoning (внутренние мысли LLM) не отображается пользователю
- ✅ **Native File Delivery** — роутер перехватывает markdown-ссылки на локальные файлы, загружает их из Shared Volume (включая `canvas` и `workspace`) и прикрепляет как нативные вложения в Mattermost.
- ✅ **Thinking placeholders** — вместо нестабильного посимвольного стриминга отображаются живые статусы генерации ("Думаю...", "Пишу ответ...").
- ✅ **Проактивные сообщения** — OpenClaw может инициировать без запроса пользователя
- ✅ **Cron-уведомления** — `ws_event=cron` доставляются через `payload.summary`
- ✅ **DM fallback** — если канал неизвестен, создаётся DM с пользователем
- ✅ **Auto-reconnect** — guardian task переподключается при разрыве (exponential backoff)
- ✅ **Typing indicator** — "Bot is typing..." каждые 4 сек пока OpenClaw думает
- ✅ **Non-blocking HTTP** — отправка в Mattermost через `asyncio.to_thread`
- ✅ **Graceful shutdown** — корректное закрытие всех WS при остановке
- ✅ **DB cache** — повторные запросы не ходят в PostgreSQL если соединение уже есть

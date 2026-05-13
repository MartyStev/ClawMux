# OpenClaw WS Router

Open-source роутер между Mattermost и персональными OpenClaw instance-ами.

Сервис держит persistent WebSocket к OpenClaw для каждого пользователя, маршрутизирует входящие сообщения, поддерживает proactive ответы и предоставляет Control-Plane API для внешних систем.

## Возможности

- 1 user = 1 OpenClaw instance (изоляция по пользователю)
- Persistent WS-подключения и автопереподключение
- Fire-and-forget API: `POST /api/v1/trigger`
- Proactive отправка уведомлений: `POST /api/v1/notify`
- Proxy передачи файлов Mattermost ↔ OpenClaw workspace
- Healthcheck (`/health`) и Prometheus metrics (`/metrics`)
- Опциональный fallback в Dify

## Технологии

- Python 3.11+
- FastAPI
- SQLAlchemy (async) + asyncpg
- Alembic
- Mattermost API/WS

## Быстрый старт

### 1. Клонирование и окружение

```bash
git clone <your-fork-or-repo-url>
cd ClawMux
cp .env.example .env
```

Заполните `.env` своими значениями.

### 2. Запуск через Docker Compose

```bash
docker compose up -d --build
```

### 3. Миграции

Миграции запускаются из `entrypoint.sh` автоматически при старте контейнера.

Для ручного запуска:

```bash
alembic upgrade head
```

### 4. Проверка

```bash
curl http://localhost:8060/health
```

## Конфигурация

Основные переменные в `.env.example`:

- `DATABASE_URL` — строка подключения к PostgreSQL
- `MATTERMOST_URL` — URL Mattermost
- `MATTERMOST_TOKEN` — токен бота Mattermost
- `API_TOKEN` — секрет для `X-Api-Token` в Control-Plane API
- `WORKSPACE_BASE_PATH` — путь внутри `ws-router` контейнера к смонтированным конфигам OpenClaw
- `DIFY_BASE_URL` / `DIFY_API_KEY` — опциональный fallback

## Control-Plane API

### `POST /api/v1/trigger`

Отправляет задачу пользователю асинхронно.

Пример запроса:

```bash
curl -X POST http://localhost:8060/api/v1/trigger \
  -H "X-Api-Token: change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "external_user_id": "user-ext-123",
    "text": "Сделай краткий отчёт по продажам"
  }'
```

### `POST /api/v1/notify`

Отправляет системное уведомление пользователю.

```bash
curl -X POST http://localhost:8060/api/v1/notify \
  -H "X-Api-Token: change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "external_user_id": "user-ext-123",
    "text": "Напоминание: ежедневный отчёт через 10 минут"
  }'
```

## Модель данных (кратко)

- `instance` — OpenClaw instance + device credentials
- `mm_user` — Mattermost user + `external_user_id`
- `user_instance` — активная привязка пользователя к instance

## Структура проекта

```text
src/
  api/          # HTTP API
  core/         # config, db, models
  services/     # Mattermost/OpenClaw clients, mapping, file handling
  utils/        # health, metrics, aggregation
alembic/        # database migrations
docs/           # additional docs
```

## Локальная разработка

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
python -m src.main
```

## Безопасность

- Не коммитьте `.env`, токены и приватные URL
- Используйте отдельные сервисные аккаунты для Mattermost и OpenClaw
- Ротируйте `API_TOKEN` и bot-токены

## Примечания для open source

- Значения в `.env.example` обезличены и безопасны как шаблон
- Все внешние идентификаторы в API унифицированы как `external_user_id`

# Создание нового инстанса OpenClaw для WS Router

Для подключения нового пользователя к инстансу OpenClaw используйте скрипт
`provision_instance.py`. Он автоматически создаёт `paired.json`, `pending.json`
и вносит запись в БД.

> **Предусловие:** контейнер `openclaw-gw-<UUID>` и директория
> `/root/openclaw/user-configs/<UUID>/` с `openclaw.json` уже должны существовать.

---

## Структура файлов инстанса

```
/root/openclaw/user-configs/<UUID>/
├── openclaw.json          ← уже есть в инстансе (не трогаем)
├── devices/
│   ├── paired.json        ← создаётся скриптом
│   └── pending.json       ← создаётся скриптом (= {})
├── identity/              ← генерируется Gateway при первом запуске
└── workspace/
```

---

## Шаг 1: Запустить provisioning-скрипт

```bash
cd /root/openclaw/ws-router

python3 scripts/provision_instance.py \
    --user-id <MATTERMOST_USER_ID> \
    --uuid    <UUID> \
    --gateway-token <GATEWAY_TOKEN>
```

**Параметры:**

| Параметр | Описание |
|----------|----------|
| `--user-id` | Mattermost user_id пользователя (26-символьный) |
| `--uuid` | UUID инстанса → определяет имя контейнера `openclaw-gw-<UUID>` и директорию `/root/openclaw/user-configs/<UUID>/` |
| `--gateway-token` | `OPENCLAW_GATEWAY_TOKEN` этого конкретного инстанса |

**Опциональные параметры:**

| Параметр | По умолчанию | Описание |
|----------|-------------|----------|
| `--configs-root` | `/root/openclaw/user-configs` | Корневая директория конфигов |
| `--database-url` | из `.env` или env | PostgreSQL DSN (если нужен прямой доступ) |
| `--pg-container` | `openclaw-postgres` | Имя контейнера postgres (для docker exec fallback) |
| `--dry-run` | — | Показать план без выполнения |

**Что делает скрипт:**

1. Проверяет существование директории `<configs-root>/<UUID>/`
2. Генерирует Ed25519 device credentials (ключи + токен)
3. Записывает `devices/paired.json` с правильной структурой
4. Записывает `devices/pending.json` = `{}`
5. Устанавливает права `1000:1000` на директорию (`chown -R`)
6. Вставляет запись в `user_instance` (через psycopg2 или docker exec fallback)

### Пример вывода

```
======================================================================
🚀 OpenClaw Instance Provisioner
======================================================================
  user_id       : abc123xyz456
  uuid          : f47ac10b-58cc-4372-a567-0e02b2c3d479
  container     : openclaw-gw-f47ac10b-58cc-4372-a567-0e02b2c3d479
  instance_url  : ws://openclaw-gw-f47ac10b-...:18789/ws
  configs_root  : /root/openclaw/user-configs
======================================================================

📁 Шаг 1: Проверка директории инстанса...
  ✅ /root/openclaw/user-configs/f47ac10b-.../devices — готова

🔑 Шаг 2: Генерация Ed25519 credentials...
  device_id : 3f2a1b...
  pub_key   : aGVsbG8...

📄 Шаг 3: Создание device-файлов...
  ✅ Создан .../devices/paired.json
  ✅ Создан .../devices/pending.json

🔒 Шаг 4: Установка прав 1000:1000...
  ✅ Права 1000:1000 установлены

🗃️  Шаг 5: Запись в базу данных user_instance...
  ✅ Запись в БД успешна

======================================================================
✅ Инстанс успешно provisioned!

📋 Следующий шаг — перезапустить WS Router:
   docker restart ws_router
======================================================================
```

---

## Шаг 2: Перезапустить WS Router

```bash
docker restart ws_router
```

---

## Примечание: `identity/` не создаём вручную

Папка `identity/` **создаётся автоматически** при первом запуске Gateway.
**Не копируйте** её из другого инстанса — каждый должен иметь свою пару ключей.

## Шаг 6: Добавить сервис в `docker-compose.yml`

```yaml
  openclaw-gw-<NAME>:
    image: ghcr.io/openclaw/openclaw:2026.4.15
    container_name: openclaw-gw-<NAME>
    environment:
      HOME: /home/node
      TERM: xterm-256color
      OPENCLAW_GATEWAY_TOKEN: <GATEWAY_TOKEN>
      OPENCLAW_ALLOW_INSECURE_PRIVATE_WS: '1'
      TZ: Europe/Moscow
      VLLM_API_KEY: sk-local
      VLLM_BASE_URL: http://vllm:8000/v1
    volumes:
      - /root/openclaw/user-configs/<UUID>:/home/node/.openclaw
      - /root/openclaw/user-configs/<UUID>/workspace:/home/node/.openclaw/workspace
    command:
      - node
      - dist/index.js
      - gateway
      - --bind
      - lan
      - --port
      - '18789'
    restart: unless-stopped
    init: true
    networks:
      - ai-network
```

> **Примечание:** Каждый инстанс на `ai-network` напрямую (без `network_mode`)
> и доступен по имени контейнера: `ws://openclaw-gw-<NAME>:18789/ws`

## Шаг 3: Проверить результат

```bash
# Убедиться что файлы созданы
ls -la /root/openclaw/user-configs/<UUID>/devices/
# paired.json  pending.json  (оба с владельцем 1000:1000)

# Проверить запись в БД
docker exec openclaw-postgres psql -U openclaw -d ws_router \
    -c "SELECT user_id, instance_url, device_id FROM user_instance WHERE user_id='<USER_ID>';"

# Контейнер должен быть уже запущен (создан ранее)
docker ps | grep openclaw-gw-<UUID>
# Должен показать (healthy)

# Проверить логи gateway
docker logs openclaw-gw-<UUID> --tail 10
# Должен показать: [gateway] ready (...)
```

---

## Чеклист

| # | Шаг | Проверка |
|---|-----|----------|
| 1 | `openclaw.json` есть | `cat /root/openclaw/user-configs/<UUID>/openclaw.json \| head -3` |
| 2 | Запустили скрипт | `python3 scripts/provision_instance.py --user-id ... --uuid ... --gateway-token ...` |
| 3 | `devices/paired.json` создан | `cat .../devices/paired.json` |
| 4 | `devices/pending.json` = `{}` | `cat .../devices/pending.json` |
| 5 | Права = 1000:1000 | `ls -la .../devices/` |
| 6 | Запись в БД | `psql ... -c "SELECT * FROM user_instance WHERE user_id='...'"` |
| 7 | Контейнер healthy | `docker ps \| grep <UUID>` |
| 8 | WS Router перезапущен | `docker restart ws_router` |
| 9 | Ответ приходит в Mattermost | Отправить сообщение боту |

---

## Частые ошибки

| Ошибка | Причина | Решение |
|--------|---------|---------|
| `INVALID_REQUEST: at /client/id: must be equal to constant` | Неправильный `clientId` в `paired.json` или коде | Используйте `"openclaw-control-ui"` |
| `metadata-upgrade` + `EACCES` | Несовпадение `platform` + нет прав на запись | Обновите `paired.json` и сделайте `chown -R 1000:1000` |
| `Connection refused` | Инстанс не запущен или ещё стартует | Подождите 15 сек, проверьте `docker ps` |
| `received 1000 (OK)` | Gateway закрыл соединение | Проверьте логи gateway: `docker logs <NAME> --tail 20` |
| Нет ответа (таймаут) | vLLM не доступен из инстанса | `docker exec <NAME> curl http://vllm:8000/v1/models` |
| Ответ не приходит в MM | Роутер не обрабатывает `agent`/`chat` события | Обновите код роутера до последней версии |

---

## Управление контекстом и историей сессий (опционально)

При активном общении с ботом история контекста может сильно разрастаться, из-за чего локальные модели (например, `Qwen3.6-27B` через `vLLM`) будут очень долго генерировать ответ (долгий этап `prefill`). 

Для ограничения глубины контекста добавьте блок `maintenance` в файл `openclaw.json` нужного инстанса:

```json
  "session": {
    "dmScope": "per-channel-peer",
    "maintenance": {
      "maxEntries": 10
    },
    "reset": {
      "mode": "idle",
      "idleMinutes": 60
    }
  }
```

- `maxEntries`: оставляет только N последних сообщений в памяти агента. Кардинально снижает задержку на генерацию первого токена.
- `reset` / `idleMinutes`: сбрасывает (очищает) контекст, если в диалоге не было активности больше указанного времени (например, 60 минут).

Если вам нужно **сбросить контекст прямо сейчас**, просто удалите файлы сессий из `workspace`:
```bash
sudo rm -rf /root/openclaw/user-configs/<UUID>/workspace/agents/main/sessions/*
docker restart openclaw-gw-<UUID>
```

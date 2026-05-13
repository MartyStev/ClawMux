# Control-Plane API — `/api/v1/trigger`

## Что это и зачем

`POST /api/v1/trigger` — это HTTP endpoint, который позволяет **внешней системе отправить задачу конкретному пользователю в OpenClaw**.

Поток данных:

```
Внешняя система (CRM, Webhook, Scheduler)
      ↓
POST /api/v1/trigger  { "crm_user_id": "...", "text": "..." }
      ↓
ws_router
  → PostgreSQL: SELECT * FROM user_instance WHERE crm_user_id = ?
  → получаем Mattermost user_id + URL инстанса OpenClaw
      ↓
OpenClaw (через существующий WS)
      ↓
Пользователь получает ответ в Mattermost (или другом канале)
```

**Ключевое**: роутер отвечает сразу (`{"status": "sent"}`), не дожидаясь ответа от OpenClaw. OpenClaw сам обрабатывает задачу и пишет пользователю.

---

## Аутентификация

Каждый запрос должен содержать заголовок:

```
X-Api-Token: <значение API_TOKEN из .env>
```

Если токен неверный или отсутствует → `401 Unauthorized`.

### Как сгенерировать токен

```bash
openssl rand -hex 32
```

Вставь результат в `.env`:

```dotenv
API_TOKEN=a3f8c2d1e4b7f9a0...
```

---

## Запрос

### `POST /api/v1/trigger`

**Headers:**

| Заголовок | Обязателен | Описание |
|-----------|-----------|----------|
| `X-Api-Token` | ✅ | Секретный токен из `.env` |
| `Content-Type` | ✅ | `application/json` |

**Body (JSON):**

| Поле | Тип | Обязателен | Описание |
|------|-----|-----------|----------|
| `crm_user_id` | string | ✅ | ID пользователя в CRM-системе (поле `crm_user_id` в БД) |
| `text` | string | ✅ | Текст задачи для OpenClaw |
| `session_key` | string | ❌ | Сессия OpenClaw (по умолчанию `agent:main:main`) |

> **Примечание:** Внутренний Mattermost `user_id` нигде не фигурирует в API —
> роутер сам находит его по `crm_user_id` из таблицы `user_instance`.

---

## Ответы

| Код | Тело | Причина |
|-----|------|---------| 
| `200` | `{"status": "sent", "request_id": "..."}` | Задача отправлена в OpenClaw |
| `401` | `{"detail": "Invalid or missing API token"}` | Неверный токен |
| `404` | `{"detail": "No OpenClaw instance configured for crm_user_id='...'"}` | crm_user_id не найден в БД |
| `503` | — | (зарезервировано для offline-пользователей) |


> `request_id` — UUID для поиска в логах: `grep request_id=<uuid> ...`

---

## БД: как привязать crm_user_id к пользователю

После применения миграции `002_add_crm_user_id` обновляем существующую строку:

```sql
-- Привязать CRM-идентификатор к уже существующему пользователю:
UPDATE user_instance
SET crm_user_id = 'crm-ext-123456'
WHERE user_id = 'abc123mattermost';

-- Проверить:
SELECT user_id, crm_user_id, instance_url FROM user_instance;
```

---

## Как тестировать

### 1. `curl` (быстрый тест)

```bash
curl -X POST http://localhost:8060/api/v1/trigger \
  -H "X-Api-Token: change-me-to-a-strong-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "crm_user_id": "crm-ext-123456",
    "text": "Сделай краткий отчёт по продажам за сегодня"
  }'
```

**Ожидаемый ответ:**
```json
{
  "status": "sent",
  "request_id": "f4a1b2c3-d4e5-6789-abcd-ef0123456789"
}
```

---

### 2. Через `httpie` (удобнее для ручного тестирования)

```bash
pip install httpie

http POST localhost:8060/api/v1/trigger \
  X-Api-Token:change-me-to-a-strong-secret \
  crm_user_id=crm-ext-123456 \
  text="Напомни команде про дейли в 10:00"
```

---

### 3. Python-скрипт (для интеграции)

```python
import requests

ROUTER_URL = "http://localhost:8060"
API_TOKEN = "change-me-to-a-strong-secret"

def trigger(crm_user_id: str, text: str) -> dict:
    resp = requests.post(
        f"{ROUTER_URL}/api/v1/trigger",
        headers={"X-Api-Token": API_TOKEN},
        json={"crm_user_id": crm_user_id, "text": text},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()

result = trigger("crm-ext-123456", "Сделай отчёт")
print(result)  # {'status': 'sent', 'request_id': '...'}
```

---

### 4. Swagger UI (встроенный в FastAPI)

Открой в браузере: **http://localhost:8060/docs**

Там увидишь раздел `control-plane` → `POST /api/v1/trigger`.  
Нажми `Try it out`, введи токен и тело запроса.

---

### 5. Тест ошибок

**Неверный токен → 401:**
```bash
curl -X POST http://localhost:8060/api/v1/trigger \
  -H "X-Api-Token: wrong-token" \
  -H "Content-Type: application/json" \
  -d '{"crm_user_id": "crm-ext-123456", "text": "test"}'
# {"detail":"Invalid or missing API token"}
```

**Несуществующий crm_user_id → 404:**
```bash
curl -X POST http://localhost:8060/api/v1/trigger \
  -H "X-Api-Token: change-me-to-a-strong-secret" \
  -H "Content-Type: application/json" \
  -d '{"crm_user_id": "nonexistent-crm-id", "text": "test"}'
# {"detail":"No OpenClaw instance configured for crm_user_id='nonexistent-crm-id'"}
```

---

## Как найти crm_user_id

`crm_user_id` — внешний идентификатор из вашей CRM-системы.

Посмотреть текущий маппинг в БД `ws_router`:
```sql
SELECT user_id, crm_user_id, instance_url FROM user_instance;
```

---

## Логи

Каждый вызов пишет в лог:

```
trigger_dispatched  crm_user_id=crm-ext-123456  mm_user_id=abc123mattermost  request_id=f4a1b2c3  session_key=agent:main:main  text_len=42
```

Отследить конкретный запрос:
```bash
docker logs ws-router 2>&1 | grep "f4a1b2c3"
```

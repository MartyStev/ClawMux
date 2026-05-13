# OpenClaw WS Router — Control-Plane API Specification

**Version:** 1.1  
**Base URL:** `http://<router-host>:8060`  
**Public URL:** `https://ai.homeapp.team/router/api/v1/trigger`
**Protocol:** HTTP/1.1, JSON

---

## Обзор

WS Router — это сервис, который управляет соединениями с экземплярами OpenClaw (по одному на пользователя). Control-Plane API позволяет внешним системам отправлять задачи пользователям в OpenClaw, используя внешний идентификатор пользователя из CRM (`crm_user_id`).

### Поток данных

```
Внешняя система (CRM)
      │
      │  POST /api/v1/trigger { "crm_user_id": "...", "text": "..." }
      ▼
   WS Router  ──────────────┐
      │                     │ 1. Lookup Mattermost user_id in DB
      │                     │ 2. Find OpenClaw instance URL
      ▼                     ▼
   OpenClaw (пользователь) ◀─┘
      │
      │  (обработка задачи)
      ▼
   Mattermost (сообщение пользователю)
```

### Ключевые особенности

- **Маршрутизация по CRM ID**: Внешние системы используют `crm_user_id`. Роутер сам находит внутренний Mattermost `user_id`.
- **Fire-and-forget**: `POST /trigger` возвращает `200 OK` немедленно, не дожидаясь ответа от OpenClaw.
- Ответ OpenClaw доставляется пользователю **автоматически** через Mattermost.

---

## Аутентификация

Все запросы требуют заголовок:

```
X-Api-Token: <token>
```

Токен задаётся в `.env` (переменная `API_TOKEN`). При отсутствии или неверном значении — `401 Unauthorized`.

---

## Endpoints

### `POST /api/v1/trigger`

Отправить задачу пользователю в OpenClaw.

#### Request

**Headers**

| Заголовок | Обязателен | Описание |
|-----------|:----------:|----------|
| `X-Api-Token` | ✅ | Секретный токен для аутентификации |
| `Content-Type` | ✅ | Должен быть `application/json` |

**Body**

```json
{
  "crm_user_id": "crm-ext-123456",
  "text": "Сделай краткий отчёт по продажам за сегодня",
  "session_key": "agent:main:main"
}
```

| Поле | Тип | Обязателен | Описание |
|------|-----|:----------:|----------|
| `crm_user_id` | `string` | ✅ | Внешний идентификатор пользователя в CRM-системе |
| `text` | `string` | ✅ | Текст задачи или запроса для OpenClaw |
| `session_key` | `string` | ❌ | Ключ сессии OpenClaw. По умолчанию: `agent:main:main` |

#### Response

**`200 OK`** — задача принята и передана в OpenClaw

```json
{
  "status": "sent",
  "request_id": "f4a1b2c3-d4e5-6789-abcd-ef0123456789"
}
```

| Поле | Тип | Описание |
|------|-----|----------|
| `status` | `string` | Всегда `"sent"` при успехе |
| `request_id` | `string` | UUID для трассировки в логах |

> ⚠️ **Важно**: `200 OK` означает только что запрос принят. Ответ OpenClaw придёт пользователю в Mattermost асинхронно, обычно через 5–30 секунд.

---

#### Коды ошибок

**`401 Unauthorized`** — неверный или отсутствующий токен

```json
{ "detail": "Invalid or missing API token" }
```

**`404 Not Found`** — пользователь с таким `crm_user_id` не найден в базе данных роутера

```json
{ "detail": "No OpenClaw instance configured for crm_user_id='crm-ext-123456'" }
```

**`422 Unprocessable Entity`** — невалидный JSON или отсутствуют обязательные поля

```json
{
  "detail": [
    {
      "loc": ["body", "crm_user_id"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

---

## Примеры использования

### curl

```bash
curl -X POST http://localhost:8060/api/v1/trigger \
  -H "X-Api-Token: my-secret-token" \
  -H "Content-Type: application/json" \
  -d '{
    "crm_user_id": "user_12345",
    "text": "Какая выручка за прошлый месяц?"
  }'
```

### Python

```python
import requests

def send_trigger(crm_id, message):
    url = "http://localhost:8060/api/v1/trigger"
    headers = {
        "X-Api-Token": "my-secret-token",
        "Content-Type": "application/json"
    }
    payload = {
        "crm_user_id": crm_id,
        "text": message
    }
    
    response = requests.post(url, json=payload, headers=headers)
    return response.json()
```

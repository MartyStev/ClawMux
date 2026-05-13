# Control-Plane API

## Назначение

Control-Plane API позволяет внешним системам отправлять задачи и уведомления пользователям OpenClaw через роутер.

Базовый URL:

```text
http://<router-host>:8060/api/v1
```

Аутентификация для всех эндпоинтов:

```text
X-Api-Token: <API_TOKEN>
```

`API_TOKEN` задаётся в `.env`.

## 1) POST /trigger

Асинхронно отправляет задачу пользователю. Ответ `200` означает, что задача принята роутером и поставлена в обработку.

### Request

```json
{
  "external_user_id": "user-ext-123",
  "provider": "mattermost",
  "text": "Сделай краткий отчёт по продажам",
  "session_key": "agent:main:main"
}
```

Поля:

- `external_user_id` (string, required): внешний идентификатор пользователя из вашей системы
- `provider` (string, optional): провайдер канала, по умолчанию `mattermost`
- `text` (string, required): текст задачи
- `session_key` (string, optional): ключ сессии OpenClaw

### Response 200

```json
{
  "status": "sent",
  "request_id": "f4a1b2c3-d4e5-6789-abcd-ef0123456789"
}
```

## 2) POST /notify

Отправляет системное уведомление пользователю.

### Request

```json
{
  "external_user_id": "user-ext-123",
  "provider": "mattermost",
  "text": "Напоминание: дейли через 10 минут"
}
```

### Response 200

```json
{
  "status": "sent"
}
```

## Коды ошибок

- `401 Unauthorized`: неверный или отсутствующий `X-Api-Token`
- `404 Not Found`: не найден маппинг пользователя
- `400 Bad Request`: провайдер канала не включён
- `422 Unprocessable Entity`: невалидное тело запроса

## Пример `curl`

```bash
curl -X POST http://localhost:8060/api/v1/trigger \
  -H "X-Api-Token: change-me-to-a-strong-secret" \
  -H "Content-Type: application/json" \
  -d '{"external_user_id":"user-ext-123","provider":"mattermost","text":"Сделай отчёт"}'
```


Важно: multi-channel структура уже есть в БД, но в runtime включён только `mattermost`.

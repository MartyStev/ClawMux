# Переключение на реальный OpenClaw с LM Studio

## 📋 Текущая конфигурация

Система работает с **Mock OpenClaw** для тестирования. Чтобы использовать реальный OpenClaw с LM Studio, выполните эти шаги:

## ✅ Требования

- ✔️ LM Studio запущен на `http://127.0.0.1:1234`
- ✔️ Модель `qwen/qwen3.5-9b` загружена в LM Studio
- ✔️ Docker установлен
- ✔️ Git установлен

## 🚀 Установка реального OpenClaw

### Шаг 1: Получить OpenClaw образ

**Вариант A: Использовать публичный образ**
```bash
export OPENCLAW_IMAGE=ghcr.io/openclaw/openclaw:latest
./setup-openclaw.sh
```

**Вариант B: Построить из локального источника**
```bash
export OPENCLAW_IMAGE=ghcr.io/openclaw/openclaw:latest
export OPENCLAW_SRC=/path/to/openclaw-source
./setup-openclaw.sh
```

Если у вас уже есть действительный образ, вы можете просто задать `OPENCLAW_IMAGE` и запустить `docker compose -f docker-compose.prod.yml up -d`.
### Шаг 2: Проверить, что LM Studio работает

```bash
# Убедиться, что LM Studio слушает на 127.0.0.1:1234
curl http://127.0.0.1:1234/v1/models | jq .

# Должны увидеть модель qwen в ответе
```

### Шаг 3: Остановить текущую систему

```bash
docker compose down
```

### Шаг 4: Запустить с реальным OpenClaw

```bash
# Использовать production compose файл
docker compose -f docker-compose.prod.yml up -d

# Или с флагом --build если нужно пересобрать образы
docker compose -f docker-compose.prod.yml up -d --build
```

### Шаг 5: Обновить конфигурацию БД

```bash
# Найти instance_uuid OpenClaw
docker compose -f docker-compose.prod.yml logs openclaw | grep -i instance

# Обновить маппинг (замените instance_uuid если нужно)
docker compose -f docker-compose.prod.yml exec -T postgres psql -U router -d ws_router << 'EOF'
UPDATE instance 
SET instance_url = 'ws://openclaw:19000'
WHERE instance_uuid = '2f99d082-71fb-4bf7-a4c5-cfeea78976c6';
EOF

# Перезапустить ws-router
docker compose -f docker-compose.prod.yml restart ws-router
```

## 🧪 Тестирование

### Проверить здоровье системы
```bash
./test_integration.sh
```

### Тестировать маршрутизацию
```bash
./test_routing.sh
```

### Мониторить логи
```bash
# All services
docker compose -f docker-compose.prod.yml logs -f

# Only OpenClaw
docker compose -f docker-compose.prod.yml logs -f openclaw

# Only WS Router
docker compose -f docker-compose.prod.yml logs -f ws-router

# Only LM Studio proxy
docker compose -f docker-compose.prod.yml logs -f lmstudio-proxy
```

## 🔧 Настройка

Переменные окружения в `docker-compose.prod.yml`:

```yaml
# LLM Configuration
LLM_PROVIDER: lmstudio        # Провайдер LLM
LLM_BASE_URL: http://lmstudio-proxy:1234/v1  # Базовый URL
LLM_MODEL: qwen/qwen3.5-9b   # Модель (измените если используете другую)

# Instance Configuration
INSTANCE_NAME: ClawMux-Main   # Имя инстанса
DEVICE_ID: clawmux-device-001 # ID устройства
GATEWAY_URL: http://ws-router:8060  # URL маршрутизатора
```

## 🔄 Переключение между mock и реальным

### На mock (для тестирования)
```bash
# Убедитесь что используется основной compose файл
docker compose down
docker compose up -d
```

### На реальный (production)
```bash
docker compose down
docker compose -f docker-compose.prod.yml up -d
```

## 🐛 Решение проблем

### Проблема: OpenClaw не подключается к LM Studio

```bash
# Проверить доступность LM Studio на хосте
curl http://127.0.0.1:1234/v1/models

# Проверить внутри контейнера nginx
docker compose -f docker-compose.prod.yml exec lmstudio-proxy \
  curl http://host.docker.internal:1234/v1/models

# Проверить логи OpenClaw
docker compose -f docker-compose.prod.yml logs openclaw | grep -i "llm\|error"
```

### Проблема: WS Router не может подключиться к OpenClaw

```bash
# Проверить что OpenClaw слушает на порту
docker compose -f docker-compose.prod.yml exec openclaw \
  netstat -tlnp | grep 19000

# Проверить logs
docker compose -f docker-compose.prod.yml logs openclaw

# Тестировать WS подключение
docker compose -f docker-compose.prod.yml exec ws-router \
  python -c "import asyncio; import websockets; \
  asyncio.run(websockets.connect('ws://openclaw:19000'))"
```

### Проблема: Модель не загружена в LM Studio

```bash
# Убедиться что модель загружена
curl http://127.0.0.1:1234/v1/models | jq '.data[] | .id'

# Если нет, загрузить в LM Studio GUI:
# 1. Open http://127.0.0.1:1234
# 2. Search for: qwen/qwen3.5-9b
# 3. Click Load
```

## 📊 Архитектура с реальным OpenClaw

```
┌─────────────────┐
│  Mattermost     │
└────────┬────────┘
         │ WebSocket
         ▼
┌─────────────────┐
│   WS Router     │ :8060
│  (ws-router)    │
└────────┬────────┘
         │ WebSocket
         ▼
┌─────────────────┐
│  OpenClaw       │ :19000
│  Instance       │
└────────┬────────┘
         │ HTTP API (/v1)
         ▼
┌─────────────────┐
│ nginx Proxy     │ :1234
│(lmstudio-proxy) │
└────────┬────────┘
         │ HTTP
         ▼
┌─────────────────┐
│  LM Studio      │ 127.0.0.1:1234
│ (Host machine)  │
└────────┬────────┘
         │ API calls
         ▼
┌─────────────────┐
│  qwen/qwen3.5-9b│
│  (Model)        │
└─────────────────┘
```

## 📝 Команды reference

```bash
# Просмотр всех сервисов
docker compose -f docker-compose.prod.yml ps

# Остановить только OpenClaw
docker compose -f docker-compose.prod.yml stop openclaw

# Перезапустить WS Router
docker compose -f docker-compose.prod.yml restart ws-router

# Посмотреть использование ресурсов
docker compose -f docker-compose.prod.yml stats

# Удалить все данные и начать заново
docker compose -f docker-compose.prod.yml down -v
```

## ✨ Готово!

Система настроена и готова к использованию с реальным OpenClaw и LM Studio.

Для получения помощи, проверьте логи:
```bash
docker compose -f docker-compose.prod.yml logs --tail=50
```

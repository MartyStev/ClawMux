# План реализации: Вложения Mattermost → OpenClaw (Shared Volumes)

> **Решение зафиксировано:** Все типы файлов (изображения, аудио, PDF, документы)
> доставляются через shared volume. Маршрут OpenClaw → Mattermost обсуждается отдельно.

---

## Почему только volume, без base64

- `chat.send attachments` в OpenClaw принимает **только `image/*`** — аудио, PDF, DOCX дропаются
- Volume работает для всех типов файлов одинаково
- Единый простой код без ветвления по MIME-типу

---

## Архитектура маршрута

```
Mattermost WS event
  └─ post.file_ids: ["abc", "xyz"]
        │
        ▼
MattermostClient
  └─ GET /api/v4/files/<id>   ← бинарные данные
        │
        ▼
FileManager.download_attachments()
  └─ сохраняет в:
     /configs/<UUID>/workspace/downloads/<filename>
     (это /root/openclaw/user-configs/<UUID>/workspace/downloads/ на хосте)
        │
        ▼
Router.handle_event()
  └─ формирует системный контекст:
     "[СИСТЕМА] Файлы доступны по путям:
      - photo.png (image/png, 512 KB) → /home/node/.openclaw/workspace/downloads/photo.png
      - report.pdf (application/pdf, 1.2 MB) → /home/node/.openclaw/workspace/downloads/report.pdf"
        │
        ▼
chat.send(message="<текст>" + системный контекст)
        │
        ▼
OpenClaw agent
  └─ читает файлы bash/python tools по путям в контейнере
```

---

## Изоляция данных

Каждый инстанс видит **только свою** директорию через Docker mount:

| Хост | Контейнер инстанса A | Контейнер инстанса B |
|:---|:---|:---|
| `/root/openclaw/user-configs/UUID_A/workspace/downloads/` | `/home/node/.openclaw/workspace/downloads/` | не смонтировано |
| `/root/openclaw/user-configs/UUID_B/workspace/downloads/` | не смонтировано | `/home/node/.openclaw/workspace/downloads/` |

`ws_router` монтирует весь `/root/openclaw/user-configs`, но записывает файлы **строго по UUID** пользователя из БД.

---

## Шаг 1: `src/config.py`

```python
# ── Attachments ────────────────────────────────────────────────────
workspace_base_path: str = Field(
    default="/configs",
    description=(
        "Base path inside ws_router container where openclaw-configs are mounted. "
        "Structure: <base>/<uuid>/workspace/..."
    ),
)
attachment_max_size_mb: int = Field(
    default=50,
    description="Max allowed file size for download (MB). Larger files are skipped.",
)
container_downloads_path: str = Field(
    default="/home/node/.openclaw/workspace/downloads",
    description="Path inside the OpenClaw container where downloaded files appear.",
)
```

---

## Шаг 2: `src/file_manager.py` (новый модуль)

```python
import asyncio
import os
import time
from dataclasses import dataclass
from typing import Optional

import structlog
from mattermostdriver import Driver

from .config import settings

log = structlog.get_logger()

@dataclass
class DownloadedFile:
    filename: str           # "report.pdf"
    host_path: str          # "/configs/<UUID>/workspace/downloads/report.pdf"
    container_path: str     # "/home/node/.openclaw/workspace/downloads/report.pdf"
    size_bytes: int
    mime_type: str          # "application/pdf"


class FileManager:
    def __init__(self, driver: Driver):
        self._driver = driver

    def resolve_host_downloads_path(self, uuid: str) -> str:
        """Путь к папке downloads на хосте (внутри контейнера роутера)."""
        return f"{settings.workspace_base_path}/{uuid}/workspace/downloads"

    async def download_attachments(
        self,
        file_ids: list[str],
        uuid: str,
    ) -> list[DownloadedFile]:
        host_dir = self.resolve_host_downloads_path(uuid)
        # Создаём папку автоматически при первом обращении
        os.makedirs(host_dir, exist_ok=True)

        results = []
        max_bytes = settings.attachment_max_size_mb * 1024 * 1024

        for file_id in file_ids:
            try:
                result = await self._download_one(file_id, host_dir, max_bytes)
                if result:
                    results.append(result)
            except Exception as e:
                log.warning("attachment_download_error", file_id=file_id, error=str(e))

        return results

    async def _download_one(
        self,
        file_id: str,
        host_dir: str,
        max_bytes: int,
    ) -> Optional[DownloadedFile]:
        # Получаем метаданные файла
        meta = await asyncio.to_thread(
            self._driver.files.get_file_metadata, file_id
        )
        filename = meta.get("name", file_id)
        size_bytes = meta.get("size", 0)
        mime_type = meta.get("mime_type", "application/octet-stream")

        if size_bytes > max_bytes:
            log.warning(
                "attachment_too_large",
                filename=filename,
                size_mb=size_bytes // (1024 * 1024),
                limit_mb=settings.attachment_max_size_mb,
            )
            return None

        # Скачиваем бинарные данные
        response = await asyncio.to_thread(
            self._driver.files.get_file, file_id
        )

        # Разрешаем конфликт имён суффиксом
        host_path = os.path.join(host_dir, filename)
        if os.path.exists(host_path):
            base, ext = os.path.splitext(filename)
            filename = f"{base}_{int(time.time())}{ext}"
            host_path = os.path.join(host_dir, filename)

        with open(host_path, "wb") as f:
            f.write(response.content)

        # Права для node user внутри OpenClaw-контейнера (UID 1000)
        os.chown(host_path, 1000, 1000)

        container_path = f"{settings.container_downloads_path}/{filename}"

        log.info(
            "attachment_downloaded",
            filename=filename,
            size_bytes=size_bytes,
            mime_type=mime_type,
        )

        return DownloadedFile(
            filename=filename,
            host_path=host_path,
            container_path=container_path,
            size_bytes=size_bytes,
            mime_type=mime_type,
        )


def build_attachment_context(files: list[DownloadedFile]) -> str:
    """Формирует системный контекст для добавления к сообщению."""
    if not files:
        return ""
    lines = [
        f"- `{f.filename}` ({f.mime_type}, {f.size_bytes // 1024} KB)"
        f" → `{f.container_path}`"
        for f in files
    ]
    return (
        "\n\n[СИСТЕМА: Пользователь прикрепил файлы. "
        "Они доступны для чтения по следующим путям:]\n"
        + "\n".join(lines)
    )
```

---

## Шаг 3: `src/mattermost.py` — добавить `file_ids` в Event

```python
# MattermostEvent — добавить поле:
file_ids: list[str] = field(default_factory=list)

# В _handle_ws_event():
file_ids = post.get("file_ids", [])

event = MattermostEvent(
    user_id=user_id,
    channel_id=channel_id,
    text=text,
    post_id=post_id,
    file_ids=file_ids,   # ← новое
)
```

---

## Шаг 4: `src/router.py` — обработка вложений

**В `__init__`:**
```python
self.file_manager = FileManager(driver=mattermost._driver)
```

**В `handle_event()` после получения instance_info:**
```python
attachment_context = ""

if event.file_ids:
    try:
        uuid = extract_uuid_from_instance_url(info.instance_url)
        if uuid:
            downloaded = await self.file_manager.download_attachments(
                event.file_ids, uuid
            )
            attachment_context = build_attachment_context(downloaded)
    except Exception as e:
        log.warning("attachment_processing_failed", error=str(e))

# Формируем финальное сообщение
message_text = event.text + attachment_context

# Если пользователь отправил только файл без текста
if not event.text.strip() and attachment_context:
    message_text = "Пользователь отправил файл(ы) без текста." + attachment_context
```

**Вспомогательная функция извлечения UUID:**
```python
import re

_UUID_RE = re.compile(
    r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
    re.IGNORECASE,
)

def extract_uuid_from_instance_url(instance_url: str) -> str | None:
    m = _UUID_RE.search(instance_url)
    return m.group(0) if m else None
```

---

## Шаг 5: `docker-compose.yml`

```yaml
services:
  ws-router:
    build: .
    container_name: ws_router
    env_file: .env
    ports:
      - "127.0.0.1:8060:8060"
    volumes:
      - /root/openclaw/user-configs:/configs   # ← ДОБАВИТЬ
    restart: unless-stopped
    networks:
      - ai-network
```

---

## Шаг 6: `.env.example`

```bash
# Attachments — Shared Volumes
WORKSPACE_BASE_PATH=/configs
ATTACHMENT_MAX_SIZE_MB=50
CONTAINER_DOWNLOADS_PATH=/home/node/.openclaw/workspace/downloads
```

---

## Edge-cases

| Ситуация | Поведение |
|:---|:---|
| `file_ids` пустой | Пропустить, отправить только текст |
| Файл > `ATTACHMENT_MAX_SIZE_MB` | Пропустить файл, логировать warning |
| UUID не найден в `instance_url` | Логировать warning, отправить только текст |
| Ошибка скачивания (403, сеть) | Логировать error, отправить только текст |
| Текст пустой (только файл) | Подставить дефолтный текст |
| Имя файла уже занято | Добавить суффикс `_{timestamp}` |
| Папка `downloads/` не существует | `os.makedirs(exist_ok=True)` создаёт автоматически |

---

## Деплой

```bash
# 1. Добавить переменные в .env на сервере
echo "WORKSPACE_BASE_PATH=/configs" >> ~/openclaw/ws-router/.env
echo "ATTACHMENT_MAX_SIZE_MB=50" >> ~/openclaw/ws-router/.env

# 2. Пересобрать с новым volume
docker compose up -d --build ws_router

# 3. Проверить монтирование
docker inspect ws_router | grep -A5 Mounts

# 4. Отправить файл боту, проверить логи
docker logs ws_router -f | grep attachment
ls -la /root/openclaw/user-configs/<UUID>/workspace/downloads/
```

---

## Список изменений

| Файл | Тип | Шаг |
|:---|:---|:---|
| `src/config.py` | +3 поля | 1 |
| `src/file_manager.py` | **Создать новый модуль** | 2 |
| `src/mattermost.py` | +`file_ids` в Event | 3 |
| `src/router.py` | Вызов FileManager + контекст | 4 |
| `docker-compose.yml` | Volume mount | 5 |
| `.env.example` | +3 переменные | 6 |

**Новых зависимостей нет** — `mattermostdriver` умеет скачивать файлы.

---

## Out of scope

- Обратный маршрут OpenClaw → Mattermost (обсуждается отдельно)
- Автоматическая очистка старых файлов (TTL cron)
- Whitelist MIME-типов

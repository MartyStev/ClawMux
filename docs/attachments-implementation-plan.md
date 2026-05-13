# Attachments Implementation Plan

**Версия:** 1.0  
**Дата:** 2026-04-27  
**Статус:** Зафиксировано ✅

---

## Архитектура (оба направления)

```
┌─────────────────────────────────────────────────────────────────────┐
│                     МАРШРУТ A: Mattermost → OpenClaw                │
│                                                                     │
│  Mattermost                                                         │
│  post.file_ids ──► MattermostClient                                 │
│                      GET /api/v4/files/<id>  (бинарные данные)      │
│                      ▼                                              │
│                    FileManager.download_attachments()               │
│                      записывает в:                                  │
│                      /configs/<UUID>/workspace/downloads/<file>     │
│                      ▼                                              │
│                    Router.handle_event()                            │
│                      добавляет к тексту системный контекст:         │
│                      "[СИСТЕМА] Файлы: /home/node/.openclaw/..."    │
│                      ▼                                              │
│                    chat.send(message + контекст)                    │
│                      ▼                                              │
│                    OpenClaw agent читает файлы по путям             │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                     МАРШРУТ B: OpenClaw → Mattermost                │
│                                                                     │
│  OpenClaw agent сохраняет файл:                                     │
│    /home/node/.openclaw/workspace/output/report.xlsx                │
│    указывает mediaUrl в tool-вызове                                 │
│    ▼                                                                │
│  chat.final payload содержит:                                       │
│    { "text": "Отчёт готов", "mediaUrls": ["/home/.../report.xlsx"] }│
│    ▼                                                                │
│  openclaw_client.py — _dispatch() перехватывает mediaUrls          │
│    ClawMessage.media_paths = [container_path]                       │
│    ▼                                                                │
│  Router: container_path → host_path (через volume mapping)          │
│    /home/node/.openclaw/workspace/output/report.xlsx                │
│    → /configs/<UUID>/workspace/output/report.xlsx                   │
│    ▼                                                                │
│  FileManager.upload_to_mattermost(host_path, channel_id)           │
│    driver.files.upload_file() → file_id                             │
│    ▼                                                                │
│  Mattermost пост с текстом + file_ids (вложение)                   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Shared Volume (общее для обоих маршрутов)

| Хост | Контейнер ws_router | Контейнер OpenClaw (UUID_A) |
|:---|:---|:---|
| `/root/openclaw/user-configs/` | `/configs/` | — |
| `/root/openclaw/user-configs/UUID_A/workspace/downloads/` | `/configs/UUID_A/workspace/downloads/` | `/home/node/.openclaw/workspace/downloads/` |
| `/root/openclaw/user-configs/UUID_A/workspace/output/` | `/configs/UUID_A/workspace/output/` | `/home/node/.openclaw/workspace/output/` |

---

## Изменения по файлам

### 1. `docker-compose.yml`

```yaml
services:
  ws-router:
    volumes:
      - /root/openclaw/user-configs:/configs   # ← ДОБАВИТЬ
```

---

### 2. `src/config.py`

```python
# ── Attachments ────────────────────────────────────────────────
workspace_base_path: str = Field(
    default="/configs",
    description="Base path where openclaw configs are mounted inside ws_router container.",
)
attachment_max_size_mb: int = Field(
    default=50,
    description="Max file size in MB for download from Mattermost.",
)
container_workspace_root: str = Field(
    default="/home/node/.openclaw/workspace",
    description="Workspace root path inside the OpenClaw container.",
)
```

**Производные пути** (вычисляются на лету, не в конфиге):

```python
# downloads — входящие файлы (Mattermost → OpenClaw)
host_downloads   = f"{settings.workspace_base_path}/{uuid}/workspace/downloads"
container_downloads = f"{settings.container_workspace_root}/downloads"

# output — исходящие файлы (OpenClaw → Mattermost)
host_output      = f"{settings.workspace_base_path}/{uuid}/workspace/output"
container_output = f"{settings.container_workspace_root}/output"
```

---

### 3. `src/file_manager.py` (новый модуль)

```python
import asyncio
import os
import time
from dataclasses import dataclass
from typing import Optional

import structlog
from mattermostdriver import Driver

from .config import settings

log = structlog.get_logger(__name__)


@dataclass
class DownloadedFile:
    filename: str
    host_path: str
    container_path: str
    size_bytes: int
    mime_type: str


class FileManager:
    def __init__(self, driver: Driver):
        self._driver = driver

    # ── Маршрут A: Mattermost → OpenClaw ──────────────────────────

    async def download_attachments(
        self,
        file_ids: list[str],
        uuid: str,
    ) -> list[DownloadedFile]:
        """Скачать файлы из Mattermost и положить в workspace/downloads/."""
        host_dir = f"{settings.workspace_base_path}/{uuid}/workspace/downloads"
        os.makedirs(host_dir, exist_ok=True)

        results = []
        max_bytes = settings.attachment_max_size_mb * 1024 * 1024

        for file_id in file_ids:
            try:
                f = await self._download_one(file_id, host_dir, max_bytes)
                if f:
                    results.append(f)
            except Exception as e:
                log.warning("attachment_download_error", file_id=file_id, error=str(e))

        return results

    async def _download_one(
        self,
        file_id: str,
        host_dir: str,
        max_bytes: int,
    ) -> Optional[DownloadedFile]:
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

        response = await asyncio.to_thread(
            self._driver.files.get_file, file_id
        )

        host_path = os.path.join(host_dir, filename)
        if os.path.exists(host_path):
            base, ext = os.path.splitext(filename)
            filename = f"{base}_{int(time.time())}{ext}"
            host_path = os.path.join(host_dir, filename)

        with open(host_path, "wb") as f:
            f.write(response.content)

        os.chown(host_path, 1000, 1000)

        container_path = (
            f"{settings.container_workspace_root}/downloads/{filename}"
        )

        log.info("attachment_downloaded", filename=filename,
                 size_bytes=size_bytes, mime_type=mime_type)

        return DownloadedFile(
            filename=filename,
            host_path=host_path,
            container_path=container_path,
            size_bytes=size_bytes,
            mime_type=mime_type,
        )

    # ── Маршрут B: OpenClaw → Mattermost ──────────────────────────

    async def upload_to_mattermost(
        self,
        host_path: str,
        channel_id: str,
    ) -> Optional[str]:
        """Загрузить файл в Mattermost, вернуть file_id."""
        if not os.path.isfile(host_path):
            log.warning("upload_file_not_found", host_path=host_path)
            return None

        file_size = os.path.getsize(host_path)
        max_bytes = settings.attachment_max_size_mb * 1024 * 1024
        if file_size > max_bytes:
            log.warning("upload_file_too_large", host_path=host_path,
                        size_mb=file_size // (1024 * 1024))
            return None

        try:
            result = await asyncio.to_thread(
                self._driver.files.upload_file,
                channel_id=channel_id,
                files={"files": (os.path.basename(host_path), open(host_path, "rb"))},
            )
            file_id = result["file_infos"][0]["id"]
            log.info("attachment_uploaded", host_path=host_path, file_id=file_id)
            return file_id
        except Exception as e:
            log.error("attachment_upload_error", host_path=host_path, error=str(e))
            return None


# ── Хелперы ────────────────────────────────────────────────────────

def build_attachment_context(files: list[DownloadedFile]) -> str:
    """Системный контекст с путями файлов для chat.send (Маршрут A)."""
    if not files:
        return ""
    lines = [
        f"- `{f.filename}` ({f.mime_type}, {f.size_bytes // 1024} KB)"
        f" → `{f.container_path}`"
        for f in files
    ]
    return (
        "\n\n[СИСТЕМА: Пользователь прикрепил файл(ы). "
        "Они доступны для чтения по следующим путям:]\n"
        + "\n".join(lines)
    )


def container_path_to_host(container_path: str, uuid: str) -> str:
    """Конвертировать путь внутри OpenClaw-контейнера в host-путь (Маршрут B)."""
    # /home/node/.openclaw/workspace/output/report.xlsx
    # → /configs/<UUID>/workspace/output/report.xlsx
    workspace_root = settings.container_workspace_root
    if not container_path.startswith(workspace_root):
        return ""
    relative = container_path[len(workspace_root):]  # /output/report.xlsx
    return f"{settings.workspace_base_path}/{uuid}/workspace{relative}"
```

---

### 4. `src/mattermost.py` — добавить `file_ids` в Event

```python
# MattermostEvent — добавить поле:
@dataclass
class MattermostEvent:
    user_id: str
    channel_id: str
    text: str
    post_id: str
    file_ids: list[str] = field(default_factory=list)  # ← ДОБАВИТЬ

# В парсере событий (где создаётся MattermostEvent):
file_ids = post.get("file_ids", [])
event = MattermostEvent(
    user_id=user_id,
    channel_id=channel_id,
    text=text,
    post_id=post_id,
    file_ids=file_ids,  # ← ДОБАВИТЬ
)

# Добавить метод отправки с вложениями:
async def send_post_with_files(
    self,
    channel_id: str,
    message: str,
    file_ids: list[str],
    root_id: str = "",
) -> None:
    """Отправить пост в Mattermost с file_ids вложениями."""
    await asyncio.to_thread(
        self._driver.posts.create_post,
        options={
            "channel_id": channel_id,
            "message": message,
            "root_id": root_id,
            "file_ids": file_ids,
        },
    )
```

---

### 5. `src/openclaw_client.py` — перехват `mediaUrls` из `chat.final`

**Изменение 1 — расширить `ClawMessage`:**
```python
@dataclass
class ClawMessage:
    msg_id: str
    seq: int
    text: str
    state: str
    ts: float
    media_paths: list[str] = field(default_factory=list)  # ← ДОБАВИТЬ
```

**Изменение 2 — читать `mediaUrls` в `_dispatch()`:**
```python
# В блоке `if event == "chat":` после строки text = ...

# Перехват исходящих файлов (Маршрут B)
media_paths: list[str] = []
if state == "final":
    raw_media = payload_dict.get("mediaUrls", [])
    if isinstance(raw_media, list):
        media_paths = [p for p in raw_media if isinstance(p, str) and p.strip()]
    single = payload_dict.get("mediaUrl", "")
    if isinstance(single, str) and single.strip() and single not in media_paths:
        media_paths.append(single.strip())

msg = ClawMessage(
    msg_id=msg_id,
    seq=seq,
    text=text,
    state=state,
    ts=time.time(),
    media_paths=media_paths,  # ← ДОБАВИТЬ
)
```

**Изменение 3 — пробросить `media_paths` через агрегатор:**

В `_select_best()` нужно сохранить `media_paths` от выбранного сообщения — они уже есть в `ClawMessage`, агрегатор просто возвращает объект целиком. Ничего менять не нужно.

---

### 6. `src/router.py` — центральная оркестрация

**В `__init__`:**
```python
self.file_manager = FileManager(driver=self._mattermost._driver)
```

**Вспомогательная функция:**
```python
import re

_UUID_RE = re.compile(
    r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
    re.IGNORECASE,
)

def _extract_uuid(instance_url: str) -> str | None:
    m = _UUID_RE.search(instance_url)
    return m.group(0) if m else None
```

**Маршрут A — в `handle_event()` перед `chat.send`:**
```python
attachment_context = ""

if event.file_ids:
    uuid = _extract_uuid(info.instance_url)
    if uuid:
        try:
            downloaded = await self.file_manager.download_attachments(
                event.file_ids, uuid
            )
            attachment_context = build_attachment_context(downloaded)
        except Exception as e:
            log.warning("attachment_download_failed", error=str(e))

# Финальный текст сообщения
message_text = event.text + attachment_context
if not event.text.strip() and attachment_context:
    message_text = "Пользователь отправил файл(ы) без текста." + attachment_context
```

**Маршрут B — после получения `claw_message` от ws_manager:**
```python
# claw_message — это ClawMessage из агрегатора
response_text = claw_message.text
uploaded_file_ids: list[str] = []

if claw_message.media_paths:
    uuid = _extract_uuid(info.instance_url)
    if uuid:
        for container_path in claw_message.media_paths:
            host_path = container_path_to_host(container_path, uuid)
            if host_path:
                fid = await self.file_manager.upload_to_mattermost(
                    host_path, event.channel_id
                )
                if fid:
                    uploaded_file_ids.append(fid)

if uploaded_file_ids:
    # Отправить пост с вложениями
    await self._mattermost.send_post_with_files(
        channel_id=event.channel_id,
        message=response_text,
        file_ids=uploaded_file_ids,
        root_id=event.post_id,
    )
else:
    # Обычная отправка текста
    await self._mattermost.send_reply(event.channel_id, response_text, event.post_id)
```

---

### 7. `.env.example`

```bash
# Attachments
WORKSPACE_BASE_PATH=/configs
ATTACHMENT_MAX_SIZE_MB=50
CONTAINER_WORKSPACE_ROOT=/home/node/.openclaw/workspace
```

---

## Порядок реализации

| # | Задача | Файл | Приоритет |
|:---|:---|:---|:---|
| 1 | Добавить volume в docker-compose | `docker-compose.yml` | 🔴 Критично |
| 2 | Добавить 3 поля в config | `src/config.py` | 🔴 Критично |
| 3 | Создать FileManager | `src/file_manager.py` | 🔴 Критично |
| 4 | Добавить `file_ids` в MattermostEvent | `src/mattermost.py` | 🔴 Критично |
| 5 | Добавить `send_post_with_files` | `src/mattermost.py` | 🔴 Критично |
| 6 | Добавить `media_paths` в ClawMessage | `src/claw_aggregator.py` | 🟡 Маршрут B |
| 7 | Читать `mediaUrls` в `_dispatch()` | `src/openclaw_client.py` | 🟡 Маршрут B |
| 8 | Вызов FileManager в handle_event | `src/router.py` | 🔴 Критично |
| 9 | Upload + send с file_ids | `src/router.py` | 🟡 Маршрут B |

**Рекомендуемая последовательность:**
1. Сначала **Маршрут A** (шаги 1–5, 8) — более простой и сразу полезный
2. Затем **Маршрут B** (шаги 6–7, 9) — требует проверки что `mediaUrls` реально приходит в payload

---

## Edge-cases

| Ситуация | Поведение |
|:---|:---|
| `file_ids` пустой | Пропустить, отправить только текст |
| Файл > `ATTACHMENT_MAX_SIZE_MB` | Пропустить файл, warning в лог |
| UUID не найден в `instance_url` | Warning в лог, отправить только текст |
| Ошибка скачивания из Mattermost | Warning в лог, отправить только текст |
| Текст пустой (только файл) | Подставить дефолтный текст |
| Имя файла уже занято | Добавить суффикс `_{timestamp}` |
| `mediaUrls` пустой в `chat.final` | Обычная отправка текста |
| Файл в `output/` не существует | Warning, игнорировать |
| Путь вне `workspace_root` | Игнорировать (безопасность) |
| `os.chown` недоступен (не root) | Обернуть в try/except, продолжить |

---

## Проверка после деплоя

```bash
# 1. Убедиться что volume смонтирован
docker inspect ws_router | python3 -c "
import json,sys; d=json.load(sys.stdin)
mounts = d[0]['Mounts']
for m in mounts: print(m['Source'], '->', m['Destination'])
"

# 2. Проверить Маршрут A: отправить файл боту в Mattermost
# → В логах должно появиться: attachment_downloaded, filename=...
# → В папке должен появиться файл:
ls -la /root/openclaw/user-configs/<UUID>/workspace/downloads/

# 3. Проверить Маршрут B: включить RAW_WS_DUMP и посмотреть payload
RAW_WS_DUMP=1 docker compose up ws_router
tail -f /tmp/ws_raw_dump.jsonl | python3 -m json.tool | grep -A5 mediaUrl
# → Убедиться что mediaUrls реально приходит в chat.final payload
```

> ⚠️ **Важно перед Маршрутом B:** Включить `RAW_WS_DUMP=1` и убедиться что `mediaUrls`
> действительно присутствует в `chat.final` payload когда агент генерирует файл.
> Если поле отсутствует — использовать Вариант А (маркер `[FILE:путь]`).

---

## Новых зависимостей нет

`mattermostdriver` уже умеет:
- `driver.files.get_file_metadata(file_id)` 
- `driver.files.get_file(file_id)`
- `driver.files.upload_file(channel_id, files=...)`
- `driver.posts.create_post(options={..., "file_ids": [...]})`

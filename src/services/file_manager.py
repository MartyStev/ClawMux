"""
ClawMux — File Manager.

Handles bidirectional file transfer between Mattermost and OpenClaw
via shared Docker volume mounted at WORKSPACE_BASE_PATH.

Route A (Mattermost → OpenClaw):
  Downloads files from Mattermost API and saves them to the user's
  workspace/downloads/ folder, accessible inside the OpenClaw container.

Route B (OpenClaw → Mattermost):
  Reads files from the user's workspace/output/ folder (written by the
  OpenClaw agent) and uploads them to Mattermost as post attachments.
"""

import asyncio
import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import aiofiles
import httpx
import structlog

from src.core.config import settings

log = structlog.get_logger(__name__)

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class DownloadedFile:
    """A file downloaded from Mattermost and saved to the shared volume."""
    filename: str           # "report.pdf"
    host_path: str          # "/configs/<UUID>/workspace/downloads/report.pdf"
    container_path: str     # "/home/node/.openclaw/workspace/downloads/report.pdf"
    size_bytes: int
    mime_type: str          # "application/pdf"


# ── Helper functions ───────────────────────────────────────────────────────────

def extract_uuid_from_instance_url(instance_url: str) -> Optional[str]:
    """Extract UUID from an OpenClaw instance URL (e.g. http://host/<uuid>/...)."""
    m = _UUID_RE.search(instance_url)
    return m.group(0) if m else None


def container_path_to_host(container_path: str, uuid: str) -> str:
    """
    Convert an absolute path inside the OpenClaw container to the
    corresponding host path accessible through the shared volume.

    Example:
      /home/node/.openclaw/workspace/output/report.xlsx
      → /configs/<UUID>/workspace/output/report.xlsx
      
      /home/node/.openclaw/canvas/documents/kazan_may_2025/index.html
      → /configs/<UUID>/canvas/documents/kazan_may_2025/index.html
    """
    openclaw_root = "/home/node/.openclaw/"
    if not container_path.startswith(openclaw_root):
        log.warning(
            "container_path_outside_openclaw_root",
            container_path=container_path,
            openclaw_root=openclaw_root,
        )
        return ""
    relative = container_path[len(openclaw_root):]  # e.g. workspace/output/report.xlsx
    return f"{settings.workspace_base_path}/{uuid}/{relative}"


def build_attachment_context(files: list[DownloadedFile]) -> str:
    """
    Build a system context string listing downloaded file paths.
    This is appended to the user's message before sending to OpenClaw (Route A).
    """
    if not files:
        return ""
    lines = [
        f"- `{f.filename}` ({f.mime_type}, {f.size_bytes // 1024} KB)"
        f" → `{f.container_path}`"
        for f in files
    ]
    return (
        "\n\n[SYSTEM: the user attached one or more files. "
        "They are available to read at the following paths:]\n"
        + "\n".join(lines)
    )


# ── FileManager ────────────────────────────────────────────────────────────────

class FileManager:
    """
    Manages file transfers between Mattermost and OpenClaw via shared volume.
    """

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http_client = http_client
        self._log = log.bind(component="FileManager")

    # ── Route A: Mattermost → OpenClaw ────────────────────────────────────────

    async def download_attachments(
        self,
        file_ids: list[str],
        uuid: str,
    ) -> list[DownloadedFile]:
        """
        Download files from Mattermost and save them to workspace/downloads/.
        Skips files that exceed ATTACHMENT_MAX_SIZE_MB.
        Directories are created automatically on first write.
        """
        if not file_ids:
            return []

        host_dir = f"{settings.workspace_base_path}/{uuid}/workspace/downloads"
        os.makedirs(host_dir, exist_ok=True)

        results: list[DownloadedFile] = []
        max_bytes = settings.attachment_max_size_mb * 1024 * 1024

        for file_id in file_ids:
            try:
                downloaded = await self._download_one(file_id, host_dir, max_bytes)
                if downloaded:
                    results.append(downloaded)
            except httpx.HTTPError as e:
                self._log.warning(
                    "attachment_download_error",
                    file_id=file_id,
                    error=str(e),
                )

        return results

    async def _download_one(
        self,
        file_id: str,
        host_dir: str,
        max_bytes: int,
    ) -> Optional[DownloadedFile]:
        """Download a single file by Mattermost file_id."""
        # Fetch file metadata first to check size and get filename
        resp = await self._http_client.get(f"/files/{file_id}/info")
        resp.raise_for_status()
        meta = resp.json()
        filename: str = meta.get("name") or file_id
        size_bytes: int = int(meta.get("size", 0))
        mime_type: str = meta.get("mime_type", "application/octet-stream")

        if size_bytes > max_bytes:
            self._log.warning(
                "attachment_too_large",
                filename=filename,
                size_mb=round(size_bytes / (1024 * 1024), 1),
                limit_mb=settings.attachment_max_size_mb,
            )
            return None

        # Resolve final filename (avoid collisions)
        host_path = os.path.join(host_dir, filename)
        if os.path.exists(host_path):
            base, ext = os.path.splitext(filename)
            import uuid as _uuid
            filename = f"{base}_{_uuid.uuid4().hex[:6]}{ext}"
            host_path = os.path.join(host_dir, filename)

        # Download binary content directly to disk via stream
        async with self._http_client.stream("GET", f"/files/{file_id}") as response:
            response.raise_for_status()
            async with aiofiles.open(host_path, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    await f.write(chunk)

        # Make file readable/writable by everyone so container can access it
        try:
            os.chmod(host_path, 0o666)
        except OSError as e:
            self._log.warning("chmod_failed", error=str(e))

        container_path = (
            f"{settings.container_workspace_root}/downloads/{filename}"
        )

        self._log.info(
            "attachment_downloaded",
            filename=filename,
            size_bytes=size_bytes,
            mime_type=mime_type,
            host_path=host_path,
        )

        return DownloadedFile(
            filename=filename,
            host_path=host_path,
            container_path=container_path,
            size_bytes=size_bytes,
            mime_type=mime_type,
        )

    # ── Route B: OpenClaw → Mattermost ────────────────────────────────────────

    async def upload_to_mattermost(
        self,
        host_path: str,
        channel_id: str,
    ) -> Optional[str]:
        """
        Upload a file from the shared volume to Mattermost.
        Returns the Mattermost file_id on success, None on failure.
        """
        if not os.path.isfile(host_path):
            self._log.warning("upload_file_not_found", host_path=host_path)
            return None

        file_size = os.path.getsize(host_path)
        max_bytes = settings.attachment_max_size_mb * 1024 * 1024
        if file_size > max_bytes:
            self._log.warning(
                "upload_file_too_large",
                host_path=host_path,
                size_mb=round(file_size / (1024 * 1024), 1),
                limit_mb=settings.attachment_max_size_mb,
            )
            return None

        filename = os.path.basename(host_path)
        try:
            async with aiofiles.open(host_path, "rb") as fh:
                content = await fh.read()
            
            resp = await self._http_client.post(
                "/files",
                data={"channel_id": channel_id},
                files={"files": (filename, content)}
            )
            resp.raise_for_status()
            result = resp.json()
            file_id: str = result["file_infos"][0]["id"]
            self._log.info(
                "attachment_uploaded",
                filename=filename,
                file_id=file_id,
                channel_id=channel_id,
            )
            return file_id
        except httpx.HTTPError as e:
            self._log.error(
                "attachment_upload_error",
                host_path=host_path,
                error=str(e),
            )
            return None

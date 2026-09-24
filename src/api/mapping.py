"""
ClawMux — Mapping Administration API.

POST /api/v1/mappings/reload

Invalidate in-memory mapping caches (identity and external_id lookups).
Used by external deployment scripts or admin operations after updating DB mappings.
Authentication: `X-Api-Token` header.
"""

import secrets

import structlog
from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel

from src.core.config import settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["mappings"])


class ReloadMappingResponse(BaseModel):
    status: str
    message: str


@router.post("/mappings/reload", response_model=ReloadMappingResponse)
async def reload_mappings(
    request: Request,
    x_api_token: str = Header(..., alias="x-api-token"),
) -> ReloadMappingResponse:
    """
    Invalidate in-memory mapping caches.
    """
    if not settings.api_token or not secrets.compare_digest(x_api_token, settings.api_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
        )

    mapping = getattr(request.app.state, "mapping", None)
    if mapping is not None:
        version = await mapping.reload_cache_version()
        logger.info("mapping_cache_invalidated", mapping_version=version)

    return ReloadMappingResponse(
        status="ok",
        message="mapping cache invalidated",
    )

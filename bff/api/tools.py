from __future__ import annotations

from fastapi import APIRouter

from bff.core.response import ok
from bff.services.container import tooling_service

router = APIRouter(prefix='/api/v1', tags=['mcp'])


@router.get('/mcp/catalog')
@router.get('/tools')  # Backward-compatible alias
async def list_mcp_catalog() -> dict:
    return ok(tooling_service.list_tools())

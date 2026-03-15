from __future__ import annotations

from fastapi import APIRouter, Query

from bff.core.response import ok
from bff.services.container import runtime_observability_service

router = APIRouter(prefix='/api/v1/runtime', tags=['runtime'])


@router.get('/mcp-routing/events')
async def list_mcp_routing_events(
    limit: int = Query(default=100, ge=1, le=500),
    eventType: str | None = Query(default=None),
    days: int = Query(default=1, ge=1, le=30),
) -> dict:
    events = runtime_observability_service.list_events(
        limit=limit,
        event_type=eventType,
        days=days,
    )
    return ok(events)


@router.get('/mcp-routing/dashboard')
async def get_mcp_routing_dashboard(
    days: int = Query(default=7, ge=1, le=30),
) -> dict:
    return ok(runtime_observability_service.dashboard(days=days))


@router.delete('/mcp-routing/legacy')
async def purge_legacy_mcp_routing_events() -> dict:
    deleted_count = runtime_observability_service.purge_legacy_events()
    return ok({"deletedCount": deleted_count})

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from bff.core.response import err, ok
from bff.domain.models import McpServerCreate, McpServerUpdate
from bff.services.container import tooling_service

router = APIRouter(prefix='/api/v1/mcp/servers', tags=['mcp'])


@router.get('')
async def list_mcp_servers() -> dict:
    return ok(tooling_service.list_mcp_servers())


@router.post('')
async def create_mcp_server(payload: McpServerCreate) -> dict:
    try:
        return ok(tooling_service.create_mcp_server(payload))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=err(str(exc))) from exc


@router.put('/{server_id}')
async def update_mcp_server(server_id: str, payload: McpServerUpdate) -> dict:
    result = tooling_service.update_mcp_server(server_id, payload)
    if result is None:
        raise HTTPException(status_code=404, detail=err('MCP Server 不存在'))
    return ok(result)


@router.delete('/{server_id}')
async def delete_mcp_server(server_id: str) -> dict:
    return ok(tooling_service.delete_mcp_server(server_id))


@router.post('/{server_id}/discover')
async def discover_mcp_server_tools(server_id: str) -> dict:
    try:
        result = await tooling_service.discover_mcp_server_tools(server_id)
        if result is None:
            raise HTTPException(status_code=404, detail=err('MCP Server 不存在'))
        return ok(result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=err(str(exc))) from exc


@router.get('/{server_id}/tools')
async def list_mcp_server_tools(server_id: str) -> dict:
    result = tooling_service.list_mcp_server_tools(server_id)
    if result is None:
        raise HTTPException(status_code=404, detail=err('MCP Server 不存在'))
    return ok(result)

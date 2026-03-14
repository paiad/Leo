from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from bff.core.response import err, ok
from bff.domain.models import ChatRequest, CreateSessionRequest
from bff.services.container import chat_service

router = APIRouter(prefix='/api/v1/chat', tags=['chat'])


@router.get('/models')
async def chat_models() -> dict:
    return ok(chat_service.model_config())


@router.get('/system-prompt')
async def chat_system_prompt() -> dict:
    return ok({'platformPrompt': chat_service.platform_prompt()})


@router.get('/sessions')
async def list_sessions() -> dict:
    return ok(chat_service.list_sessions())


@router.post('/sessions')
async def create_session(payload: CreateSessionRequest | None = None) -> dict:
    title = payload.title if payload else None
    source = payload.source if payload else "browser"
    return ok(chat_service.create_session(title, source=source))


@router.get('/sessions/{session_id}/messages')
async def list_session_messages(session_id: str) -> dict:
    messages = chat_service.get_session_messages(session_id)
    if messages is None:
        raise HTTPException(status_code=404, detail=err('会话不存在'))
    return ok(messages)


@router.delete('/sessions/{session_id}/messages/{message_id}')
async def delete_session_message(session_id: str, message_id: str) -> dict:
    deleted = chat_service.delete_session_message(session_id, message_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail=err('会话不存在'))
    return ok({'deleted': deleted})


@router.delete('/sessions/{session_id}/messages')
async def clear_session_messages(session_id: str) -> dict:
    deleted_count = chat_service.clear_session_messages(session_id)
    if deleted_count is None:
        raise HTTPException(status_code=404, detail=err('会话不存在'))
    return ok({'deletedCount': deleted_count})


async def _chat_completion(payload: ChatRequest, stream: bool = False):
    if stream:
        return StreamingResponse(
            chat_service.stream_message(payload),
            media_type='text/event-stream',
        )
    try:
        return await chat_service.send_message(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=err(str(exc))) from exc


@router.post('/completions')
async def create_chat_completion(payload: ChatRequest, stream: bool = False):
    return await _chat_completion(payload, stream=stream)


@router.post('/messages')  # Backward-compatible alias
async def chat_messages(payload: ChatRequest) -> dict:
    return await _chat_completion(payload, stream=False)


@router.post('/completions/stream')  # Backward-compatible alias
@router.post('/stream')  # Backward-compatible alias
async def stream_chat_completion(payload: ChatRequest):
    return await _chat_completion(payload, stream=True)

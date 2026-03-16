from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Request

from app.logger import logger
from bff.api.integration.feishu_audio_asr import LocalAsrEngine
from bff.api.integration.feishu_messaging import (
    send_text_message as _send_text_message_impl,
    send_text_message_dedup as _send_text_message_dedup_impl,
    split_text as _split_text_impl,
)
from bff.api.integration.feishu_progress import (
    format_step_progress_message as _format_step_progress_message_impl,
    format_thinking_progress_message as _format_thinking_progress_message_impl,
    normalize_progress_mode as _normalize_progress_mode_impl,
    parse_sse_message as _parse_sse_message_impl,
    stream_with_progress_reply,
)
from bff.domain.models import ChatRequest
from bff.services.container import chat_service
from bff.utils.env import get_env

router = APIRouter(prefix="/api/v1/feishu", tags=["feishu"])

_FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
_FEISHU_SEND_MESSAGE_URL = "https://open.feishu.cn/open-apis/im/v1/messages"
_FEISHU_GET_MESSAGE_RESOURCE_URL = (
    "https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{file_key}"
)


def _env_bool(name: str, default: bool) -> bool:
    raw = get_env(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_sse_message(raw: str) -> tuple[str, dict[str, Any]] | None:
    return _parse_sse_message_impl(raw)


def _format_step_progress_message(payload: dict[str, Any]) -> str:
    return _format_step_progress_message_impl(payload)


def _progress_mode() -> str:
    mode = str(get_env("FEISHU_PROGRESS_MODE", "steps") or "steps")
    return _normalize_progress_mode_impl(mode)


def _format_thinking_progress_message(payload: dict[str, Any]) -> str:
    try:
        max_len = int(str(get_env("FEISHU_THOUGHTS_MAX_CHARS", "220") or "220"))
    except ValueError:
        max_len = 220
    return _format_thinking_progress_message_impl(payload, max_chars=max_len)


def _clear_current_task_cancellation() -> None:
    task = asyncio.current_task()
    if task is None or not hasattr(task, "uncancel"):
        return
    while task.cancelling():
        task.uncancel()


def _verify_token(token: str | None) -> bool:
    expected = str(get_env("FEISHU_VERIFICATION_TOKEN", "") or "").strip()
    if not expected:
        return True
    return bool(token and token == expected)


def _split_text(text: str, max_len: int = 3000) -> list[str]:
    return _split_text_impl(text, max_len=max_len)


class _TokenStore:
    def __init__(self) -> None:
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> str:
        async with self._lock:
            now = time.time()
            if self._token and now < self._expires_at:
                return self._token

            app_id = str(get_env("FEISHU_APP_ID", "") or "").strip()
            app_secret = str(get_env("FEISHU_APP_SECRET", "") or "").strip()
            if not app_id or not app_secret:
                raise RuntimeError(
                    "Missing FEISHU_APP_ID or FEISHU_APP_SECRET. "
                    "Set them in environment variables before enabling Feishu webhook."
                )

            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    _FEISHU_TOKEN_URL,
                    json={"app_id": app_id, "app_secret": app_secret},
                )
            response.raise_for_status()
            payload = response.json()
            if payload.get("code", 0) != 0:
                raise RuntimeError(
                    f"Failed to get tenant_access_token: {payload.get('msg', 'unknown error')}"
                )
            token = str(payload.get("tenant_access_token") or "").strip()
            if not token:
                raise RuntimeError("Feishu auth response does not contain tenant_access_token")

            expire = int(payload.get("expire") or 7200)
            # Refresh token before actual expiration.
            self._expires_at = now + max(60, expire - 120)
            self._token = token
            return token


class _MessageDeduper:
    def __init__(self, ttl_seconds: int = 600) -> None:
        self._ttl = ttl_seconds
        self._seen: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def exists(self, message_id: str) -> bool:
        now = time.time()
        async with self._lock:
            expired = [key for key, expires_at in self._seen.items() if expires_at <= now]
            for key in expired:
                self._seen.pop(key, None)

            if message_id in self._seen:
                return True
            self._seen[message_id] = now + self._ttl
            return False


class _SessionMap:
    def __init__(self) -> None:
        self._map: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, chat_id: str) -> str:
        target_title = f"Feishu-{chat_id}"
        async with self._lock:
            existing = self._map.get(chat_id)
            if existing:
                return existing

            restored = sorted(
                (
                    session
                    for session in chat_service._store.sessions.values()  # type: ignore[attr-defined]
                    if (session.title or "").strip().lower() == target_title.lower()
                ),
                key=lambda item: item.updatedAt,
                reverse=True,
            )
            if restored:
                session_id = str(restored[0].id)
                self._map[chat_id] = session_id
                return session_id

            session = chat_service.create_session(title=target_title, source="lark")
            session_id = str(session["id"])
            self._map[chat_id] = session_id
            return session_id


_token_store = _TokenStore()
_deduper = _MessageDeduper()
_session_map = _SessionMap()
_local_asr_engine = LocalAsrEngine()


async def _send_text_message(chat_id: str, text: str) -> None:
    await _send_text_message_impl(
        chat_id,
        text,
        get_token=_token_store.get,
        send_url=_FEISHU_SEND_MESSAGE_URL,
    )


async def _send_text_message_dedup(
    chat_id: str,
    text: str,
    *,
    last_sent: str | None,
) -> str | None:
    return await _send_text_message_dedup_impl(
        chat_id,
        text,
        last_sent=last_sent,
        send_func=_send_text_message,
    )


def _extract_user_text(message: dict[str, Any]) -> str:
    msg_type = str(message.get("message_type") or "").strip()
    content_raw = str(message.get("content") or "").strip()
    if not content_raw:
        return ""

    try:
        content_obj = json.loads(content_raw)
    except json.JSONDecodeError:
        return content_raw

    if msg_type == "text":
        return str(content_obj.get("text") or "").strip()
    return ""


def _extract_audio_file_key(message: dict[str, Any]) -> str:
    content_raw = str(message.get("content") or "").strip()
    if not content_raw:
        return ""
    try:
        content_obj = json.loads(content_raw)
    except json.JSONDecodeError:
        return ""
    return str(content_obj.get("file_key") or "").strip()


async def _download_audio_resource(message_id: str, file_key: str) -> bytes:
    token = await _token_store.get()
    headers = {"Authorization": f"Bearer {token}"}
    resource_url = _FEISHU_GET_MESSAGE_RESOURCE_URL.format(
        message_id=message_id,
        file_key=file_key,
    )
    async with httpx.AsyncClient(timeout=40.0) as client:
        response = await client.get(
            resource_url,
            # Feishu message resource API uses `type=file` for audio/video/file resources.
            params={"type": "file"},
            headers=headers,
        )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body_preview = ""
        try:
            body_preview = response.text[:500]
        except Exception:
            body_preview = "<unavailable>"
        raise RuntimeError(
            "Feishu audio resource HTTP error: "
            f"status={response.status_code}, body={body_preview}"
        ) from exc

    content_type = str(response.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        payload = response.json()
        raise RuntimeError(
            "Feishu audio resource download failed: "
            f"code={payload.get('code')}, msg={payload.get('msg')}"
        )
    return response.content


async def _resolve_user_input_text(message: dict[str, Any]) -> str:
    msg_type = str(message.get("message_type") or "").strip()
    if msg_type == "text":
        return _extract_user_text(message)
    if msg_type != "audio":
        return ""
    if not _env_bool("FEISHU_ENABLE_AUDIO_ASR", True):
        return ""

    message_id = str(message.get("message_id") or "").strip()
    file_key = _extract_audio_file_key(message)
    if not message_id or not file_key:
        logger.warning(
            "Skip Feishu audio ASR due to missing message_id or file_key: "
            f"message_id={message_id}, has_file_key={bool(file_key)}"
        )
        return ""
    audio_bytes = await _download_audio_resource(message_id=message_id, file_key=file_key)
    if not audio_bytes:
        return ""
    return await _local_asr_engine.transcribe_audio(audio_bytes)


def _should_reply(event: dict[str, Any], message: dict[str, Any]) -> bool:
    sender = event.get("sender") or {}
    sender_type = str(sender.get("sender_type") or "")
    # Avoid bot self-loop or app-originated events.
    if sender_type and sender_type != "user":
        logger.info(
            "Skip Feishu event due to sender type: "
            f"sender_type={sender_type}, message_id={message.get('message_id')}"
        )
        return False

    chat_type = str(message.get("chat_type") or "")
    reply_only_when_mentioned = _env_bool("FEISHU_REPLY_ONLY_WHEN_MENTIONED", True)
    if chat_type == "group" and reply_only_when_mentioned:
        mentions = message.get("mentions") or []
        if not mentions:
            logger.info(
                "Skip Feishu group message without mention while FEISHU_REPLY_ONLY_WHEN_MENTIONED=true: "
                f"chat_id={message.get('chat_id')}, message_id={message.get('message_id')}"
            )
        return bool(mentions)
    return True


async def _handle_receive_event(event: dict[str, Any]) -> None:
    message = event.get("message") or {}
    chat_id = str(message.get("chat_id") or "").strip()
    message_id = str(message.get("message_id") or "").strip()
    chat_type = str(message.get("chat_type") or "").strip()
    msg_type = str(message.get("message_type") or "").strip()
    logger.info(
        "Feishu incoming event received: "
        f"chat_id={chat_id}, chat_type={chat_type}, message_id={message_id}, msg_type={msg_type}"
    )
    if not chat_id:
        logger.warning("Skip Feishu event: missing chat_id")
        return

    if not _should_reply(event, message):
        return

    try:
        user_text = await _resolve_user_input_text(message)
    except Exception:
        logger.exception(
            "Failed to resolve Feishu message content: "
            f"chat_id={chat_id}, message_id={message_id}, msg_type={msg_type}"
        )
        try:
            if msg_type == "audio":
                await _send_text_message(chat_id, "语音识别失败，请重试或改发文字。")
            else:
                await _send_text_message(chat_id, "处理消息时发生错误，请稍后重试。")
        except Exception:
            logger.exception(
                "Failed to send Feishu content-resolve fallback message: "
                f"chat_id={chat_id}, message_id={message_id}"
            )
        return

    if not user_text:
        msg_type = str(message.get("message_type") or "")
        logger.info(
            "Feishu message has no extractable text content: "
            f"chat_id={chat_id}, message_id={message_id}, msg_type={msg_type}"
        )
        if msg_type == "audio":
            await _send_text_message(chat_id, "语音识别失败，请重试或改发文字。")
        elif msg_type and msg_type != "text":
            await _send_text_message(chat_id, "目前仅支持文本和语音消息。")
        return

    if msg_type == "audio":
        preview = user_text if len(user_text) <= 300 else f"{user_text[:300]}...(truncated)"
        logger.info(
            "Feishu audio ASR transcript: "
            f"chat_id={chat_id}, message_id={message_id}, text={preview}"
        )

    session_id = await _session_map.get_or_create(chat_id)
    logger.info(
        "Feishu message accepted for model processing: "
        f"chat_id={chat_id}, message_id={message_id}, session_id={session_id}, text_len={len(user_text)}"
    )
    try:
        send_step_progress = _env_bool("FEISHU_SEND_STEP_PROGRESS", False)
        if not send_step_progress:
            result = await chat_service.send_message(
                ChatRequest(
                    content=user_text,
                    sessionId=session_id,
                    source="lark",
                    userInputType="audio_asr" if msg_type == "audio" else "text",
                )
            )
            assistant_text = str((result.get("data") or {}).get("content") or "").strip()
            if not assistant_text:
                assistant_text = "收到消息，但模型返回了空内容。"
            await _send_text_message(chat_id, assistant_text)
            return

        mode = _progress_mode()
        try:
            max_progress = int(str(get_env("FEISHU_MAX_STEP_PROGRESS", "60") or "60"))
        except ValueError:
            max_progress = 60
        try:
            thinking_max_chars = int(
                str(get_env("FEISHU_THOUGHTS_MAX_CHARS", "220") or "220")
            )
        except ValueError:
            thinking_max_chars = 220

        handled = await stream_with_progress_reply(
            raw_events=chat_service.stream_message(
                ChatRequest(
                    content=user_text,
                    sessionId=session_id,
                    source="lark",
                    userInputType="audio_asr" if msg_type == "audio" else "text",
                )
            ),
            send_text=lambda text: _send_text_message(chat_id, text),
            send_text_dedup=lambda text, last_sent: _send_text_message_dedup(
                chat_id,
                text,
                last_sent=last_sent,
            ),
            mode=mode,
            max_progress=max_progress,
            thinking_max_chars=thinking_max_chars,
            fallback_error_message="处理消息时发生错误，请稍后重试。",
            fallback_empty_message="收到消息，但模型返回了空内容。",
        )
        if not handled:
            return
    except asyncio.CancelledError:
        logger.warning(
            "Feishu message handling cancelled: "
            f"chat_id={chat_id}, message_id={message_id}"
        )
        _clear_current_task_cancellation()
        try:
            await _send_text_message(chat_id, "当前请求处理中断，请重试一次。")
        except Exception:
            logger.exception(
                "Failed to send Feishu cancellation fallback message: "
                f"chat_id={chat_id}, message_id={message_id}"
            )
        return
    except Exception:
        logger.exception("Failed to handle Feishu incoming message")
        try:
            await _send_text_message(chat_id, "处理消息时发生错误，请稍后重试。")
        except Exception:
            logger.exception(
                "Failed to send Feishu fallback error message: "
                f"chat_id={chat_id}, message_id={message_id}"
            )


async def is_duplicate_message(message_id: str) -> bool:
    """Check whether this Feishu message ID has already been processed."""
    return await _deduper.exists(message_id)


async def handle_message_receive_event(event: dict[str, Any]) -> None:
    """Handle a parsed im.message.receive_v1 event payload."""
    await _handle_receive_event(event)


@router.post("/events")
async def receive_feishu_events(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    body = await request.json()

    # Encrypt Key mode payload ({"encrypt": "..."}) is not handled in this lightweight adapter.
    if "encrypt" in body and "type" not in body:
        logger.error(
            "Received encrypted Feishu event payload, but decrypt logic is not configured. "
            "Disable Encrypt Key or implement decrypt flow before using encrypted callback."
        )
        return {"code": 1, "msg": "encrypted payload is not supported"}

    # URL verification request
    if body.get("type") == "url_verification":
        if not _verify_token(body.get("token")):
            logger.warning("Feishu url_verification token mismatch")
            return {"code": 1, "msg": "invalid token"}
        return {"challenge": body.get("challenge", "")}

    header = body.get("header") or {}
    event_type = str(header.get("event_type") or "")
    if event_type != "im.message.receive_v1":
        return {"code": 0}

    if not _verify_token(header.get("token")):
        logger.warning("Feishu event token mismatch")
        return {"code": 1, "msg": "invalid token"}

    event = body.get("event") or {}
    message = event.get("message") or {}
    message_id = str(message.get("message_id") or "").strip()
    if not message_id:
        return {"code": 0}

    if await _deduper.exists(message_id):
        return {"code": 0}

    background_tasks.add_task(_handle_receive_event, event)
    return {"code": 0}

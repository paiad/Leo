from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

import httpx


def verify_token(token: str | None, expected_token: str) -> bool:
    expected = str(expected_token or "").strip()
    if not expected:
        return True
    return bool(token and token == expected)


class TokenStore:
    def __init__(self, *, token_url: str) -> None:
        self._token_url = token_url
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get(
        self,
        *,
        app_id: str,
        app_secret: str,
    ) -> str:
        async with self._lock:
            now = time.time()
            if self._token and now < self._expires_at:
                return self._token

            if not app_id or not app_secret:
                raise RuntimeError(
                    "Missing FEISHU_APP_ID or FEISHU_APP_SECRET. "
                    "Set them in environment variables before enabling Feishu webhook."
                )

            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    self._token_url,
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
            self._expires_at = now + max(60, expire - 120)
            self._token = token
            return token


class MessageDeduper:
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


class SessionMap:
    def __init__(
        self,
        *,
        list_sessions: Callable[[], list[Any]],
        create_session: Callable[[str], str],
    ) -> None:
        self._list_sessions = list_sessions
        self._create_session = create_session
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
                    for session in self._list_sessions()
                    if (getattr(session, "title", "") or "").strip().lower()
                    == target_title.lower()
                ),
                key=lambda item: getattr(item, "updatedAt", ""),
                reverse=True,
            )
            if restored:
                session_id = str(getattr(restored[0], "id"))
                self._map[chat_id] = session_id
                return session_id

            session_id = str(self._create_session(target_title))
            self._map[chat_id] = session_id
            return session_id

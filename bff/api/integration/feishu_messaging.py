from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

import httpx

from app.logger import logger


def split_text(text: str, max_len: int = 3000) -> list[str]:
    if not text:
        return [""]
    return [text[i : i + max_len] for i in range(0, len(text), max_len)]


async def send_text_message(
    chat_id: str,
    text: str,
    *,
    get_token: Callable[[], Awaitable[str]],
    send_url: str,
) -> None:
    token = await get_token()
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=20.0) as client:
        for chunk in split_text(text):
            payload = {
                "receive_id": chat_id,
                "msg_type": "text",
                "content": json.dumps({"text": chunk}, ensure_ascii=False),
            }
            response = await client.post(
                send_url,
                params={"receive_id_type": "chat_id"},
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            if data.get("code", 0) != 0:
                raise RuntimeError(
                    "Feishu send message failed: "
                    f"code={data.get('code')}, msg={data.get('msg', 'unknown error')}"
                )
            logger.info(
                "Feishu reply sent successfully: "
                f"chat_id={chat_id}, message_id={((data.get('data') or {}).get('message_id') or '')}"
            )


async def send_text_message_dedup(
    chat_id: str,
    text: str,
    *,
    last_sent: str | None,
    send_func: Callable[[str, str], Awaitable[None]],
) -> str | None:
    normalized = str(text or "").strip()
    if not normalized:
        return last_sent
    if last_sent is not None and normalized == last_sent:
        return last_sent
    await send_func(chat_id, normalized)
    return normalized

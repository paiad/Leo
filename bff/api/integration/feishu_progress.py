from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from collections.abc import AsyncIterable, Awaitable, Callable
from typing import Any


def parse_sse_message(raw: str) -> tuple[str, dict[str, Any]] | None:
    if not raw:
        return None
    event_type: str | None = None
    data_raw: str | None = None
    for line in raw.strip().splitlines():
        if line.startswith("event:"):
            event_type = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_raw = line[len("data:") :].strip()
    if not event_type:
        return None
    if not data_raw:
        return event_type, {}
    try:
        payload = json.loads(data_raw)
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return event_type, payload


def format_step_progress_message(payload: dict[str, Any]) -> str:
    step = payload.get("step")
    try:
        step_num = int(step)
    except Exception:
        step_num = None
    if step_num is not None and step_num > 0:
        return f"执行步骤 {step_num}"
    return "执行步骤"


def normalize_progress_mode(mode: str) -> str:
    normalized = str(mode or "steps").strip().lower()
    if normalized in {"steps", "thoughts", "both"}:
        return normalized
    return "steps"


def format_thinking_progress_message(payload: dict[str, Any], *, max_chars: int) -> str:
    message = str(payload.get("message") or "").strip()
    if not message:
        return ""
    if "[truncated]" in message:
        return ""
    if max_chars > 0 and len(message) > max_chars:
        return ""
    return message


def _normalize_for_similarity(text: str) -> str:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return ""
    # Drop markdown markers and punctuation/noise so formatting differences
    # (line breaks, bullets, emphasis) do not bypass dedup.
    normalized = normalized.replace("**", " ")
    normalized = re.sub(r"[\r\n\t]+", " ", normalized)
    normalized = re.sub(r"[-*#>`~_]+", " ", normalized)
    normalized = re.sub(
        r"[，。！？；：、“”‘’（）()【】\[\],.!?;:\"'…·/\\|]+",
        " ",
        normalized,
    )
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def is_semantically_similar(a: str, b: str, *, threshold: float = 0.88) -> bool:
    left = _normalize_for_similarity(a)
    right = _normalize_for_similarity(b)
    if not left or not right:
        return False
    if left == right:
        return True

    shorter = left if len(left) <= len(right) else right
    longer = right if shorter is left else left
    if shorter and shorter in longer and (len(shorter) / max(1, len(longer))) >= 0.65:
        return True

    ratio = SequenceMatcher(None, left, right).ratio()
    return ratio >= threshold


async def stream_with_progress_reply(
    *,
    raw_events: AsyncIterable[str],
    send_text: Callable[[str], Awaitable[None]],
    send_text_dedup: Callable[[str, str | None], Awaitable[str | None]],
    mode: str,
    max_progress: int,
    thinking_max_chars: int,
    fallback_error_message: str,
    fallback_empty_message: str,
) -> bool:
    chunks: list[str] = []
    last_progress_text: str | None = None
    last_sent_text: str | None = None
    progress_count = 0
    has_started_reply = False
    pending_thinking_text: str | None = None

    async for raw_event in raw_events:
        parsed = parse_sse_message(raw_event)
        if not parsed:
            continue
        event_type, payload = parsed

        if event_type == "error":
            message = str(payload.get("message") or "").strip() or fallback_error_message
            await send_text(message)
            return False

        if event_type == "progress" and str(payload.get("phase") or "") == "step_start":
            if mode in {"steps", "both"} and progress_count < max_progress:
                text = format_step_progress_message(payload)
                if text and text != last_progress_text:
                    last_sent_text = await send_text_dedup(text, last_sent_text)
                    last_progress_text = text
                    progress_count += 1
            continue

        if event_type == "progress" and str(payload.get("phase") or "") == "thinking":
            if has_started_reply:
                continue
            if mode in {"thoughts", "both"} and progress_count < max_progress:
                text = format_thinking_progress_message(payload, max_chars=thinking_max_chars)
                if text and text != last_progress_text:
                    # Emit the previous thinking now; keep the latest one as pending.
                    # This lets us semantically filter only the final thinking message
                    # against the final assistant summary.
                    if pending_thinking_text:
                        last_sent_text = await send_text_dedup(
                            pending_thinking_text,
                            last_sent_text,
                        )
                    pending_thinking_text = text
                    last_progress_text = text
                    progress_count += 1
            continue

        if event_type == "chunk":
            chunk = str(payload.get("content") or "")
            if chunk:
                has_started_reply = True
                chunks.append(chunk)
            continue

        if event_type == "done":
            break

    assistant_text = "".join(chunks).strip() or fallback_empty_message
    if pending_thinking_text and not is_semantically_similar(
        pending_thinking_text,
        assistant_text,
    ):
        last_sent_text = await send_text_dedup(pending_thinking_text, last_sent_text)
    await send_text_dedup(assistant_text, last_sent_text)
    return True

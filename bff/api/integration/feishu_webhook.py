from __future__ import annotations

from typing import Any, Callable


def encrypted_payload_error(body: dict[str, Any]) -> dict[str, Any] | None:
    if "encrypt" in body and "type" not in body:
        return {"code": 1, "msg": "encrypted payload is not supported"}
    return None


def url_verification_response(
    body: dict[str, Any],
    *,
    verify_token: Callable[[str | None], bool],
) -> dict[str, Any] | None:
    if body.get("type") != "url_verification":
        return None
    if not verify_token(body.get("token")):
        return {"code": 1, "msg": "invalid token"}
    return {"challenge": body.get("challenge", "")}


def extract_message_receive_event(
    body: dict[str, Any],
    *,
    verify_token: Callable[[str | None], bool],
) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
    header = body.get("header") or {}
    event_type = str(header.get("event_type") or "")
    if event_type != "im.message.receive_v1":
        return None, "", None

    if not verify_token(header.get("token")):
        return None, "", {"code": 1, "msg": "invalid token"}

    event = body.get("event") or {}
    message = event.get("message") or {}
    message_id = str(message.get("message_id") or "").strip()
    if not message_id:
        return None, "", None
    return event, message_id, None

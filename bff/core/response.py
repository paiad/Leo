from __future__ import annotations

from typing import Any


def ok(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None}


def err(message: str) -> dict[str, Any]:
    return {"success": False, "data": None, "error": message}

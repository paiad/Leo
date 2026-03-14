from __future__ import annotations

import os
from pathlib import Path

from app.config import config

TRUTHY_VALUES = {"1", "true", "yes", "on"}


def is_truthy_env(value: str | None) -> bool:
    return (value or "").strip().lower() in TRUTHY_VALUES


def is_memory_sync_enabled() -> bool:
    return is_truthy_env(os.getenv("BFF_MEMORY_SYNC_ENABLED", "1"))


def chat_session_store_path(override_path: str | None = None) -> Path:
    raw = (
        override_path
        or os.getenv("BFF_CHAT_MEMORY_STORE_PATH")
        or str(Path(config.root_path) / "config" / "chat-memory-store.json")
    )
    return Path(raw.strip()).expanduser()

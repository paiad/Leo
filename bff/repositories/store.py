from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock

from bff.domain.models import SessionRecord, McpServerRecord
from bff.utils.memory_settings import chat_session_store_path


@dataclass
class InMemoryStore:
    """
    Runtime store with optional session persistence.

    Naming is kept for backward compatibility, but when enable_persistence=True
    this store is effectively memory + JSON snapshot persistence.
    """
    sessions: dict[str, SessionRecord] = field(default_factory=dict)
    mcp_servers: dict[str, McpServerRecord] = field(default_factory=dict)
    enable_persistence: bool = True
    persistence_path: str | None = None
    _persist_lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.enable_persistence:
            return
        self._load_sessions()

    def persist_sessions(self) -> None:
        if not self.enable_persistence:
            return
        state_path = self._state_file_path()
        if state_path is None:
            return
        state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "sessions": {
                sid: session.model_dump()
                for sid, session in sorted(self.sessions.items(), key=lambda item: item[0])
            },
        }
        temp_path = state_path.with_suffix(state_path.suffix + ".tmp")
        with self._persist_lock:
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(state_path)

    def _state_file_path(self) -> Path | None:
        path = chat_session_store_path(self.persistence_path)
        return path if str(path).strip() else None

    def _load_sessions(self) -> None:
        state_path = self._state_file_path()
        if state_path is None or not state_path.exists():
            return

        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            return

        raw_sessions = payload.get("sessions")
        if not isinstance(raw_sessions, dict):
            return

        loaded: dict[str, SessionRecord] = {}
        for sid, item in raw_sessions.items():
            if not isinstance(sid, str) or not isinstance(item, dict):
                continue
            try:
                session = SessionRecord.model_validate(item)
            except Exception:
                continue
            loaded[sid] = session
        if loaded:
            self.sessions.update(loaded)


store = InMemoryStore()

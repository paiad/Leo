from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class TurnMemoryPayload:
    source: str
    session_id: str
    question: str
    answer: str
    model: str | None = None

    def to_json_text(self) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": self.source,
            "session_id": self.session_id,
            "model": self.model or "",
            "user": self.question,
            "assistant": self.answer,
        }
        return json.dumps(payload, ensure_ascii=False)

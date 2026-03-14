from __future__ import annotations

import inspect
from typing import Any, Callable

from app.logger import logger


class RuntimeProgressEmitter:
    async def emit(
        self,
        progress_callback: Callable[[dict[str, Any]], Any] | None,
        event: dict[str, Any],
    ) -> None:
        if progress_callback is None:
            return
        try:
            maybe_awaitable = progress_callback(event)
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable
        except Exception as exc:
            logger.debug(f"Failed to emit progress event {event.get('phase')}: {exc}")

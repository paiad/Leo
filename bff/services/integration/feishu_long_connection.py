from __future__ import annotations

import asyncio
from concurrent.futures import CancelledError as FutureCancelledError
import json
import threading
from typing import Any

from app.logger import logger
from bff.api.integration.feishu import handle_message_receive_event, is_duplicate_message
from bff.utils.env import get_env


def _env_bool(name: str, default: bool) -> bool:
    raw = get_env(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


class FeishuLongConnectionService:
    def __init__(self) -> None:
        self._started = False
        self._stopping = False
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    @staticmethod
    def _normalize_payload(data: Any, lark_module: Any) -> dict[str, Any]:
        if isinstance(data, dict):
            return data
        try:
            raw = lark_module.JSON.marshal(data)
            if isinstance(raw, str):
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
        except Exception:
            logger.exception("Failed to parse Feishu long-connection event payload")
        return {}

    def _schedule_message_event(self, event: dict[str, Any]) -> None:
        if self._loop is None or self._stopping or self._loop.is_closed():
            return

        message = event.get("message") or {}
        message_id = str(message.get("message_id") or "").strip()

        async def worker() -> None:
            if message_id and await is_duplicate_message(message_id):
                return
            await handle_message_receive_event(event)

        try:
            future = asyncio.run_coroutine_threadsafe(worker(), self._loop)
        except RuntimeError:
            logger.warning("Skip Feishu message event scheduling: target loop is not available.")
            return

        def on_done(done_future) -> None:
            try:
                done_future.result()
            except (FutureCancelledError, asyncio.CancelledError):
                logger.info("Feishu long-connection async worker cancelled.")
            except Exception:
                logger.exception("Feishu long-connection async worker failed")

        future.add_done_callback(on_done)

    def _on_receive_message(self, data: Any, lark_module: Any) -> None:
        payload = self._normalize_payload(data, lark_module)
        if not payload:
            return

        # SDK usually provides {"schema","header","event"}.
        event = payload.get("event") if isinstance(payload.get("event"), dict) else payload
        if not isinstance(event, dict):
            return
        self._schedule_message_event(event)

    def _run_client(self, app_id: str, app_secret: str, level_name: str) -> None:
        try:
            import lark_oapi as lark  # type: ignore
            import lark_oapi.ws.client as lark_ws_client  # type: ignore

            # lark-oapi ws.Client uses asyncio.get_event_loop() internally,
            # so we must create/bind an event loop in this thread.
            thread_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(thread_loop)
            # lark_oapi.ws.client captures a module-global `loop` at import time.
            # Force it to use this thread's loop to avoid "event loop is already running".
            lark_ws_client.loop = thread_loop

            log_level = getattr(lark.LogLevel, level_name, lark.LogLevel.INFO)

            event_handler = (
                lark.EventDispatcherHandler.builder("", "")
                .register_p2_im_message_receive_v1(
                    lambda data: self._on_receive_message(data, lark)
                )
                .build()
            )

            client = lark.ws.Client(
                app_id,
                app_secret,
                log_level=log_level,
                event_handler=event_handler,
            )
            client.start()
        except Exception:
            logger.exception("Feishu long-connection client exited unexpectedly")

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if not _env_bool("FEISHU_USE_LONG_CONNECTION", False):
            logger.info("Feishu long connection is disabled. Set FEISHU_USE_LONG_CONNECTION=true to enable.")
            return

        with self._lock:
            if self._started:
                return

            app_id = str(get_env("FEISHU_APP_ID", "") or "").strip()
            app_secret = str(get_env("FEISHU_APP_SECRET", "") or "").strip()
            if not app_id or not app_secret:
                logger.warning(
                    "Feishu long connection skipped: missing FEISHU_APP_ID or FEISHU_APP_SECRET."
                )
                return

            level_name = str(get_env("FEISHU_LONG_CONNECTION_LOG_LEVEL", "INFO") or "INFO").strip().upper()
            self._stopping = False
            self._loop = loop
            self._thread = threading.Thread(
                target=self._run_client,
                args=(app_id, app_secret, level_name),
                name="feishu-long-connection",
                daemon=True,
            )
            self._thread.start()
            self._started = True
            logger.info("Feishu long connection started.")

    def stop(self) -> None:
        # lark_oapi ws client does not expose a documented stop() in current SDK.
        if self._started:
            self._stopping = True
            self._loop = None
            self._started = False
            logger.info("Feishu long connection shutdown requested (daemon thread will exit with process).")


feishu_long_connection_service = FeishuLongConnectionService()

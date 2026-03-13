from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncGenerator
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import tiktoken
from app.config import config
from app.prompt.manus import SYSTEM_PROMPT
from bff.domain.models import (
    ChatRequest,
    MessageRecord,
    SessionRecord,
    now_iso,
    new_id,
)
from bff.repositories.store import InMemoryStore
from bff.services.agent_runtime import AgentRuntime
from bff.services.model_service import ModelService


def sse_event(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def split_chunks(text: str, chunk_size: int = 120) -> list[str]:
    if not text:
        return [""]
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


class ChatService:
    def __init__(self, store: InMemoryStore, runtime: AgentRuntime, model_service: ModelService):
        self._store = store
        self._runtime = runtime
        self._model_service = model_service
        self._lock = asyncio.Lock()
        self._record_lock = asyncio.Lock()
        self._tokenizer = self._init_tokenizer()
        self._record_tz = ZoneInfo("Asia/Shanghai")
        self._record_root = Path(config.root_path) / "logs" / "chat"

    @staticmethod
    def build_prompt(content: str, workspace_prompt: str | None) -> str:
        if workspace_prompt and workspace_prompt.strip():
            return f"{content}\n\n[Workspace Prompt]\n{workspace_prompt.strip()}"
        return content

    @staticmethod
    def _env_int(name: str, default: int, minimum: int = 0) -> int:
        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            value = int(raw.strip())
        except ValueError:
            return default
        return max(minimum, value)

    def _init_tokenizer(self):
        default_llm = config.llm.get("default")
        model_name = default_llm.model if default_llm else "gpt-4o-mini"
        try:
            return tiktoken.encoding_for_model(model_name)
        except Exception:
            try:
                return tiktoken.get_encoding("cl100k_base")
            except Exception:
                return None

    def _count_tokens(self, text: str) -> int:
        if not text:
            return 0
        if self._tokenizer is not None:
            try:
                return len(self._tokenizer.encode(text))
            except Exception:
                pass
        ascii_chars = sum(1 for ch in text if ord(ch) < 128)
        non_ascii_chars = len(text) - ascii_chars
        return max(1, (ascii_chars + 3) // 4 + non_ascii_chars)

    def _context_window_limit(self) -> int:
        env_limit = self._env_int("BFF_CHAT_CONTEXT_WINDOW_TOKENS", -1, minimum=-1)
        if env_limit > 0:
            return env_limit
        default_llm = config.llm.get("default")
        if default_llm and default_llm.max_input_tokens and default_llm.max_input_tokens > 0:
            return default_llm.max_input_tokens
        return 8192

    def _history_token_budget(self, current_user_text: str) -> int:
        context_limit = self._context_window_limit()
        default_llm = config.llm.get("default")
        default_reserved = (
            min(default_llm.max_tokens, 1200) if default_llm else 1200
        )
        reserved_output = self._env_int(
            "BFF_CHAT_RESERVED_OUTPUT_TOKENS",
            default_reserved,
        )
        safety_buffer = self._env_int("BFF_CHAT_CONTEXT_SAFETY_BUFFER_TOKENS", 300)
        max_history_cap = self._env_int("BFF_CHAT_HISTORY_MAX_TOKENS", 2500)
        dynamic_budget = context_limit - reserved_output - safety_buffer - self._count_tokens(
            current_user_text
        )
        return max(0, min(max_history_cap, dynamic_budget))

    def _fit_message_line_to_budget(
        self,
        role: str,
        content: str,
        token_budget: int,
    ) -> str:
        prefix = f"[{role}] "
        if token_budget <= self._count_tokens(prefix):
            return ""
        if self._count_tokens(prefix + content) <= token_budget:
            return prefix + content

        low = 1
        high = len(content)
        best = ""
        while low <= high:
            mid = (low + high) // 2
            candidate = prefix + content[:mid].rstrip() + "…"
            if self._count_tokens(candidate) <= token_budget:
                best = candidate
                low = mid + 1
            else:
                high = mid - 1
        return best

    def _format_history_context(self, session: SessionRecord, current_user_text: str) -> str:
        messages = session.messages
        if not messages:
            return ""

        # Current request is appended before runtime invocation; exclude it from history.
        history_messages = messages[:-1] if messages[-1].role == "user" else messages
        relevant_messages = [
            message
            for message in history_messages
            if message.role in {"user", "assistant"} and (message.content or "").strip()
        ]
        if not relevant_messages:
            return ""

        history_budget = self._history_token_budget(current_user_text)
        if history_budget <= 0:
            return current_user_text

        message_char_limit = self._env_int("BFF_CHAT_HISTORY_MESSAGE_CHAR_LIMIT", 1200)
        selected_lines_reversed: list[str] = []
        used_tokens = 0
        omitted_count = 0

        for message in reversed(relevant_messages):
            content = (message.content or "").strip()
            if len(content) > message_char_limit:
                content = content[:message_char_limit].rstrip() + "…"

            remaining = history_budget - used_tokens
            if remaining <= 0:
                omitted_count += 1
                continue

            line = self._fit_message_line_to_budget(message.role, content, remaining)
            if not line:
                omitted_count += 1
                break

            line_tokens = self._count_tokens(line)
            if line_tokens > remaining:
                omitted_count += 1
                break

            selected_lines_reversed.append(line)
            used_tokens += line_tokens

        if not selected_lines_reversed:
            return current_user_text

        selected_lines = list(reversed(selected_lines_reversed))
        header = (
            "[Recent Session Context]\n"
            f"Included history tokens: {used_tokens}/{history_budget}."
        )
        if omitted_count > 0:
            header += f" Omitted earlier messages: {omitted_count}."

        return (
            header
            + "\n"
            + "\n".join(selected_lines)
            + f"\n\n[Current User Request]\n{current_user_text}"
        )

    def _build_runtime_prompt(
        self,
        session: SessionRecord,
        user_text: str,
        workspace_prompt: str | None,
    ) -> str:
        prompt = self.build_prompt(user_text, workspace_prompt)
        history_context = self._format_history_context(session, prompt)
        if not history_context:
            return prompt
        return history_context

    @staticmethod
    def platform_prompt() -> str:
        return SYSTEM_PROMPT.format(directory=config.workspace_root)

    def model_config(self) -> dict[str, Any]:
        return self._model_service.chat_model_config()

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions = sorted(self._store.sessions.values(), key=lambda x: x.updatedAt, reverse=True)
        return [session.model_dump(exclude={"messages"}) for session in sessions]

    def create_session(self, title: str | None = None) -> dict[str, Any]:
        now = now_iso()
        session = SessionRecord(
            id=new_id(),
            title=(title or "New Chat").strip() or "New Chat",
            createdAt=now,
            updatedAt=now,
            messages=[],
        )
        self._store.sessions[session.id] = session
        return session.model_dump(exclude={"messages"})

    def get_session_messages(self, session_id: str) -> list[dict[str, Any]] | None:
        session = self._store.sessions.get(session_id)
        if not session:
            return None
        return [message.model_dump() for message in session.messages]

    def delete_session_message(self, session_id: str, message_id: str) -> bool | None:
        session = self._store.sessions.get(session_id)
        if not session:
            return None
        before = len(session.messages)
        session.messages = [msg for msg in session.messages if msg.id != message_id]
        session.updatedAt = now_iso()
        return before != len(session.messages)

    def clear_session_messages(self, session_id: str) -> int | None:
        session = self._store.sessions.get(session_id)
        if not session:
            return None
        deleted = len(session.messages)
        session.messages = []
        session.updatedAt = now_iso()
        return deleted

    async def send_message(self, payload: ChatRequest) -> dict[str, Any]:
        user_text = payload.content.strip()
        if not user_text:
            raise ValueError("content 不能为空")

        session = await self._append_user_message(payload.sessionId, user_text)
        runtime_prompt = self._build_runtime_prompt(
            session,
            user_text,
            payload.workspacePrompt,
        )
        response_text = await self._runtime.ask(
            runtime_prompt
        )
        assistant = await self._append_assistant_message(session.id, response_text, payload.model)
        await self._append_chat_record(
            source=payload.source,
            session_id=session.id,
            question=user_text,
            answer=response_text,
            model=payload.model,
        )

        return {
            "success": True,
            "data": assistant,
            "toolEvents": [],
            "decisionEvents": [],
            "error": None,
        }

    async def stream_message(self, payload: ChatRequest) -> AsyncGenerator[str, None]:
        user_text = payload.content.strip()
        if not user_text:
            yield sse_event(
                "error",
                {
                    "message": "content 不能为空",
                    "errorType": "validation_error",
                    "context": {},
                },
            )
            return

        session = await self._append_user_message(payload.sessionId, user_text)
        progress_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        has_persisted_response = False

        async def on_runtime_event(event: dict[str, Any]) -> None:
            if not isinstance(event, dict):
                return
            await progress_queue.put(event)

        async def persist_response(response_text: str) -> dict[str, Any]:
            nonlocal has_persisted_response
            if has_persisted_response:
                messages = self.get_session_messages(session.id) or []
                if messages:
                    return messages[-1]
                return {
                    "id": "",
                    "role": "assistant",
                    "content": response_text,
                    "createdAt": now_iso(),
                    "model": payload.model or self._model_service.get_runtime_model_name(),
                    "toolEvents": [],
                    "decisionEvents": [],
                }
            assistant = await self._append_assistant_message(
                session.id,
                response_text,
                payload.model,
            )
            await self._append_chat_record(
                source=payload.source,
                session_id=session.id,
                question=user_text,
                answer=response_text,
                model=payload.model,
            )
            has_persisted_response = True
            return assistant

        response_task: asyncio.Task[str] | None = None
        try:
            yield sse_event(
                "progress",
                {
                    "phase": "accepted",
                    "message": "请求已接收，正在启动代理",
                },
            )
            response_task = asyncio.create_task(
                self._runtime.ask(
                    self._build_runtime_prompt(
                        session,
                        user_text,
                        payload.workspacePrompt,
                    ),
                    progress_callback=on_runtime_event,
                )
            )

            while not response_task.done() or not progress_queue.empty():
                try:
                    event = await asyncio.wait_for(progress_queue.get(), timeout=0.2)
                except asyncio.TimeoutError:
                    await asyncio.sleep(0)
                    continue

                event_type = str(event.get("type") or "").strip()
                if event_type in {"progress", "tool_start", "tool_done"}:
                    yield sse_event(event_type, event)
                await asyncio.sleep(0)

            response_text = await response_task
            assistant = await persist_response(response_text)
            for chunk in split_chunks(response_text):
                yield sse_event("chunk", {"content": chunk})
                await asyncio.sleep(0)
            yield sse_event(
                "done",
                {
                    "done": True,
                    "messageId": assistant["id"],
                    "toolEvents": [],
                    "decisionEvents": [],
                },
            )
        except asyncio.CancelledError:
            # Client disconnected (for example, route switch). Keep computing and persist final reply.
            if response_task:
                try:
                    response_text = await asyncio.shield(response_task)
                except Exception:
                    raise
                await persist_response(response_text)
            raise
        except Exception as exc:
            if response_task and not response_task.done():
                response_task.cancel()
            yield sse_event(
                "error",
                {
                    "message": str(exc),
                    "errorType": "runtime_error",
                    "context": {},
                },
            )

    @staticmethod
    def _is_truthy_env(value: str | None) -> bool:
        return (value or "").strip().lower() in {"1", "true", "yes", "on"}

    async def _append_chat_record(
        self,
        *,
        source: str,
        session_id: str,
        question: str,
        answer: str,
        model: str | None,
    ) -> None:
        # Logging-only record, not used for session restore.
        if not self._is_truthy_env(os.getenv("BFF_CHAT_RECORD_ENABLED", "1")):
            return

        now_local = datetime.now(self._record_tz)
        date_str = now_local.strftime("%Y-%m-%d")
        record_path = self._record_root / f"{date_str}.json"
        source_key = "lark" if source == "lark" else "browser"

        entry = {
            "id": new_id(),
            "ts": now_local.isoformat(),
            "session_id": session_id,
            "question": question,
            "answer": answer,
            "model": model or self._model_service.get_runtime_model_name(),
        }

        async with self._record_lock:
            self._record_root.mkdir(parents=True, exist_ok=True)
            payload: dict[str, Any]
            if record_path.exists():
                try:
                    payload = json.loads(record_path.read_text(encoding="utf-8"))
                except Exception:
                    payload = {}
            else:
                payload = {}

            sources = payload.get("sources")
            if not isinstance(sources, dict):
                sources = {"lark": [], "browser": []}
            if not isinstance(sources.get("lark"), list):
                sources["lark"] = []
            if not isinstance(sources.get("browser"), list):
                sources["browser"] = []

            sources[source_key].append(entry)
            payload["date"] = date_str
            payload["timezone"] = "Asia/Shanghai"
            payload["version"] = 1
            payload["sources"] = sources

            record_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    async def _append_user_message(self, session_id: str | None, content: str) -> SessionRecord:
        async with self._lock:
            session = self._ensure_session(session_id)
            session.messages.append(
                MessageRecord(
                    id=new_id(),
                    role="user",
                    content=content,
                    createdAt=now_iso(),
                )
            )
            session.updatedAt = now_iso()
            return session

    async def _append_assistant_message(
        self,
        session_id: str,
        content: str,
        model: str | None,
    ) -> dict[str, Any]:
        async with self._lock:
            session = self._ensure_session(session_id)
            message = MessageRecord(
                id=new_id(),
                role="assistant",
                content=content,
                createdAt=now_iso(),
                model=model or self._model_service.get_runtime_model_name(),
                toolEvents=[],
                decisionEvents=[],
            )
            session.messages.append(message)
            session.updatedAt = now_iso()
            return message.model_dump()

    def _ensure_session(self, session_id: str | None) -> SessionRecord:
        if session_id and session_id in self._store.sessions:
            return self._store.sessions[session_id]

        now = now_iso()
        session = SessionRecord(
            id=new_id(),
            title="New Chat",
            createdAt=now,
            updatedAt=now,
            messages=[],
        )
        self._store.sessions[session.id] = session
        return session

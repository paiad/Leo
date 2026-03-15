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
from opencc import OpenCC
from app.config import config
from app.logger import logger
from app.prompt.manus import SYSTEM_PROMPT
from bff.domain.models import (
    ChatRequest,
    MessageRecord,
    SessionRecord,
    now_iso,
    new_id,
)
from bff.repositories.store import InMemoryStore, PostgresStore
from bff.services.memory.memory_sync_service import MemorySyncService
from bff.services.models.model_service import ModelService
from bff.services.runtime.agent_runtime import AgentRuntime
from bff.services.chat.context_memory_service import ContextMemoryService


def sse_event(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def split_chunks(text: str, chunk_size: int = 120) -> list[str]:
    if not text:
        return [""]
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


class ChatService:
    def __init__(
        self,
        store: InMemoryStore | PostgresStore,
        runtime: AgentRuntime,
        model_service: ModelService,
        memory_sync: MemorySyncService | None = None,
    ):
        self._store = store
        self._runtime = runtime
        self._model_service = model_service
        self._memory_sync = memory_sync
        self._context_memory = ContextMemoryService(store=store)
        self._lock = asyncio.Lock()
        self._record_lock = asyncio.Lock()
        self._tokenizer = self._init_tokenizer()
        self._record_tz = ZoneInfo("Asia/Shanghai")
        self._record_root = Path(config.root_path) / "logs" / "chat"
        self._opencc_converter: OpenCC | None = None
        self._opencc_init_failed = False

    def _schedule_memory_sync(
        self,
        *,
        source: str,
        session_id: str,
        question: str,
        answer: str,
        model: str | None,
    ) -> None:
        if not self._memory_sync:
            return
        self._memory_sync.schedule_sync_turn(
            source=source,
            session_id=session_id,
            question=question,
            answer=answer,
            model=model or self._model_service.get_runtime_model_name(),
        )

    async def _persist_post_turn(
        self,
        *,
        source: str,
        session_id: str,
        user_text: str,
        response_text: str,
        model: str | None,
    ) -> None:
        await self._context_memory.persist_turn_memory(
            session_id=session_id,
            user_message=user_text,
            assistant_message=response_text,
        )
        await self._append_chat_record(
            source=source,
            session_id=session_id,
            question=user_text,
            answer=response_text,
            model=model,
        )
        self._schedule_memory_sync(
            source=source,
            session_id=session_id,
            question=user_text,
            answer=response_text,
            model=model,
        )

    def _schedule_post_turn_persist(
        self,
        *,
        source: str,
        session_id: str,
        user_text: str,
        response_text: str,
        model: str | None,
    ) -> None:
        task = asyncio.create_task(
            self._persist_post_turn(
                source=source,
                session_id=session_id,
                user_text=user_text,
                response_text=response_text,
                model=model,
            )
        )

        def _consume_exception(done_task: asyncio.Task[None]) -> None:
            try:
                done_task.result()
            except Exception:
                logger.exception(
                    "Async post-turn persistence failed: "
                    f"source={source}, session_id={session_id}"
                )

        task.add_done_callback(_consume_exception)

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

    @staticmethod
    def _current_user_request_block(current_user_text: str) -> str:
        return f"[Current User Request]\n{current_user_text}"

    def _format_history_context(self, session: SessionRecord, current_user_text: str) -> str:
        messages = session.messages
        if not messages:
            return self._current_user_request_block(current_user_text)

        # Current request is appended before runtime invocation; exclude it from history.
        history_messages = messages[:-1] if messages[-1].role == "user" else messages
        relevant_messages = [
            message
            for message in history_messages
            if message.role in {"user", "assistant"} and (message.content or "").strip()
        ]
        if not relevant_messages:
            return self._current_user_request_block(current_user_text)

        history_budget = self._history_token_budget(current_user_text)
        if history_budget <= 0:
            return self._current_user_request_block(current_user_text)

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
            return self._current_user_request_block(current_user_text)

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
            + "\n\n"
            + self._current_user_request_block(current_user_text)
        )

    def _build_runtime_prompt(
        self,
        session: SessionRecord,
        user_text: str,
        workspace_prompt: str | None,
        *,
        source: str,
        request_message_id: str | None = None,
    ) -> str:
        current_request_text = self.build_prompt(user_text, workspace_prompt)
        output_policy = (
            self._build_output_policy_text(source=source, session_id=session.id)
            if self._should_apply_output_policy(user_text)
            else ""
        )
        if output_policy:
            current_request_text = f"{current_request_text}\n\n{output_policy}"

        context_bundle = self._context_memory.build_context_bundle(
            session_id=session.id,
            current_user_text=current_request_text,
        )
        if context_bundle.text:
            self._context_memory.persist_injection_audit(
                session_id=session.id,
                request_message_id=request_message_id,
                query_text=user_text,
                bundle=context_bundle,
            )

        prompt_with_history = self._format_history_context(session, current_request_text)
        if context_bundle.text:
            return f"{context_bundle.text}\n\n{prompt_with_history}"
        return prompt_with_history

    def _source_key(self, source: str) -> str:
        return "lark" if source == "lark" else "browser"

    def _session_output_dir(self, *, source: str, session_id: str) -> Path:
        source_key = self._source_key(source)
        date_str = datetime.now(self._record_tz).strftime("%Y-%m-%d")
        return Path(config.workspace_root) / "generated" / source_key / session_id / date_str

    def _should_apply_output_policy(self, user_text: str) -> bool:
        # Explicit runtime overrides.
        if self._is_truthy_env(os.getenv("BFF_CHAT_DISABLE_FILE_OUTPUT_POLICY")):
            return False
        if self._is_truthy_env(os.getenv("BFF_CHAT_FORCE_FILE_OUTPUT_POLICY")):
            return True

        text = (user_text or "").strip().lower()
        if not text:
            return False

        # Only enforce file-output constraints for artifact-style requests.
        artifact_markers = (
            "save ",
            "write to file",
            "create file",
            "export",
            "download",
            "pdf",
            "ppt",
            "excel",
            "csv",
            "docx",
            "png",
            "jpg",
            "jpeg",
            "svg",
            "mp4",
            "zip",
            "保存",
            "存储",
            "文件",
            "导出",
            "下载",
            "生成图片",
            "生成文件",
            "输出到",
            "写入文件",
            "图片",
            "海报",
            "视频",
        )
        return any(marker in text for marker in artifact_markers)

    def _build_output_policy_text(self, *, source: str, session_id: str) -> str:
        output_dir = self._session_output_dir(source=source, session_id=session_id)
        relative_dir = output_dir.relative_to(config.workspace_root)
        return (
            "[File Output Policy]\n"
            f"- Save all newly generated files under: {output_dir}\n"
            f"- Relative path from workspace root: {relative_dir}\n"
            "- Do not write generated deliverables to logs/.\n"
            "- For image requests, include a directly viewable Markdown image in the final answer: ![alt](http/https-image-url).\n"
            "- Do not reply with only a local file path for image requests; file path is supplemental only.\n"
            "- For non-image generated files, include the relative file path(s) in the final answer."
        )

    @staticmethod
    def platform_prompt() -> str:
        return SYSTEM_PROMPT.format(directory=config.workspace_root)

    def model_config(self) -> dict[str, Any]:
        return self._model_service.chat_model_config()

    def _session_source(self, session: SessionRecord) -> str:
        source = (getattr(session, "source", None) or "").strip().lower()
        if source in {"browser", "lark"}:
            return source
        # Backward compatibility for historical records without source field.
        if (session.title or "").strip().lower().startswith("feishu-"):
            return "lark"
        return "browser"

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions = sorted(self._store.sessions.values(), key=lambda x: x.updatedAt, reverse=True)
        result: list[dict[str, Any]] = []
        for session in sessions:
            payload = session.model_dump(exclude={"messages"})
            payload["source"] = self._session_source(session)
            result.append(payload)
        return result

    def create_session(self, title: str | None = None, *, source: str = "browser") -> dict[str, Any]:
        now = now_iso()
        source_key = "lark" if source == "lark" else "browser"
        session = SessionRecord(
            id=new_id(),
            title=(title or "New Chat").strip() or "New Chat",
            createdAt=now,
            updatedAt=now,
            source=source_key,
            messages=[],
        )
        self._store.sessions[session.id] = session
        self._store.persist_sessions()
        payload = session.model_dump(exclude={"messages"})
        payload["source"] = self._session_source(session)
        return payload

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
        if before != len(session.messages):
            self._store.persist_sessions()
        return before != len(session.messages)

    async def clear_session_messages(self, session_id: str) -> int | None:
        async with self._lock:
            session = self._store.sessions.get(session_id)
            if not session:
                return None
            deleted = len(session.messages)
            source = self._session_source(session)
            session.messages = []
            session.updatedAt = now_iso()
            self._store.persist_sessions()

            deleter = getattr(self._store, "delete_mcp_routing_events_by_session", None)
            if callable(deleter):
                try:
                    deleter(session_id)
                except Exception as exc:
                    logger.warning(
                        "Failed to delete MCP routing events for session clear: "
                        f"session_id={session_id}, error={exc}"
                    )

        try:
            self._context_memory.purge_session_memory(session_id=session_id)
        except Exception as exc:
            logger.warning(
                "Failed to purge context memory for session clear: "
                f"session_id={session_id}, error={exc}"
            )

        if self._memory_sync:
            try:
                await self._memory_sync.forget_session(
                    source=source,
                    session_id=session_id,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to purge Memory MCP session state: "
                    f"session_id={session_id}, source={source}, error={exc}"
                )

        return deleted

    async def send_message(self, payload: ChatRequest) -> dict[str, Any]:
        user_text = payload.content.strip()
        if not user_text:
            raise ValueError("content 不能为空")

        session, user_message_id = await self._append_user_message(
            payload.sessionId,
            user_text,
            payload.source,
            payload.userInputType,
        )
        self._session_output_dir(source=payload.source, session_id=session.id).mkdir(
            parents=True, exist_ok=True
        )
        runtime_prompt = self._build_runtime_prompt(
            session,
            user_text,
            payload.workspacePrompt,
            source=payload.source,
            request_message_id=user_message_id,
        )
        response_text = await self._runtime.ask(
            runtime_prompt,
            session_id=session.id,
        )
        response_text = self._to_simplified_chinese(response_text)
        assistant = await self._append_assistant_message(session.id, response_text, payload.model)
        self._schedule_post_turn_persist(
            source=payload.source,
            session_id=session.id,
            user_text=user_text,
            response_text=response_text,
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

        session, user_message_id = await self._append_user_message(
            payload.sessionId,
            user_text,
            payload.source,
            payload.userInputType,
        )
        self._session_output_dir(source=payload.source, session_id=session.id).mkdir(
            parents=True, exist_ok=True
        )
        progress_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        has_persisted_response = False

        async def on_runtime_event(event: dict[str, Any]) -> None:
            if not isinstance(event, dict):
                return
            await progress_queue.put(event)

        async def persist_response(response_text: str) -> dict[str, Any]:
            response_text = self._to_simplified_chinese(response_text)
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
            self._schedule_post_turn_persist(
                source=payload.source,
                session_id=session.id,
                user_text=user_text,
                response_text=response_text,
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
                        source=payload.source,
                        request_message_id=user_message_id,
                    ),
                    session_id=session.id,
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
            normalized_response_text = str(assistant.get("content") or "")
            for chunk in split_chunks(normalized_response_text):
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

    def _to_simplified_chinese(self, text: str) -> str:
        if not text:
            return text
        if not self._is_truthy_env(os.getenv("BFF_FORCE_SIMPLIFIED_CHINESE", "1")):
            return text
        if self._opencc_init_failed:
            return text
        if self._opencc_converter is None:
            try:
                self._opencc_converter = OpenCC("t2s")
            except Exception as exc:
                self._opencc_init_failed = True
                logger.warning(
                    "Simplified-Chinese normalization disabled: OpenCC init failed: "
                    f"{exc}"
                )
                return text
        try:
            return str(self._opencc_converter.convert(text))
        except Exception as exc:
            logger.warning(
                "Simplified-Chinese normalization failed during convert: "
                f"{exc}"
            )
            return text

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
        source_key = "lark" if source == "lark" else "browser"
        source_root = self._record_root / source_key
        record_path = source_root / f"{date_str}.json"

        entry = {
            "id": new_id(),
            "ts": now_local.isoformat(),
            "session_id": session_id,
            "question": question,
            "answer": answer,
            "model": model or self._model_service.get_runtime_model_name(),
        }

        async with self._record_lock:
            source_root.mkdir(parents=True, exist_ok=True)
            payload: dict[str, Any]
            if record_path.exists():
                try:
                    payload = json.loads(record_path.read_text(encoding="utf-8"))
                except Exception:
                    payload = {}
            else:
                payload = {}

            entries = payload.get("entries")
            if not isinstance(entries, list):
                entries = []
            entries.append(entry)
            payload["date"] = date_str
            payload["timezone"] = "Asia/Shanghai"
            payload["version"] = 1
            payload["source"] = source_key
            payload["entries"] = entries

            record_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    async def _append_user_message(
        self,
        session_id: str | None,
        content: str,
        source: str = "browser",
        user_input_type: str = "text",
    ) -> tuple[SessionRecord, str]:
        async with self._lock:
            session = self._ensure_session(session_id, source=source)
            message_id = new_id()
            normalized_input_type = "audio_asr" if user_input_type == "audio_asr" else "text"
            session.messages.append(
                MessageRecord(
                    id=message_id,
                    role="user",
                    content=content,
                    createdAt=now_iso(),
                    userInputType=normalized_input_type,
                )
            )
            session.updatedAt = now_iso()
            self._store.persist_sessions()
            return session, message_id

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
            self._store.persist_sessions()
            return message.model_dump()

    def _ensure_session(self, session_id: str | None, *, source: str = "browser") -> SessionRecord:
        if session_id and session_id in self._store.sessions:
            return self._store.sessions[session_id]

        now = now_iso()
        source_key = "lark" if source == "lark" else "browser"
        session = SessionRecord(
            id=new_id(),
            title="New Chat",
            createdAt=now,
            updatedAt=now,
            source=source_key,
            messages=[],
        )
        self._store.sessions[session.id] = session
        self._store.persist_sessions()
        return session

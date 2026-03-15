from __future__ import annotations

import re
from typing import Any, Callable

from app.agent.manus import Manus
from app.logger import logger


class RuntimeFinalizer:
    def __init__(self, extract_current_user_request: Callable[[str], str]):
        self._extract_current_user_request = extract_current_user_request

    @staticmethod
    def _is_terminate_only_tool_calls(tool_calls: Any) -> bool:
        if not tool_calls:
            return False
        names: list[str] = []
        for call in tool_calls:
            function = getattr(call, "function", None)
            name = getattr(function, "name", None)
            if name is None and isinstance(call, dict):
                function_dict = call.get("function") or {}
                name = function_dict.get("name")
            names.append(str(name or "").strip().lower())
        return bool(names) and all(name == "terminate" for name in names)

    @staticmethod
    def select_final_assistant_text(messages: list[Any]) -> str | None:
        """
        Select user-facing final text from agent memory.

        Prefer assistant messages that do not contain tool calls. This avoids
        leaking intermediate "thought" content attached to tool-call messages.
        """
        final_texts: list[str] = []

        for message in messages:
            role = getattr(message, "role", None)
            role_value = getattr(role, "value", role)
            content = getattr(message, "content", None)
            if role_value != "assistant" or not isinstance(content, str):
                continue
            text = content.strip()
            if not text:
                continue
            tool_calls = getattr(message, "tool_calls", None)
            if not tool_calls or RuntimeFinalizer._is_terminate_only_tool_calls(tool_calls):
                final_texts.append(text)

        if final_texts:
            return final_texts[-1]
        return None

    @staticmethod
    def _truncate_text(value: str, *, limit: int) -> str:
        text = (value or "").strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "..."

    @staticmethod
    def _strip_ansi(value: str) -> str:
        if not value:
            return ""
        return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", value)

    @staticmethod
    def _looks_incomplete(text: str) -> bool:
        content = (text or "").strip()
        if not content:
            return True
        if content.count("```") % 2 == 1:
            return True

        last_line = content.splitlines()[-1].strip()
        if re.fullmatch(r"[-*+]\s*", last_line):
            return True
        if re.fullmatch(r"\d+\.\s*", last_line):
            return True

        trailing_tokens = (
            ":",
            "：",
            ",",
            "，",
            "、",
            ";",
            "；",
            "(",
            "（",
            "[",
            "{",
            "-",
            "—",
            "·",
            "|",
            "`",
        )
        if content.endswith("```"):
            return True
        if content.endswith(trailing_tokens):
            return True
        return False

    def _build_repair_prompt(
        self,
        *,
        user_request: str,
        candidate_text: str,
        run_result: str,
        messages: list[Any],
    ) -> str:
        evidence_lines: list[str] = []
        for message in messages[-12:]:
            role = getattr(message, "role", None)
            role_value = getattr(role, "value", role)
            content = getattr(message, "content", None)
            if role_value not in {"assistant", "tool"} or not isinstance(content, str):
                continue
            text = self._strip_ansi(content).strip()
            if not text:
                continue

            if role_value == "assistant":
                tool_calls = getattr(message, "tool_calls", None)
                if tool_calls and not self._is_terminate_only_tool_calls(tool_calls):
                    continue
                evidence_lines.append(
                    f"[assistant] {self._truncate_text(text, limit=500)}"
                )
                continue

            tool_name = getattr(message, "name", "") or "tool"
            evidence_lines.append(
                f"[tool:{tool_name}] {self._truncate_text(text, limit=600)}"
            )

        evidence = "\n".join(evidence_lines).strip() or "(none)"
        run_result_block = self._truncate_text(self._strip_ansi(run_result), limit=900) or "(empty)"
        return (
            "You are repairing a possibly truncated final answer.\n"
            "Task:\n"
            "1) Keep only facts already present in candidate/evidence.\n"
            "2) Complete unfinished sentences or markdown if needed.\n"
            "3) Do not add new claims.\n"
            "4) Output only the repaired Chinese final answer.\n\n"
            f"User request:\n{self._truncate_text(user_request, limit=800)}\n\n"
            f"Candidate text:\n{self._truncate_text(candidate_text, limit=1200)}\n\n"
            f"Run result:\n{run_result_block}\n\n"
            f"Turn evidence:\n{evidence}\n"
        )

    async def _repair_incomplete_candidate(
        self,
        *,
        agent: Manus,
        user_request: str,
        candidate_text: str,
        run_result: str,
        messages: list[Any],
    ) -> str | None:
        repair_prompt = self._build_repair_prompt(
            user_request=user_request,
            candidate_text=candidate_text,
            run_result=run_result,
            messages=messages,
        )
        repaired = await agent.llm.ask(
            messages=[{"role": "user", "content": repair_prompt}],
            stream=False,
            temperature=0.0,
        )
        cleaned = (repaired or "").strip()
        if not cleaned:
            return None
        return cleaned

    def _build_deterministic_fallback(
        self,
        *,
        state_value: str,
        candidate_final_text: str | None,
        run_result: str,
        messages: list[Any],
    ) -> str:
        tool_lines: list[str] = []
        for message in reversed(messages):
            role = getattr(message, "role", None)
            role_value = getattr(role, "value", role)
            if role_value != "tool":
                continue
            content = getattr(message, "content", None)
            if not isinstance(content, str):
                continue
            text = self._strip_ansi(content).strip()
            if not text:
                continue
            name = str(getattr(message, "name", "") or "tool")
            line = f"- {name}: {self._truncate_text(text, limit=180)}"
            tool_lines.append(line)
            if len(tool_lines) >= 2:
                break

        candidate_line = ""
        if candidate_final_text and candidate_final_text.strip():
            candidate_line = (
                "- 候选回复（可能截断）: "
                f"{self._truncate_text(candidate_final_text, limit=220)}"
            )

        run_line = (
            "- 运行结果摘要: "
            f"{self._truncate_text(self._strip_ansi(run_result or ''), limit=220) or '(empty)'}"
        )
        details = [item for item in [candidate_line, run_line, *tool_lines] if item]
        detail_block = "\n".join(details) if details else "- 无可用执行细节。"
        return (
            "任务已结束，但最终文本不完整，已返回确定性结果。\n"
            f"- 运行状态: {state_value}\n"
            f"{detail_block}\n"
            "- 下一步: 你可以让我“基于本轮工具输出重新生成最终答复”。"
        )

    def _build_finalize_prompt(
        self,
        *,
        user_request: str,
        run_result: str,
        candidate_final_text: str | None,
        messages: list[Any],
    ) -> str:
        transcript_lines: list[str] = []
        for message in messages[-18:]:
            role = getattr(message, "role", None)
            role_value = getattr(role, "value", role)
            if role_value not in {"user", "assistant", "tool"}:
                continue

            content = getattr(message, "content", None)
            if not isinstance(content, str) or not content.strip():
                continue

            if role_value == "assistant":
                tool_calls = getattr(message, "tool_calls", None)
                if tool_calls:
                    # Skip "thinking + tool call" assistant turns in final transcript.
                    continue
                transcript_lines.append(
                    f"[assistant] {self._truncate_text(content, limit=700)}"
                )
                continue

            if role_value == "tool":
                tool_name = getattr(message, "name", "") or "tool"
                transcript_lines.append(
                    f"[tool:{tool_name}] {self._truncate_text(content, limit=900)}"
                )
                continue

            transcript_lines.append(
                f"[user] {self._truncate_text(content, limit=500)}"
            )

        transcript = "\n".join(transcript_lines).strip() or "(no transcript)"
        candidate_block = (
            self._truncate_text(candidate_final_text, limit=1200)
            if candidate_final_text
            else "(none)"
        )
        run_result_block = self._truncate_text(run_result or "", limit=1200) or "(empty)"

        return (
            "You are in FINALIZE phase of a runtime state machine.\n"
            "State machine: plan -> act -> verify -> finalize.\n"
            "Only output the final user-visible answer.\n\n"
            "Rules:\n"
            "1) Never output internal thoughts, planning, or \"I will try\" language.\n"
            "2) Use past tense and concrete outcome.\n"
            "3) If task is partially done, explicitly list: completed / not completed / next step.\n"
            "4) Keep concise and factual.\n\n"
            f"User request:\n{self._truncate_text(user_request, limit=1000)}\n\n"
            f"Candidate final answer (if any):\n{candidate_block}\n\n"
            f"Agent run result:\n{run_result_block}\n\n"
            f"Execution transcript:\n{transcript}\n\n"
            "Now produce the final answer in Chinese."
        )

    async def finalize_response(
        self,
        *,
        agent: Manus,
        user_prompt: str,
        run_result: str,
        candidate_final_text: str | None,
        messages: list[Any] | None = None,
    ) -> str:
        user_request = self._extract_current_user_request(user_prompt).strip() or user_prompt
        finalize_messages = list(messages) if messages is not None else list(agent.messages)
        state = getattr(agent, "state", None)
        state_value = getattr(state, "value", str(state))
        candidate_text = (candidate_final_text or "").strip()

        if candidate_text:
            if not self._looks_incomplete(candidate_text):
                return candidate_text
            try:
                repaired = await self._repair_incomplete_candidate(
                    agent=agent,
                    user_request=user_request,
                    candidate_text=candidate_text,
                    run_result=run_result,
                    messages=finalize_messages,
                )
                if repaired:
                    return repaired
            except Exception as exc:
                logger.warning(f"Repair phase failed, fallback to deterministic summary: {exc}")
            return self._build_deterministic_fallback(
                state_value=state_value,
                candidate_final_text=candidate_text,
                run_result=run_result,
                messages=finalize_messages,
            )

        finalize_prompt = self._build_finalize_prompt(
            user_request=user_request,
            run_result=run_result,
            candidate_final_text=candidate_final_text,
            messages=finalize_messages,
        )
        try:
            finalized = await agent.llm.ask(
                messages=[{"role": "user", "content": finalize_prompt}],
                stream=False,
                temperature=0.1,
            )
            cleaned = (finalized or "").strip()
            if cleaned:
                if self._looks_incomplete(cleaned):
                    repaired = await self._repair_incomplete_candidate(
                        agent=agent,
                        user_request=user_request,
                        candidate_text=cleaned,
                        run_result=run_result,
                        messages=finalize_messages,
                    )
                    if repaired:
                        return repaired
                return cleaned
        except Exception as exc:
            logger.warning(f"Finalize phase failed, fallback to deterministic summary: {exc}")

        return self._build_deterministic_fallback(
            state_value=state_value,
            candidate_final_text=candidate_text or None,
            run_result=run_result,
            messages=finalize_messages,
        )

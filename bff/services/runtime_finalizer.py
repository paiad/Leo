from __future__ import annotations

from typing import Any, Callable

from app.agent.manus import Manus
from app.logger import logger


class RuntimeFinalizer:
    def __init__(self, extract_current_user_request: Callable[[str], str]):
        self._extract_current_user_request = extract_current_user_request

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
            if not tool_calls:
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
    ) -> str:
        user_request = self._extract_current_user_request(user_prompt).strip() or user_prompt
        finalize_prompt = self._build_finalize_prompt(
            user_request=user_request,
            run_result=run_result,
            candidate_final_text=candidate_final_text,
            messages=list(agent.messages),
        )
        try:
            finalized = await agent.llm.ask(
                messages=[{"role": "user", "content": finalize_prompt}],
                stream=False,
                temperature=0.1,
            )
            cleaned = (finalized or "").strip()
            if cleaned:
                return cleaned
        except Exception as exc:
            logger.warning(f"Finalize phase failed, fallback to deterministic summary: {exc}")

        if candidate_final_text and candidate_final_text.strip():
            return candidate_final_text.strip()

        state = getattr(agent, "state", None)
        state_value = getattr(state, "value", str(state))
        return (
            "任务执行已结束，但未生成可靠的最终总结。\n"
            f"- 运行状态: {state_value}\n"
            "- 已执行操作: 已运行工具步骤，请查看上方工具轨迹。\n"
            "- 下一步: 你可以让我继续执行“验证结果并给出最终结论”。"
        )

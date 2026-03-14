from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

from app.llm import LLM
from psycopg import connect as pg_connect

from bff.repositories.store import InMemoryStore, PostgresStore
from bff.services.chat.context_memory_helpers import (
    as_str_list,
    count_tokens_rough,
    extract_fact_sentences,
    fact_match_score,
    normalize_decisions,
    safe_json_parse,
    summary_match_score,
    tokenize_for_match,
)
from bff.services.chat.context_memory_repository import ContextMemoryRepository


@dataclass
class ContextBundle:
    text: str = ""
    summary_ids: list[int] = field(default_factory=list)
    fact_ids: list[int] = field(default_factory=list)
    used_tokens: int = 0
    budget_tokens: int = 0


class ContextMemoryService:
    def __init__(self, store: InMemoryStore | PostgresStore):
        self._store = store
        self._enabled = self._is_truthy_env(os.getenv("BFF_CHAT_CONTEXT_MEMORY_ENABLED", "1"))
        self._summary_top = self._env_int("BFF_CHAT_SUMMARY_TOP_K", 1, minimum=0)
        self._facts_top = self._env_int("BFF_CHAT_FACTS_TOP_K", 8, minimum=0)
        self._context_budget = self._env_int("BFF_CHAT_MEMORY_MAX_TOKENS", 1200, minimum=0)
        self._summary_source_window = self._env_int("BFF_CHAT_SUMMARY_SOURCE_MESSAGES", 12, minimum=4)
        self._rolling_active_keep = self._env_int("BFF_CHAT_ROLLING_ACTIVE_KEEP", 8, minimum=2)
        self._stage_trigger = self._env_int("BFF_CHAT_STAGE_TRIGGER", 4, minimum=2)
        self._global_trigger = self._env_int("BFF_CHAT_GLOBAL_TRIGGER", 3, minimum=2)
        self._extract_use_llm = self._is_truthy_env(os.getenv("BFF_CHAT_MEMORY_EXTRACT_USE_LLM", "1"))
        self._extract_llm = LLM(config_name="default") if self._extract_use_llm else None
        self._repo = ContextMemoryRepository(connect_fn=self._connect)

    @staticmethod
    def _is_truthy_env(value: str | None) -> bool:
        return (value or "").strip().lower() in {"1", "true", "yes", "on"}

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

    @property
    def is_available(self) -> bool:
        return self._enabled and isinstance(self._store, PostgresStore) and bool(self._store.database_url)

    def _connect(self):
        if not isinstance(self._store, PostgresStore):
            raise RuntimeError("Context memory requires PostgresStore")
        return pg_connect(self._store.database_url)

    def build_context_bundle(self, *, session_id: str, current_user_text: str) -> ContextBundle:
        if not self.is_available or self._context_budget <= 0:
            return ContextBundle()

        summary_rows, fact_rows = self._repo.fetch_active_summaries_and_facts(session_id=session_id)
        query_terms = tokenize_for_match(current_user_text)
        ranked_summaries = sorted(
            summary_rows,
            key=lambda row: summary_match_score(
                text=str(row.get("summary_text") or ""),
                query_terms=query_terms,
                kind=str(row.get("summary_kind") or "rolling"),
            ),
            reverse=True,
        )[: self._summary_top]
        ranked_facts = sorted(
            fact_rows,
            key=lambda row: fact_match_score(
                key=str(row.get("fact_key") or ""),
                value=str(row.get("fact_value") or ""),
                priority=int(row.get("priority") or 0),
                query_terms=query_terms,
            ),
            reverse=True,
        )[: self._facts_top]

        parts: list[str] = []
        used = 0
        summary_ids: list[int] = []
        fact_ids: list[int] = []

        for row in ranked_summaries:
            text = str(row.get("summary_text") or "").strip()
            if not text:
                continue
            kind = str(row.get("summary_kind") or "rolling")
            candidate = f"[Session Summary:{kind}]\n{text}"
            tk = count_tokens_rough(candidate)
            if used + tk > self._context_budget:
                break
            parts.append(candidate)
            used += tk
            summary_ids.append(int(row["id"]))

        fact_lines: list[str] = []
        for row in ranked_facts:
            line = f"- {str(row.get('fact_key') or '').strip()}: {str(row.get('fact_value') or '').strip()}"
            tk = count_tokens_rough(line)
            if used + tk > self._context_budget:
                break
            fact_lines.append(line)
            used += tk
            fact_ids.append(int(row["id"]))
        if fact_lines:
            parts.append("[Stable Facts]\n" + "\n".join(fact_lines))

        if not parts:
            return ContextBundle()

        self._repo.touch_used_facts(fact_ids=fact_ids)
        return ContextBundle(
            text="\n\n".join(parts),
            summary_ids=summary_ids,
            fact_ids=fact_ids,
            used_tokens=used,
            budget_tokens=self._context_budget,
        )

    def persist_injection_audit(
        self,
        *,
        session_id: str,
        request_message_id: str | None,
        query_text: str,
        bundle: ContextBundle,
    ) -> None:
        if not self.is_available:
            return
        self._repo.persist_injection_audit(
            session_id=session_id,
            request_message_id=request_message_id,
            query_text=query_text,
            summary_ids=bundle.summary_ids,
            fact_ids=bundle.fact_ids,
            budget_tokens=bundle.budget_tokens,
            used_tokens=bundle.used_tokens,
        )

    async def persist_turn_memory(
        self,
        *,
        session_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        if not self.is_available:
            return
        extracted = await self._extract_turn_struct(
            user_message=user_message,
            assistant_message=assistant_message,
        )
        self._repo.upsert_facts(session_id=session_id, facts=extracted.get("facts", []))
        self._repo.upsert_decisions(session_id=session_id, decisions=extracted.get("decisions", []))
        self._repo.insert_rolling_summary(
            session_id=session_id,
            extracted=extracted,
            quality_score=0.85 if self._extract_llm is not None else 0.70,
        )
        self._repo.consolidate_stage_summary(session_id=session_id, trigger=self._stage_trigger)
        self._repo.consolidate_global_summary(session_id=session_id, trigger=self._global_trigger)
        self._repo.trim_rolling_summaries(session_id=session_id, keep=self._rolling_active_keep)

    async def _extract_turn_struct(
        self,
        *,
        user_message: str,
        assistant_message: str,
    ) -> dict[str, Any]:
        if self._extract_llm is not None:
            try:
                return await self._extract_with_llm(
                    user_message=user_message,
                    assistant_message=assistant_message,
                )
            except Exception:
                pass
        return self._extract_with_rules(user_message=user_message, assistant_message=assistant_message)

    async def _extract_with_llm(
        self,
        *,
        user_message: str,
        assistant_message: str,
    ) -> dict[str, Any]:
        assert self._extract_llm is not None
        prompt = (
            "Extract structured memory JSON for one chat turn.\n"
            "Return strict JSON only with keys: goals, constraints, decisions, open_questions, facts.\n"
            "Each value is an array of short strings except decisions can be objects with {key,value,rationale}.\n"
            "Keep max 5 items per list.\n"
            f"USER:\n{user_message}\n\nASSISTANT:\n{assistant_message}"
        )
        raw = await self._extract_llm.ask(
            messages=[{"role": "user", "content": prompt}],
            stream=False,
            temperature=0.1,
        )
        data = safe_json_parse(raw)
        if not isinstance(data, dict):
            raise ValueError("invalid extract json")
        return {
            "goals": as_str_list(data.get("goals")),
            "constraints": as_str_list(data.get("constraints")),
            "open_questions": as_str_list(data.get("open_questions")),
            "facts": as_str_list(data.get("facts")),
            "decisions": normalize_decisions(data.get("decisions")),
        }

    def _extract_with_rules(self, *, user_message: str, assistant_message: str) -> dict[str, Any]:
        constraints = extract_fact_sentences(user_message)
        goals: list[str] = []
        for frag in re.split(r"[。！？!?;\n]+", user_message):
            s = frag.strip()
            if not s:
                continue
            if any(t in s.lower() for t in ("需要", "目标", "希望", "want", "need", "goal")):
                goals.append(s[:180])

        decisions: list[dict[str, str]] = []
        if assistant_message.strip():
            decisions.append(
                {
                    "key": "assistant_commitment",
                    "value": assistant_message.strip()[:180],
                    "rationale": "derived_from_turn",
                }
            )
        return {
            "goals": goals[:5],
            "constraints": constraints[:5],
            "open_questions": [],
            "facts": constraints[:5],
            "decisions": decisions[:5],
        }

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.llm import LLM
from psycopg import connect as pg_connect
from psycopg.rows import dict_row

from bff.repositories.store import InMemoryStore, PostgresStore


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _count_tokens_rough(text: str) -> int:
    if not text:
        return 0
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    non_ascii_chars = len(text) - ascii_chars
    return max(1, (ascii_chars + 3) // 4 + non_ascii_chars)


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
        return pg_connect(self._store.database_url, row_factory=dict_row)

    def build_context_bundle(self, *, session_id: str, current_user_text: str) -> ContextBundle:
        if not self.is_available or self._context_budget <= 0:
            return ContextBundle()

        summary_rows: list[dict[str, Any]] = []
        fact_rows: list[dict[str, Any]] = []
        query_terms = self._tokenize_for_match(current_user_text)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, summary_text, summary_kind, created_at
                    FROM chat_session_summaries
                    WHERE session_id = %s AND status = 'active'
                    ORDER BY created_at DESC
                    LIMIT 30
                    """,
                    (session_id,),
                )
                summary_rows = cur.fetchall()
                cur.execute(
                    """
                    SELECT id, fact_key, fact_value, priority
                    FROM chat_memory_facts
                    WHERE (session_id = %s OR session_id IS NULL) AND status = 'active'
                    ORDER BY priority DESC, updated_at DESC
                    LIMIT 50
                    """,
                    (session_id,),
                )
                fact_rows = cur.fetchall()

        ranked_summaries = sorted(
            summary_rows,
            key=lambda row: self._summary_match_score(
                text=str(row.get("summary_text") or ""),
                query_terms=query_terms,
                kind=str(row.get("summary_kind") or "rolling"),
            ),
            reverse=True,
        )[: self._summary_top]
        ranked_facts = sorted(
            fact_rows,
            key=lambda row: self._fact_match_score(
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

        if ranked_summaries:
            for row in ranked_summaries:
                text = str(row.get("summary_text") or "").strip()
                if not text:
                    continue
                kind = str(row.get("summary_kind") or "rolling")
                candidate = f"[Session Summary:{kind}]\n{text}"
                tk = _count_tokens_rough(candidate)
                if used + tk > self._context_budget:
                    break
                parts.append(candidate)
                used += tk
                summary_ids.append(int(row["id"]))

        if ranked_facts:
            fact_lines: list[str] = []
            for row in ranked_facts:
                line = f"- {str(row.get('fact_key') or '').strip()}: {str(row.get('fact_value') or '').strip()}"
                tk = _count_tokens_rough(line)
                if used + tk > self._context_budget:
                    break
                fact_lines.append(line)
                used += tk
                fact_ids.append(int(row["id"]))
            if fact_lines:
                parts.append("[Stable Facts]\n" + "\n".join(fact_lines))

        if not parts:
            return ContextBundle()
        self._touch_used_facts(fact_ids=fact_ids)
        return ContextBundle(
            text="\n\n".join(parts),
            summary_ids=summary_ids,
            fact_ids=fact_ids,
            used_tokens=used,
            budget_tokens=self._context_budget,
        )

    def _touch_used_facts(self, *, fact_ids: list[int]) -> None:
        if not fact_ids:
            return
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE chat_memory_facts
                    SET last_used_at = %s, last_used_at_ts = %s::timestamptz
                    WHERE id = ANY(%s)
                    """,
                    (_now_iso(), _now_iso(), fact_ids),
                )
            conn.commit()

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
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chat_context_injections (
                        session_id,
                        request_message_id,
                        summary_ids,
                        fact_ids,
                        prompt_budget_tokens,
                        used_tokens,
                        overflow_strategy,
                        created_at,
                        query_text,
                        retrieval_strategy,
                        dropped_item_ids
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        session_id,
                        request_message_id,
                        bundle.summary_ids,
                        bundle.fact_ids,
                        bundle.budget_tokens,
                        bundle.used_tokens,
                        "drop_low_priority_facts",
                        _now_iso(),
                        query_text[:1200],
                        "priority_topk",
                        [],
                    ),
                )
            conn.commit()

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
        self._upsert_facts(
            session_id=session_id,
            facts=extracted.get("facts", []),
        )
        self._upsert_decisions(
            session_id=session_id,
            decisions=extracted.get("decisions", []),
        )
        self._insert_rolling_summary(
            session_id=session_id,
            extracted=extracted,
        )
        self._consolidate_stage_summary(session_id=session_id)
        self._consolidate_global_summary(session_id=session_id)
        self._trim_rolling_summaries(session_id=session_id)

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
        return self._extract_with_rules(
            user_message=user_message,
            assistant_message=assistant_message,
        )

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
        data = self._safe_json_parse(raw)
        if not isinstance(data, dict):
            raise ValueError("invalid extract json")
        return {
            "goals": self._as_str_list(data.get("goals")),
            "constraints": self._as_str_list(data.get("constraints")),
            "open_questions": self._as_str_list(data.get("open_questions")),
            "facts": self._as_str_list(data.get("facts")),
            "decisions": self._normalize_decisions(data.get("decisions")),
        }

    def _extract_with_rules(self, *, user_message: str, assistant_message: str) -> dict[str, Any]:
        constraints = self._extract_fact_sentences(user_message)
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

    def _upsert_facts(self, *, session_id: str, facts: list[str]) -> None:
        if not facts:
            return
        now = _now_iso()
        with self._connect() as conn:
            with conn.cursor() as cur:
                for sentence in facts[:10]:
                    normalized_hash = hashlib.md5(
                        f"constraint|{sentence.strip().lower()}".encode("utf-8")
                    ).hexdigest()
                    fact_key = "fact_" + normalized_hash[:12]
                    cur.execute(
                        """
                        SELECT id
                        FROM chat_memory_facts
                        WHERE session_id = %s AND normalized_fact_hash = %s AND status = 'active'
                        ORDER BY updated_at DESC
                        LIMIT 1
                        """,
                        (session_id, normalized_hash),
                    )
                    existing = cur.fetchone()
                    if existing:
                        cur.execute(
                            """
                            UPDATE chat_memory_facts
                            SET fact_value = %s,
                                updated_at = %s,
                                updated_at_ts = %s::timestamptz,
                                priority = GREATEST(priority, %s)
                            WHERE id = %s
                            """,
                            (sentence, now, now, 85, int(existing["id"])),
                        )
                    else:
                        cur.execute(
                            """
                            INSERT INTO chat_memory_facts (
                                session_id, fact_type, fact_key, fact_value, confidence, priority,
                                effective_from, status, created_at, updated_at, normalized_fact_hash,
                                created_at_ts, updated_at_ts, effective_from_ts
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s, %s::timestamptz, %s::timestamptz, %s::timestamptz)
                            """,
                            (
                                session_id,
                                "constraint",
                                fact_key,
                                sentence,
                                0.86,
                                85,
                                now,
                                now,
                                now,
                                normalized_hash,
                                now,
                                now,
                                now,
                            ),
                        )
            conn.commit()

    def _upsert_decisions(self, *, session_id: str, decisions: list[dict[str, str]]) -> None:
        if not decisions:
            return
        now = _now_iso()
        with self._connect() as conn:
            with conn.cursor() as cur:
                for item in decisions[:10]:
                    key = (item.get("key") or "").strip()[:120]
                    value = (item.get("value") or "").strip()[:500]
                    rationale = (item.get("rationale") or "").strip()[:500]
                    if not key or not value:
                        continue
                    cur.execute(
                        """
                        SELECT id
                        FROM chat_decisions
                        WHERE session_id = %s AND decision_key = %s AND status = 'active'
                        ORDER BY updated_at DESC
                        LIMIT 1
                        """,
                        (session_id, key),
                    )
                    existing = cur.fetchone()
                    if existing:
                        cur.execute(
                            """
                            UPDATE chat_decisions
                            SET decision_value = %s,
                                rationale = %s,
                                updated_at = %s,
                                updated_at_ts = %s::timestamptz
                            WHERE id = %s
                            """,
                            (value, rationale, now, now, int(existing["id"])),
                        )
                        continue
                    cur.execute(
                        """
                        INSERT INTO chat_decisions (
                            session_id, decision_key, decision_value, rationale, status,
                            created_at, updated_at, created_at_ts, updated_at_ts
                        )
                        VALUES (%s, %s, %s, %s, 'active', %s, %s, %s::timestamptz, %s::timestamptz)
                        """,
                        (session_id, key, value, rationale, now, now, now, now),
                    )
            conn.commit()

    def _insert_rolling_summary(self, *, session_id: str, extracted: dict[str, Any]) -> None:
        text_sections: list[str] = []
        for title, key in (
            ("Goals", "goals"),
            ("Constraints", "constraints"),
            ("Decisions", "decisions"),
            ("Open Questions", "open_questions"),
        ):
            items = extracted.get(key) or []
            if key == "decisions":
                lines = [
                    f"- {(d.get('key') or '').strip()}: {(d.get('value') or '').strip()}"
                    for d in items
                    if isinstance(d, dict)
                ]
            else:
                lines = [f"- {str(v).strip()}" for v in items if str(v).strip()]
            if lines:
                text_sections.append(f"{title}:\n" + "\n".join(lines[:5]))
        if not text_sections:
            return
        summary_text = "\n\n".join(text_sections)
        now = _now_iso()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chat_session_summaries (
                        session_id, summary_level, summary_kind, summary_text, summary_json,
                        message_count, approx_tokens, created_at, created_at_ts, parent_summary_ids,
                        quality_score, status
                    )
                    VALUES (%s, 1, 'rolling', %s, %s::jsonb, %s, %s, %s, %s::timestamptz, %s, %s, 'active')
                    """,
                    (
                        session_id,
                        summary_text[:2400],
                        json.dumps(extracted, ensure_ascii=False),
                        1,
                        _count_tokens_rough(summary_text),
                        now,
                        now,
                        [],
                        0.85 if self._extract_llm is not None else 0.70,
                    ),
                )
            conn.commit()

    def _consolidate_stage_summary(self, *, session_id: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, summary_text
                    FROM chat_session_summaries
                    WHERE session_id = %s AND status = 'active' AND summary_kind = 'rolling'
                    ORDER BY created_at ASC
                    """,
                    (session_id,),
                )
                rows = cur.fetchall()
                if len(rows) < self._stage_trigger:
                    return
                picked = rows[: self._stage_trigger]
                parent_ids = [int(r["id"]) for r in picked]
                summary_text = self._merge_summary_texts([str(r["summary_text"]) for r in picked], max_len=2600)
                now = _now_iso()
                cur.execute(
                    """
                    INSERT INTO chat_session_summaries (
                        session_id, summary_level, summary_kind, summary_text, summary_json,
                        message_count, approx_tokens, created_at, created_at_ts, parent_summary_ids,
                        quality_score, status
                    )
                    VALUES (%s, 2, 'stage', %s, %s::jsonb, %s, %s, %s, %s::timestamptz, %s, %s, 'active')
                    """,
                    (
                        session_id,
                        summary_text,
                        "{}",
                        len(picked),
                        _count_tokens_rough(summary_text),
                        now,
                        now,
                        parent_ids,
                        0.80,
                    ),
                )
                cur.execute(
                    "UPDATE chat_session_summaries SET status='superseded' WHERE id = ANY(%s)",
                    (parent_ids,),
                )
            conn.commit()

    def _consolidate_global_summary(self, *, session_id: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, summary_text
                    FROM chat_session_summaries
                    WHERE session_id = %s AND status = 'active' AND summary_kind = 'stage'
                    ORDER BY created_at ASC
                    """,
                    (session_id,),
                )
                rows = cur.fetchall()
                if len(rows) < self._global_trigger:
                    return
                picked = rows[: self._global_trigger]
                parent_ids = [int(r["id"]) for r in picked]
                summary_text = self._merge_summary_texts([str(r["summary_text"]) for r in picked], max_len=3000)
                now = _now_iso()
                cur.execute(
                    """
                    INSERT INTO chat_session_summaries (
                        session_id, summary_level, summary_kind, summary_text, summary_json,
                        message_count, approx_tokens, created_at, created_at_ts, parent_summary_ids,
                        quality_score, status
                    )
                    VALUES (%s, 3, 'global', %s, %s::jsonb, %s, %s, %s, %s::timestamptz, %s, %s, 'active')
                    """,
                    (
                        session_id,
                        summary_text,
                        "{}",
                        len(picked),
                        _count_tokens_rough(summary_text),
                        now,
                        now,
                        parent_ids,
                        0.78,
                    ),
                )
                cur.execute(
                    "UPDATE chat_session_summaries SET status='superseded' WHERE id = ANY(%s)",
                    (parent_ids,),
                )
            conn.commit()

    def _trim_rolling_summaries(self, *, session_id: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id
                    FROM chat_session_summaries
                    WHERE session_id = %s AND summary_kind = 'rolling' AND status = 'active'
                    ORDER BY created_at DESC
                    """,
                    (session_id,),
                )
                rows = cur.fetchall()
                if len(rows) <= self._rolling_active_keep:
                    return
                drop_ids = [int(r["id"]) for r in rows[self._rolling_active_keep :]]
                cur.execute(
                    "UPDATE chat_session_summaries SET status='superseded' WHERE id = ANY(%s)",
                    (drop_ids,),
                )
            conn.commit()

    @staticmethod
    def _tokenize_for_match(text: str) -> set[str]:
        if not text:
            return set()
        return {w for w in re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]+", text.lower()) if len(w) >= 2}

    def _summary_match_score(self, *, text: str, query_terms: set[str], kind: str) -> float:
        terms = self._tokenize_for_match(text)
        overlap = len(query_terms & terms)
        base = float(overlap)
        if kind == "global":
            base += 0.6
        elif kind == "stage":
            base += 0.3
        return base

    def _fact_match_score(self, *, key: str, value: str, priority: int, query_terms: set[str]) -> float:
        text_terms = self._tokenize_for_match(f"{key} {value}")
        overlap = len(query_terms & text_terms)
        return overlap * 2.0 + min(priority, 100) / 100.0

    @staticmethod
    def _safe_json_parse(raw: str) -> Any:
        text = (raw or "").strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            m = re.search(r"\{[\s\S]*\}", text)
            if not m:
                return None
            try:
                return json.loads(m.group(0))
            except Exception:
                return None

    @staticmethod
    def _as_str_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for item in value:
            s = str(item).strip()
            if s:
                result.append(s[:220])
        return result[:5]

    @staticmethod
    def _normalize_decisions(value: Any) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        out: list[dict[str, str]] = []
        for item in value:
            if isinstance(item, dict):
                key = str(item.get("key") or "").strip()
                val = str(item.get("value") or "").strip()
                rationale = str(item.get("rationale") or "").strip()
            else:
                key = ""
                val = str(item).strip()
                rationale = ""
            if not val:
                continue
            if not key:
                key = "decision_" + hashlib.sha1(val.lower().encode("utf-8")).hexdigest()[:10]
            out.append({"key": key[:120], "value": val[:500], "rationale": rationale[:500]})
        return out[:5]

    @staticmethod
    def _merge_summary_texts(texts: list[str], *, max_len: int) -> str:
        lines: list[str] = []
        seen: set[str] = set()
        for text in texts:
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                normalized = re.sub(r"\s+", " ", line.lower())
                if normalized in seen:
                    continue
                seen.add(normalized)
                lines.append(line)
                if len("\n".join(lines)) >= max_len:
                    merged = "\n".join(lines)
                    return merged[:max_len].rstrip() + "..."
        return "\n".join(lines)[:max_len]

    @staticmethod
    def _extract_fact_sentences(text: str) -> list[str]:
        # Capture explicit preference/constraint lines and keep them short.
        signal = (
            "记住",
            "请记住",
            "偏好",
            "必须",
            "不要",
            "always",
            "never",
            "remember",
            "prefer",
            "must",
            "do not",
        )
        fragments = re.split(r"[。！？!?;\n]+", text)
        facts: list[str] = []
        for fragment in fragments:
            sentence = fragment.strip()
            if not sentence:
                continue
            lower = sentence.lower()
            if any(s in sentence for s in signal[:5]) or any(s in lower for s in signal[5:]):
                if len(sentence) > 220:
                    sentence = sentence[:220].rstrip() + "..."
                facts.append(sentence)
        return facts[:5]

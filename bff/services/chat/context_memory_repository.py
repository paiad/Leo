from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from psycopg.rows import dict_row

from bff.services.chat.context_memory_helpers import (
    build_rolling_summary_text,
    count_tokens_rough,
    merge_summary_texts,
    now_iso,
)


class ContextMemoryRepository:
    def __init__(self, connect_fn: Callable[[], AbstractContextManager[Any]]):
        self._connect = connect_fn

    def fetch_active_summaries_and_facts(self, *, session_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        summary_rows: list[dict[str, Any]] = []
        fact_rows: list[dict[str, Any]] = []
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
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
        return summary_rows, fact_rows

    def purge_session_memory(self, *, session_id: str) -> None:
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("DELETE FROM chat_context_injections WHERE session_id = %s", (session_id,))
                cur.execute("DELETE FROM chat_decisions WHERE session_id = %s", (session_id,))
                cur.execute("DELETE FROM chat_memory_facts WHERE session_id = %s", (session_id,))
                cur.execute("DELETE FROM chat_session_summaries WHERE session_id = %s", (session_id,))
            conn.commit()

    def touch_used_facts(self, *, fact_ids: list[int]) -> None:
        if not fact_ids:
            return
        now = now_iso()
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    UPDATE chat_memory_facts
                    SET last_used_at = %s, last_used_at_ts = %s::timestamptz
                    WHERE id = ANY(%s)
                    """,
                    (now, now, fact_ids),
                )
            conn.commit()

    def persist_injection_audit(
        self,
        *,
        session_id: str,
        request_message_id: str | None,
        query_text: str,
        summary_ids: list[int],
        fact_ids: list[int],
        budget_tokens: int,
        used_tokens: int,
    ) -> None:
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
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
                        summary_ids,
                        fact_ids,
                        budget_tokens,
                        used_tokens,
                        "drop_low_priority_facts",
                        now_iso(),
                        query_text[:1200],
                        "priority_topk",
                        [],
                    ),
                )
            conn.commit()

    def upsert_facts(self, *, session_id: str, facts: list[str]) -> None:
        if not facts:
            return
        now = now_iso()
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
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
                        continue

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

    def upsert_decisions(self, *, session_id: str, decisions: list[dict[str, str]]) -> None:
        if not decisions:
            return
        now = now_iso()
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
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

    def insert_rolling_summary(self, *, session_id: str, extracted: dict[str, Any], quality_score: float) -> None:
        summary_text = build_rolling_summary_text(extracted)
        if not summary_text:
            return
        now = now_iso()
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
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
                        count_tokens_rough(summary_text),
                        now,
                        now,
                        [],
                        quality_score,
                    ),
                )
            conn.commit()

    def consolidate_stage_summary(self, *, session_id: str, trigger: int) -> None:
        self._consolidate_summary(
            session_id=session_id,
            source_kind="rolling",
            target_kind="stage",
            target_level=2,
            trigger=trigger,
            max_len=2600,
            quality_score=0.80,
        )

    def consolidate_global_summary(self, *, session_id: str, trigger: int) -> None:
        self._consolidate_summary(
            session_id=session_id,
            source_kind="stage",
            target_kind="global",
            target_level=3,
            trigger=trigger,
            max_len=3000,
            quality_score=0.78,
        )

    def _consolidate_summary(
        self,
        *,
        session_id: str,
        source_kind: str,
        target_kind: str,
        target_level: int,
        trigger: int,
        max_len: int,
        quality_score: float,
    ) -> None:
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT id, summary_text
                    FROM chat_session_summaries
                    WHERE session_id = %s AND status = 'active' AND summary_kind = %s
                    ORDER BY created_at ASC
                    """,
                    (session_id, source_kind),
                )
                rows = cur.fetchall()
                if len(rows) < trigger:
                    return
                picked = rows[:trigger]
                parent_ids = [int(r["id"]) for r in picked]
                summary_text = merge_summary_texts([str(r["summary_text"]) for r in picked], max_len=max_len)
                now = now_iso()
                cur.execute(
                    """
                    INSERT INTO chat_session_summaries (
                        session_id, summary_level, summary_kind, summary_text, summary_json,
                        message_count, approx_tokens, created_at, created_at_ts, parent_summary_ids,
                        quality_score, status
                    )
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::timestamptz, %s, %s, 'active')
                    """,
                    (
                        session_id,
                        target_level,
                        target_kind,
                        summary_text,
                        "{}",
                        len(picked),
                        count_tokens_rough(summary_text),
                        now,
                        now,
                        parent_ids,
                        quality_score,
                    ),
                )
                cur.execute(
                    "UPDATE chat_session_summaries SET status='superseded' WHERE id = ANY(%s)",
                    (parent_ids,),
                )
            conn.commit()

    def trim_rolling_summaries(self, *, session_id: str, keep: int) -> None:
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
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
                if len(rows) <= keep:
                    return
                drop_ids = [int(r["id"]) for r in rows[keep:]]
                cur.execute(
                    "UPDATE chat_session_summaries SET status='superseded' WHERE id = ANY(%s)",
                    (drop_ids,),
                )
            conn.commit()

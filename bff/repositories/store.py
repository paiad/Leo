from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock

from psycopg import connect as pg_connect
from psycopg.rows import dict_row

from bff.domain.models import (
    MessageRecord,
    McpDiscoveredTool,
    McpRoutingPolicyRecord,
    McpServerRecord,
    SessionRecord,
    new_id,
    now_iso,
)
from bff.utils.memory_settings import chat_session_store_path


@dataclass
class InMemoryStore:
    """
    Runtime store with optional session persistence.

    Naming is kept for backward compatibility, but when enable_persistence=True
    this store is effectively memory + JSON snapshot persistence.
    """
    sessions: dict[str, SessionRecord] = field(default_factory=dict)
    mcp_servers: dict[str, McpServerRecord] = field(default_factory=dict)
    mcp_routing_policies: dict[str, McpRoutingPolicyRecord] = field(default_factory=dict)
    mcp_routing_events: list[dict[str, object]] = field(default_factory=list)
    enable_persistence: bool = True
    persistence_path: str | None = None
    _persist_lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.enable_persistence:
            return
        self._load_sessions()

    def persist_sessions(self) -> None:
        if not self.enable_persistence:
            return
        state_path = self._state_file_path()
        if state_path is None:
            return
        state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "sessions": {
                sid: session.model_dump()
                for sid, session in sorted(self.sessions.items(), key=lambda item: item[0])
            },
        }
        temp_path = state_path.with_suffix(state_path.suffix + ".tmp")
        with self._persist_lock:
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(state_path)

    def _state_file_path(self) -> Path | None:
        path = chat_session_store_path(self.persistence_path)
        return path if str(path).strip() else None

    def _load_sessions(self) -> None:
        state_path = self._state_file_path()
        if state_path is None or not state_path.exists():
            return

        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            return

        raw_sessions = payload.get("sessions")
        if not isinstance(raw_sessions, dict):
            return

        loaded: dict[str, SessionRecord] = {}
        for sid, item in raw_sessions.items():
            if not isinstance(sid, str) or not isinstance(item, dict):
                continue
            try:
                session = SessionRecord.model_validate(item)
            except Exception:
                continue
            loaded[sid] = session
        if loaded:
            self.sessions.update(loaded)

    def persist_mcp_servers(self) -> None:
        # In-memory/json mode does not persist MCP servers at store layer.
        return

    def get_mcp_routing_policy(
        self, intent: str, server_id: str
    ) -> McpRoutingPolicyRecord | None:
        key_exact = f"{(intent or '').strip().lower()}:{(server_id or '').strip().lower()}"
        policy = self.mcp_routing_policies.get(key_exact)
        if policy is not None:
            return policy
        key_wildcard = f"*:{(server_id or '').strip().lower()}"
        return self.mcp_routing_policies.get(key_wildcard)

    def record_mcp_routing_event(self, payload: dict[str, object]) -> None:
        event = dict(payload or {})
        event.setdefault("id", new_id())
        event.setdefault("createdAt", now_iso())
        self.mcp_routing_events.append(event)
        # Keep a bounded in-memory ring to avoid unbounded growth.
        if len(self.mcp_routing_events) > 2000:
            self.mcp_routing_events = self.mcp_routing_events[-2000:]

    def delete_mcp_routing_events_by_session(self, session_id: str) -> int:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return 0
        before = len(self.mcp_routing_events)
        self.mcp_routing_events = [
            event
            for event in self.mcp_routing_events
            if str((event or {}).get("session_id") or "").strip() != normalized_session_id
        ]
        return before - len(self.mcp_routing_events)

    def delete_legacy_mcp_routing_events(self) -> int:
        before = len(self.mcp_routing_events)
        self.mcp_routing_events = [
            event
            for event in self.mcp_routing_events
            if str((event or {}).get("session_id") or "").strip()
        ]
        return before - len(self.mcp_routing_events)

    def list_mcp_routing_events(
        self,
        *,
        limit: int = 100,
        event_type: str | None = None,
        since_iso: str | None = None,
    ) -> list[dict[str, object]]:
        normalized_limit = max(1, min(1000, int(limit or 100)))
        normalized_event_type = (event_type or "").strip().lower()
        filtered: list[dict[str, object]] = []
        for event in self.mcp_routing_events:
            item = dict(event or {})
            item_type = str(item.get("event_type") or "").strip().lower()
            created_at = str(item.get("createdAt") or item.get("created_at") or "")
            if normalized_event_type and item_type != normalized_event_type:
                continue
            if since_iso and created_at and created_at < since_iso:
                continue
            if created_at and "createdAt" not in item:
                item["createdAt"] = created_at
            filtered.append(item)

        filtered.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
        return filtered[:normalized_limit]


@dataclass
class PostgresStore:
    sessions: dict[str, SessionRecord] = field(default_factory=dict)
    mcp_servers: dict[str, McpServerRecord] = field(default_factory=dict)
    mcp_routing_policies: dict[str, McpRoutingPolicyRecord] = field(default_factory=dict)
    database_url: str = ""
    _persist_lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        self._init_schema()
        self._load_sessions()
        self._load_mcp_servers()
        self._load_mcp_routing_policies()
        if not self.sessions:
            self._bootstrap_from_json_snapshot()

    def _connect(self):
        return pg_connect(self.database_url, row_factory=dict_row)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chat_sessions (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        source TEXT NOT NULL DEFAULT 'browser',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chat_messages (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        model TEXT,
                        user_input_type TEXT NOT NULL DEFAULT 'text',
                        tool_events_json TEXT NOT NULL DEFAULT '[]',
                        decision_events_json TEXT NOT NULL DEFAULT '[]'
                    )
                    """
                )
                cur.execute(
                    "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS user_input_type TEXT NOT NULL DEFAULT 'text'"
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS mcp_servers (
                        server_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        type TEXT NOT NULL,
                        command TEXT,
                        args_json TEXT NOT NULL DEFAULT '[]',
                        env_json TEXT NOT NULL DEFAULT '{}',
                        url TEXT,
                        description TEXT NOT NULL DEFAULT '',
                        enabled BOOLEAN NOT NULL DEFAULT TRUE,
                        discovered_tools_json TEXT NOT NULL DEFAULT '[]',
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_mcp_servers_enabled ON mcp_servers(enabled)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created ON chat_messages(session_id, created_at)"
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runtime_mcp_routing_policies (
                        intent TEXT NOT NULL,
                        server_id TEXT NOT NULL,
                        enabled BOOLEAN NOT NULL DEFAULT TRUE,
                        score_bias INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (intent, server_id)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runtime_mcp_routing_events (
                        id TEXT PRIMARY KEY,
                        session_id TEXT,
                        event_type TEXT NOT NULL DEFAULT 'decision',
                        prompt_hash TEXT NOT NULL,
                        intent TEXT NOT NULL,
                        selected_server_id TEXT,
                        candidate_servers_json TEXT NOT NULL DEFAULT '[]',
                        scores_json TEXT NOT NULL DEFAULT '{}',
                        connected_servers_json TEXT NOT NULL DEFAULT '[]',
                        used_servers_json TEXT NOT NULL DEFAULT '[]',
                        success BOOLEAN,
                        latency_ms INTEGER,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                # Backward-compatible migration for existing tables.
                cur.execute(
                    "ALTER TABLE runtime_mcp_routing_events ADD COLUMN IF NOT EXISTS session_id TEXT"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_runtime_mcp_routing_events_created ON runtime_mcp_routing_events(created_at)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_runtime_mcp_routing_events_session_created ON runtime_mcp_routing_events(session_id, created_at)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_runtime_mcp_routing_events_intent_server ON runtime_mcp_routing_events(intent, selected_server_id)"
                )
            conn.commit()

    def _load_sessions(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, title, source, created_at, updated_at
                    FROM chat_sessions
                    ORDER BY updated_at DESC
                    """
                )
                session_rows = cur.fetchall()

                cur.execute(
                    """
                    SELECT id, session_id, role, content, created_at, model, user_input_type, tool_events_json, decision_events_json
                    FROM chat_messages
                    ORDER BY created_at ASC, id ASC
                    """
                )
                message_rows = cur.fetchall()

        sessions: dict[str, SessionRecord] = {}
        for row in session_rows:
            sid = str(row["id"])
            try:
                sessions[sid] = SessionRecord(
                    id=sid,
                    title=str(row["title"]),
                    createdAt=str(row["created_at"]),
                    updatedAt=str(row["updated_at"]),
                    source=str(row["source"]) if row["source"] in {"browser", "lark"} else "browser",
                    messages=[],
                )
            except Exception:
                continue

        for row in message_rows:
            sid = str(row["session_id"])
            session = sessions.get(sid)
            if session is None:
                continue
            try:
                tool_events = json.loads(str(row["tool_events_json"] or "[]"))
                decision_events = json.loads(str(row["decision_events_json"] or "[]"))
                session.messages.append(
                    MessageRecord(
                        id=str(row["id"]),
                        role=str(row["role"]),
                        content=str(row["content"] or ""),
                        createdAt=str(row["created_at"]),
                        model=row["model"],
                        userInputType=(
                            "audio_asr" if str(row.get("user_input_type") or "text") == "audio_asr" else "text"
                        ),
                        toolEvents=tool_events if isinstance(tool_events, list) else [],
                        decisionEvents=decision_events if isinstance(decision_events, list) else [],
                    )
                )
            except Exception:
                continue

        # Re-validate via pydantic for safety.
        validated: dict[str, SessionRecord] = {}
        for sid, session in sessions.items():
            try:
                validated[sid] = SessionRecord.model_validate(session.model_dump())
            except Exception:
                continue
        self.sessions.update(validated)

    def _load_mcp_servers(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        server_id,
                        name,
                        type,
                        command,
                        args_json,
                        env_json,
                        url,
                        description,
                        enabled,
                        discovered_tools_json
                    FROM mcp_servers
                    ORDER BY server_id ASC
                    """
                )
                rows = cur.fetchall()

        loaded: dict[str, McpServerRecord] = {}
        for row in rows:
            try:
                args = json.loads(str(row["args_json"] or "[]"))
                env = json.loads(str(row["env_json"] or "{}"))
                discovered_raw = json.loads(str(row["discovered_tools_json"] or "[]"))
                discovered: list[McpDiscoveredTool] = []
                if isinstance(discovered_raw, list):
                    for item in discovered_raw:
                        if isinstance(item, dict):
                            try:
                                discovered.append(McpDiscoveredTool.model_validate(item))
                            except Exception:
                                continue

                record = McpServerRecord(
                    serverId=str(row["server_id"]),
                    name=str(row["name"]),
                    type=(
                        str(row["type"])
                        if row["type"] in {"stdio", "sse", "http", "streamablehttp"}
                        else "stdio"
                    ),
                    command=row["command"],
                    args=args if isinstance(args, list) else [],
                    env=env if isinstance(env, dict) else {},
                    url=row["url"],
                    description=str(row["description"] or ""),
                    enabled=bool(row["enabled"]),
                    discoveredTools=discovered,
                )
                loaded[record.serverId] = record
            except Exception:
                continue

        self.mcp_servers.update(loaded)

    def _load_mcp_routing_policies(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT intent, server_id, enabled, score_bias, updated_at
                    FROM runtime_mcp_routing_policies
                    ORDER BY intent ASC, server_id ASC
                    """
                )
                rows = cur.fetchall()

        loaded: dict[str, McpRoutingPolicyRecord] = {}
        for row in rows:
            try:
                intent = str(row["intent"] or "").strip().lower()
                server_id = str(row["server_id"] or "").strip().lower()
                if not intent or not server_id:
                    continue
                policy = McpRoutingPolicyRecord(
                    intent=intent,
                    serverId=server_id,
                    enabled=bool(row["enabled"]),
                    scoreBias=int(row["score_bias"] or 0),
                    updatedAt=str(row["updated_at"] or now_iso()),
                )
                loaded[f"{intent}:{server_id}"] = policy
            except Exception:
                continue
        self.mcp_routing_policies.update(loaded)

    def _bootstrap_from_json_snapshot(self) -> None:
        state_path = chat_session_store_path(None)
        if not state_path.exists():
            return
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        raw_sessions = payload.get("sessions")
        if not isinstance(raw_sessions, dict):
            return
        loaded: dict[str, SessionRecord] = {}
        for sid, item in raw_sessions.items():
            if not isinstance(sid, str) or not isinstance(item, dict):
                continue
            try:
                loaded[sid] = SessionRecord.model_validate(item)
            except Exception:
                continue
        if not loaded:
            return
        self.sessions.update(loaded)
        self.persist_sessions()

    def persist_sessions(self) -> None:
        with self._persist_lock:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    current_ids = set(self.sessions.keys())

                    # Delete removed sessions first, messages are cascaded.
                    if current_ids:
                        placeholders = ",".join(["%s"] * len(current_ids))
                        cur.execute(
                            f"DELETE FROM chat_sessions WHERE id NOT IN ({placeholders})",
                            tuple(current_ids),
                        )
                    else:
                        cur.execute("DELETE FROM chat_messages")
                        cur.execute("DELETE FROM chat_sessions")
                        conn.commit()
                        return

                    # Keep session metadata in sync via idempotent upsert.
                    for sid, session in self.sessions.items():
                        cur.execute(
                            """
                            INSERT INTO chat_sessions (id, title, source, created_at, updated_at)
                            VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT (id) DO UPDATE
                            SET title = EXCLUDED.title,
                                source = EXCLUDED.source,
                                created_at = EXCLUDED.created_at,
                                updated_at = EXCLUDED.updated_at
                            """,
                            (
                                sid,
                                session.title,
                                session.source,
                                session.createdAt,
                                session.updatedAt,
                            ),
                        )

                    # Incremental message persistence:
                    # 1) insert only newly seen message ids
                    # 2) delete message ids removed from in-memory state
                    current_sid_list = list(current_ids)
                    cur.execute(
                        """
                        SELECT
                            id,
                            session_id,
                            role,
                            content,
                            created_at,
                            model,
                            user_input_type,
                            tool_events_json,
                            decision_events_json
                        FROM chat_messages
                        WHERE session_id = ANY(%s)
                        """,
                        (current_sid_list,),
                    )
                    existing_rows = cur.fetchall()
                    existing_ids_by_session: dict[str, set[str]] = {}
                    existing_payload_by_session: dict[str, dict[str, tuple[object, ...]]] = {}
                    for row in existing_rows:
                        sid = str(row["session_id"])
                        msg_id = str(row["id"])
                        existing_ids_by_session.setdefault(sid, set()).add(msg_id)
                        existing_payload_by_session.setdefault(sid, {})[msg_id] = (
                            str(row["role"]),
                            str(row["content"] or ""),
                            str(row["created_at"]),
                            row["model"],
                            ("audio_asr" if str(row["user_input_type"] or "text") == "audio_asr" else "text"),
                            str(row["tool_events_json"] or "[]"),
                            str(row["decision_events_json"] or "[]"),
                        )

                    for sid, session in self.sessions.items():
                        existing_ids = existing_ids_by_session.get(sid, set())
                        current_message_ids = {msg.id for msg in session.messages}

                        removed_ids = list(existing_ids - current_message_ids)
                        if removed_ids:
                            cur.execute(
                                """
                                DELETE FROM chat_messages
                                WHERE session_id = %s AND id = ANY(%s)
                                """,
                                (sid, removed_ids),
                            )

                        existing_payload = existing_payload_by_session.get(sid, {})
                        changed_messages: list[MessageRecord] = []
                        for msg in session.messages:
                            serialized_tool_events = json.dumps(
                                msg.toolEvents or [], ensure_ascii=False
                            )
                            serialized_decision_events = json.dumps(
                                msg.decisionEvents or [], ensure_ascii=False
                            )
                            current_payload = (
                                msg.role,
                                msg.content,
                                msg.createdAt,
                                msg.model,
                                ("audio_asr" if msg.userInputType == "audio_asr" else "text"),
                                serialized_tool_events,
                                serialized_decision_events,
                            )
                            if existing_payload.get(msg.id) != current_payload:
                                changed_messages.append(msg)

                        if changed_messages:
                            cur.executemany(
                                """
                                INSERT INTO chat_messages (
                                    id, session_id, role, content, created_at, model, user_input_type, tool_events_json, decision_events_json
                                )
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                                ON CONFLICT (id) DO UPDATE
                                SET
                                    session_id = EXCLUDED.session_id,
                                    role = EXCLUDED.role,
                                    content = EXCLUDED.content,
                                    created_at = EXCLUDED.created_at,
                                    model = EXCLUDED.model,
                                    user_input_type = EXCLUDED.user_input_type,
                                    tool_events_json = EXCLUDED.tool_events_json,
                                    decision_events_json = EXCLUDED.decision_events_json
                                """,
                                [
                                    (
                                        msg.id,
                                        sid,
                                        msg.role,
                                        msg.content,
                                        msg.createdAt,
                                        msg.model,
                                        ("audio_asr" if msg.userInputType == "audio_asr" else "text"),
                                        json.dumps(msg.toolEvents or [], ensure_ascii=False),
                                        json.dumps(msg.decisionEvents or [], ensure_ascii=False),
                                    )
                                    for msg in changed_messages
                                ],
                            )
                conn.commit()

    def persist_mcp_servers(self) -> None:
        with self._persist_lock:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    current_ids = set(self.mcp_servers.keys())
                    if current_ids:
                        placeholders = ",".join(["%s"] * len(current_ids))
                        cur.execute(
                            f"DELETE FROM mcp_servers WHERE server_id NOT IN ({placeholders})",
                            tuple(current_ids),
                        )
                    else:
                        cur.execute("DELETE FROM mcp_servers")

                    updated_at = now_iso()
                    for server_id, server in self.mcp_servers.items():
                        cur.execute(
                            """
                            INSERT INTO mcp_servers (
                                server_id,
                                name,
                                type,
                                command,
                                args_json,
                                env_json,
                                url,
                                description,
                                enabled,
                                discovered_tools_json,
                                updated_at
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (server_id) DO UPDATE
                            SET
                                name = EXCLUDED.name,
                                type = EXCLUDED.type,
                                command = EXCLUDED.command,
                                args_json = EXCLUDED.args_json,
                                env_json = EXCLUDED.env_json,
                                url = EXCLUDED.url,
                                description = EXCLUDED.description,
                                enabled = EXCLUDED.enabled,
                                discovered_tools_json = EXCLUDED.discovered_tools_json,
                                updated_at = EXCLUDED.updated_at
                            """,
                            (
                                server_id,
                                server.name,
                                server.type,
                                server.command,
                                json.dumps(server.args or [], ensure_ascii=False),
                                json.dumps(server.env or {}, ensure_ascii=False),
                                server.url,
                                server.description or "",
                                bool(server.enabled),
                                json.dumps(
                                    [tool.model_dump() for tool in (server.discoveredTools or [])],
                                    ensure_ascii=False,
                                ),
                                updated_at,
                            ),
                        )
                conn.commit()

    def get_mcp_routing_policy(
        self, intent: str, server_id: str
    ) -> McpRoutingPolicyRecord | None:
        normalized_intent = (intent or "").strip().lower()
        normalized_server_id = (server_id or "").strip().lower()
        if not normalized_server_id:
            return None
        exact = self.mcp_routing_policies.get(f"{normalized_intent}:{normalized_server_id}")
        if exact is not None:
            return exact
        return self.mcp_routing_policies.get(f"*:{normalized_server_id}")

    def record_mcp_routing_event(self, payload: dict[str, object]) -> None:
        event = dict(payload or {})
        event_id = str(event.get("id") or new_id())
        event_type = str(event.get("event_type") or "decision")
        session_id = event.get("session_id")
        prompt_hash = str(event.get("prompt_hash") or "")
        intent = str(event.get("intent") or "")
        if not prompt_hash or not intent:
            return

        selected_server_id = event.get("selected_server_id")
        latency_value = event.get("latency_ms")
        try:
            latency_ms = int(latency_value) if latency_value is not None else None
        except Exception:
            latency_ms = None
        success_value = event.get("success")
        success = bool(success_value) if success_value is not None else None

        candidate_servers = event.get("candidate_servers") or []
        scores = event.get("scores") or {}
        connected_servers = event.get("connected_servers") or []
        used_servers = event.get("used_servers") or []
        created_at = str(event.get("created_at") or now_iso())

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO runtime_mcp_routing_events (
                        id,
                        session_id,
                        event_type,
                        prompt_hash,
                        intent,
                        selected_server_id,
                        candidate_servers_json,
                        scores_json,
                        connected_servers_json,
                        used_servers_json,
                        success,
                        latency_ms,
                        created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        event_id,
                        (str(session_id).strip() if session_id is not None else None),
                        event_type,
                        prompt_hash,
                        intent,
                        str(selected_server_id) if selected_server_id is not None else None,
                        json.dumps(candidate_servers, ensure_ascii=False),
                        json.dumps(scores, ensure_ascii=False),
                        json.dumps(connected_servers, ensure_ascii=False),
                        json.dumps(used_servers, ensure_ascii=False),
                        success,
                        latency_ms,
                        created_at,
                    ),
                )
            conn.commit()

    def delete_mcp_routing_events_by_session(self, session_id: str) -> int:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return 0
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM runtime_mcp_routing_events WHERE session_id = %s",
                    (normalized_session_id,),
                )
                deleted = int(getattr(cur, "rowcount", 0) or 0)
            conn.commit()
        return deleted

    def delete_legacy_mcp_routing_events(self) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM runtime_mcp_routing_events WHERE session_id IS NULL OR session_id = ''"
                )
                deleted = int(getattr(cur, "rowcount", 0) or 0)
            conn.commit()
        return deleted

    def list_mcp_routing_events(
        self,
        *,
        limit: int = 100,
        event_type: str | None = None,
        since_iso: str | None = None,
    ) -> list[dict[str, object]]:
        normalized_limit = max(1, min(1000, int(limit or 100)))
        clauses = []
        params: list[object] = []
        if event_type:
            clauses.append("event_type = %s")
            params.append(str(event_type).strip().lower())
        if since_iso:
            clauses.append("created_at >= %s")
            params.append(str(since_iso))

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT
                id,
                session_id,
                event_type,
                prompt_hash,
                intent,
                selected_server_id,
                candidate_servers_json,
                scores_json,
                connected_servers_json,
                used_servers_json,
                success,
                latency_ms,
                created_at
            FROM runtime_mcp_routing_events
            {where_sql}
            ORDER BY created_at DESC
            LIMIT %s
        """
        params.append(normalized_limit)

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()

        events: list[dict[str, object]] = []
        for row in rows:
            try:
                events.append(
                    {
                        "id": str(row["id"]),
                        "session_id": (str(row.get("session_id") or "") or None),
                        "event_type": str(row["event_type"] or ""),
                        "prompt_hash": str(row["prompt_hash"] or ""),
                        "intent": str(row["intent"] or ""),
                        "selected_server_id": row["selected_server_id"],
                        "candidate_servers": json.loads(
                            str(row.get("candidate_servers_json") or "[]")
                        ),
                        "scores": json.loads(str(row.get("scores_json") or "{}")),
                        "connected_servers": json.loads(
                            str(row.get("connected_servers_json") or "[]")
                        ),
                        "used_servers": json.loads(
                            str(row.get("used_servers_json") or "[]")
                        ),
                        "success": row.get("success"),
                        "latency_ms": row.get("latency_ms"),
                        "createdAt": str(row.get("created_at") or ""),
                    }
                )
            except Exception:
                continue
        return events


def create_store() -> InMemoryStore | PostgresStore:
    database_url = os.getenv("BFF_DATABASE_URL", "").strip() or os.getenv("DATABASE_URL", "").strip()
    if database_url.startswith("postgresql://") or database_url.startswith("postgres://"):
        return PostgresStore(database_url=database_url)
    return InMemoryStore()


store = create_store()

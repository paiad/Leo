from __future__ import annotations

import json
import os
import sqlite3
import re
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from app.logger import logger
from bff.services.runtime.mcp_routing.runtime_logviz import render_ascii_box

try:
    from app.rag_mcp.embedder import Embedder
except Exception:  # pragma: no cover - optional dependency at runtime
    Embedder = None  # type: ignore[assignment,misc]


@dataclass(frozen=True)
class ToolIndexRow:
    server_id: str
    tool_name: str
    title: str
    text: str
    schema_json: dict[str, Any]
    score: float
    keyword_score: float
    vector_score: float


class McpToolIndexSqlite:
    _INDEX_TEXT_VERSION = "2"
    def __init__(
        self,
        *,
        sqlite_path: Path,
        embeddings_enabled: bool,
        embedding_provider: str,
        embedding_model: str,
        openai_embedding_model: str,
        query_embedding_timeout_ms: int,
        query_embedding_cache_ttl_s: int,
        query_embedding_cache_max_size: int,
    ) -> None:
        self._path = sqlite_path
        self._embeddings_enabled = embeddings_enabled and Embedder is not None
        self._embedder = (
            Embedder(
                provider=embedding_provider,
                local_model=embedding_model,
                openai_model=openai_embedding_model,
            )
            if self._embeddings_enabled
            else None
        )
        self._query_embedding_timeout_s = max(0, int(query_embedding_timeout_ms)) / 1000.0
        self._query_embedding_cache_ttl_s = max(0, int(query_embedding_cache_ttl_s))
        self._query_embedding_cache_max_size = max(1, int(query_embedding_cache_max_size))
        self._query_embedding_cache: OrderedDict[str, tuple[float, list[float]]] = OrderedDict()
        self._query_embedding_cache_lock = Lock()
        self._embed_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="mcp-tool-query-embed",
        )
        self._ensure_schema()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(tz=timezone.utc).isoformat()

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_docs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    text TEXT NOT NULL,
                    schema_json TEXT NOT NULL DEFAULT '{}',
                    embedding_json TEXT NOT NULL DEFAULT '[]',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_tool_docs_unique
                ON tool_docs(server_id, tool_name)
                """
            )
            cur.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS tool_docs_fts
                USING fts5(
                    title,
                    text,
                    content='tool_docs',
                    content_rowid='id',
                    tokenize='unicode61'
                )
                """
            )
            cur.executescript(
                """
                CREATE TRIGGER IF NOT EXISTS tool_docs_ai AFTER INSERT ON tool_docs BEGIN
                    INSERT INTO tool_docs_fts(rowid, title, text) VALUES (new.id, new.title, new.text);
                END;
                CREATE TRIGGER IF NOT EXISTS tool_docs_ad AFTER DELETE ON tool_docs BEGIN
                    INSERT INTO tool_docs_fts(tool_docs_fts, rowid, title, text) VALUES('delete', old.id, old.title, old.text);
                END;
                CREATE TRIGGER IF NOT EXISTS tool_docs_au AFTER UPDATE ON tool_docs BEGIN
                    INSERT INTO tool_docs_fts(tool_docs_fts, rowid, title, text) VALUES('delete', old.id, old.title, old.text);
                    INSERT INTO tool_docs_fts(rowid, title, text) VALUES (new.id, new.title, new.text);
                END;
                """
            )
            conn.commit()

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join((value or "").strip().lower().split())

    @staticmethod
    def _tokenize_for_fts(text: str) -> list[str]:
        raw = (text or "").strip().lower()
        if not raw:
            return []
        # Keep CJK phrases and ASCII tokens; drop punctuation like "/" that breaks MATCH.
        return re.findall(r"[\u4e00-\u9fff]+|[a-z0-9_]+", raw)

    @classmethod
    def _build_fts_query(cls, query: str) -> str:
        tokens = cls._tokenize_for_fts(query)
        if not tokens:
            return query
        cleaned = [tok.replace('"', "").strip() for tok in tokens if tok.strip()]
        if not cleaned:
            return query
        # High-recall OR query.
        return " OR ".join(cleaned)

    @staticmethod
    def _safe_json_load(raw: str, default: Any) -> Any:
        try:
            parsed = json.loads(raw)
        except Exception:
            return default
        return parsed

    @staticmethod
    def _catalog_hash(store: Any) -> str:
        servers = getattr(store, "mcp_servers", {}) or {}
        items: list[dict[str, Any]] = []
        for server_id, server in sorted(servers.items(), key=lambda it: str(it[0])):
            sid = str(server_id or "").strip().lower()
            if not sid or not getattr(server, "enabled", False):
                continue
            tools = []
            for tool in (getattr(server, "discoveredTools", []) or [])[:2000]:
                if getattr(tool, "enabled", True) is False:
                    continue
                tools.append(
                    {
                        "name": str(getattr(tool, "name", "") or "").strip().lower(),
                        "description": str(getattr(tool, "description", "") or "").strip(),
                        "inputSchema": getattr(tool, "inputSchema", {}) or {},
                    }
                )
            items.append(
                {
                    "server_id": sid,
                    "name": str(getattr(server, "name", "") or "").strip(),
                    "description": str(getattr(server, "description", "") or "").strip(),
                    "category": str(getattr(server, "category", "") or "").strip(),
                    "capabilityProfile": getattr(server, "capabilityProfile", {}) or {},
                    "tools": tools,
                }
            )
        raw = json.dumps(
            {
                "index_text_version": McpToolIndexSqlite._INDEX_TEXT_VERSION,
                "items": items,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        # Fast hash that is stable enough for change detection.
        import hashlib

        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def refresh_from_store(self, store: Any | None) -> bool:
        if store is None:
            return False
        new_hash = self._catalog_hash(store)
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT value FROM meta WHERE key='catalog_hash'")
            row = cur.fetchone()
            old_hash = str(row["value"]) if row else ""
            if old_hash == new_hash:
                return False

            logger.info("MCP tool index: refreshing sqlite index from runtime store")
            cur.execute("DELETE FROM tool_docs")

            servers = getattr(store, "mcp_servers", {}) or {}
            tool_texts: list[str] = []
            tool_rows: list[tuple[str, str, str, str, str]] = []

            for server_id, server in sorted(servers.items(), key=lambda it: str(it[0])):
                if not getattr(server, "enabled", False):
                    continue
                sid = str(server_id or "").strip().lower()
                if not sid:
                    continue
                server_name = str(getattr(server, "name", "") or "").strip()
                server_desc = str(getattr(server, "description", "") or "").strip()
                category = str(getattr(server, "category", "") or "").strip()
                capability = getattr(server, "capabilityProfile", {}) or {}
                capability_hint = self._capability_hint_text(capability)

                for tool in (getattr(server, "discoveredTools", []) or [])[:5000]:
                    if getattr(tool, "enabled", True) is False:
                        continue
                    tool_name = str(getattr(tool, "name", "") or "").strip().lower()
                    if not tool_name:
                        continue
                    tool_desc = str(getattr(tool, "description", "") or "").strip()
                    media_hint = self._playwright_media_hint_text(
                        server_id=sid,
                        tool_name=tool_name,
                        tool_desc=tool_desc,
                    )
                    input_schema = getattr(tool, "inputSchema", {}) or {}
                    schema_json = json.dumps(input_schema, ensure_ascii=False)
                    title = f"{sid}/{tool_name}"
                    schema_keys = " ".join(sorted(self._extract_schema_keys(input_schema))[:80])
                    text = "\n".join(
                        part
                        for part in [
                            title,
                            server_name,
                            server_desc,
                            f"category:{category}" if category else "",
                            capability_hint,
                            media_hint,
                            tool_desc,
                            f"schema_keys: {schema_keys}" if schema_keys else "",
                        ]
                        if part
                    ).strip()
                    tool_rows.append((sid, tool_name, title, text, schema_json))
                    tool_texts.append(text)

            embeddings: list[list[float]] = []
            if self._embeddings_enabled and self._embedder and tool_texts:
                try:
                    embeddings = self._embedder.embed_texts(tool_texts)
                except Exception as exc:
                    logger.warning(f"MCP tool index: embedding failed, disable embeddings for refresh: {exc}")
                    embeddings = []

            now_iso = self._now_iso()
            for index, (sid, tool_name, title, text, schema_json) in enumerate(tool_rows):
                embedding_json = "[]"
                if embeddings and index < len(embeddings):
                    embedding_json = json.dumps(embeddings[index], ensure_ascii=False)
                cur.execute(
                    """
                    INSERT INTO tool_docs(server_id, tool_name, title, text, schema_json, embedding_json, enabled, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (sid, tool_name, title, text, schema_json, embedding_json, now_iso),
                )

            cur.execute(
                "INSERT INTO meta(key, value) VALUES('catalog_hash', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (new_hash,),
            )
            cur.execute(
                "INSERT INTO meta(key, value) VALUES('updated_at', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (now_iso,),
            )
            conn.commit()
        return True

    def list_enabled_tool_names(self, *, server_id: str, limit: int = 30) -> list[str]:
        sid = (server_id or "").strip().lower()
        if not sid:
            return []
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                rows = cur.execute(
                    """
                    SELECT tool_name
                    FROM tool_docs
                    WHERE server_id = ? AND enabled = 1
                    ORDER BY tool_name
                    LIMIT ?
                    """,
                    (sid, max(1, int(limit))),
                ).fetchall()
                return [str(row["tool_name"] or "").strip().lower() for row in rows if row["tool_name"]]
        except Exception:
            return []

    @staticmethod
    def _capability_hint_text(capability_profile: dict[str, Any]) -> str:
        if not isinstance(capability_profile, dict) or not capability_profile:
            return ""
        contract = capability_profile.get("output_contract")
        if not isinstance(contract, dict):
            return ""
        keywords = contract.get("keywords") or {}
        kw_zh = keywords.get("zh") if isinstance(keywords, dict) else None
        kw_en = keywords.get("en") if isinstance(keywords, dict) else None
        parts: list[str] = []
        if isinstance(kw_zh, list):
            parts.extend(str(x).strip() for x in kw_zh[:40] if str(x).strip())
        if isinstance(kw_en, list):
            parts.extend(str(x).strip() for x in kw_en[:40] if str(x).strip())
        examples = contract.get("example_queries")
        if isinstance(examples, list):
            parts.extend(str(x).strip() for x in examples[:20] if str(x).strip())
        if not parts:
            return ""
        return "capability_hints: " + " ".join(parts)

    @staticmethod
    def _playwright_media_hint_text(
        *,
        server_id: str,
        tool_name: str,
        tool_desc: str,
    ) -> str:
        if (server_id or "").strip().lower() != "playwright":
            return ""
        lowered_name = (tool_name or "").strip().lower()
        lowered_desc = (tool_desc or "").strip().lower()
        if not (lowered_name.startswith("browser_") or "browser" in lowered_desc):
            return ""
        return (
            "media_hints: 看 想看 观看 播放 继续播放 暂停 视频 节目 综艺 电视剧 电影 "
            "短视频 B站 bilibili youtube watch play video episode show"
        )

    @staticmethod
    def _extract_schema_keys(schema: dict[str, Any]) -> set[str]:
        keys: set[str] = set()
        stack = [schema]
        while stack:
            item = stack.pop()
            if not isinstance(item, dict):
                continue
            props = item.get("properties")
            if isinstance(props, dict):
                for k, v in props.items():
                    if isinstance(k, str) and k.strip():
                        keys.add(k.strip().lower())
                    if isinstance(v, dict):
                        stack.append(v)
            for v in item.values():
                if isinstance(v, dict):
                    stack.append(v)
                elif isinstance(v, list):
                    for child in v:
                        if isinstance(child, dict):
                            stack.append(child)
        return keys

    @staticmethod
    def _dot(a: list[float], b: list[float]) -> float:
        return float(sum(x * y for x, y in zip(a, b, strict=False)))

    def _get_cached_query_embedding(self, normalized_query: str) -> list[float] | None:
        if self._query_embedding_cache_ttl_s <= 0:
            return None
        now = time.time()
        with self._query_embedding_cache_lock:
            item = self._query_embedding_cache.get(normalized_query)
            if not item:
                return None
            cached_at, vector = item
            if (now - cached_at) > float(self._query_embedding_cache_ttl_s):
                self._query_embedding_cache.pop(normalized_query, None)
                return None
            self._query_embedding_cache.move_to_end(normalized_query)
            return list(vector)

    def _set_cached_query_embedding(self, normalized_query: str, vector: list[float]) -> None:
        if self._query_embedding_cache_ttl_s <= 0:
            return
        now = time.time()
        with self._query_embedding_cache_lock:
            self._query_embedding_cache[normalized_query] = (now, list(vector))
            self._query_embedding_cache.move_to_end(normalized_query)
            while len(self._query_embedding_cache) > self._query_embedding_cache_max_size:
                self._query_embedding_cache.popitem(last=False)

    def _embed_query_with_timeout(self, normalized_query: str) -> tuple[list[float] | None, int, str]:
        if not self._embedder:
            return None, 0, "disabled"

        started = time.perf_counter()
        timeout_s = self._query_embedding_timeout_s
        if timeout_s <= 0:
            try:
                vectors = self._embedder.embed_texts([normalized_query])
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                return (vectors[0] if vectors else None), elapsed_ms, "ok"
            except Exception:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                return None, elapsed_ms, "error"

        future = self._embed_executor.submit(self._embedder.embed_texts, [normalized_query])
        try:
            vectors = future.result(timeout=timeout_s)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return (vectors[0] if vectors else None), elapsed_ms, "ok"
        except FutureTimeoutError:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            future.cancel()
            return None, elapsed_ms, "timeout"
        except Exception:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return None, elapsed_ms, "error"

    def warmup_query_embedding(
        self,
        query: str = "打开网页并点击播放视频",
        *,
        timeout_ms: int | None = None,
    ) -> dict[str, object]:
        normalized_query = self._normalize_text(query)
        if not normalized_query:
            return {"ok": False, "mode": "empty_query", "elapsed_ms": 0}
        if not (self._embeddings_enabled and self._embedder):
            return {"ok": False, "mode": "disabled", "elapsed_ms": 0}

        cached = self._get_cached_query_embedding(normalized_query)
        if cached is not None:
            return {"ok": True, "mode": "cache_hit", "elapsed_ms": 0}

        if timeout_ms is not None and int(timeout_ms) > 0 and self._embedder is not None:
            started = time.perf_counter()
            future = self._embed_executor.submit(self._embedder.embed_texts, [normalized_query])
            try:
                vectors = future.result(timeout=max(1, int(timeout_ms)) / 1000.0)
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                query_vec = vectors[0] if vectors else None
                mode = "ok"
            except FutureTimeoutError:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                future.cancel()
                query_vec = None
                mode = "timeout"
            except Exception:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                query_vec = None
                mode = "error"
        else:
            query_vec, elapsed_ms, mode = self._embed_query_with_timeout(normalized_query)
        if query_vec is not None:
            self._set_cached_query_embedding(normalized_query, query_vec)
        return {
            "ok": bool(query_vec is not None),
            "mode": mode,
            "elapsed_ms": int(elapsed_ms),
        }

    def search(
        self,
        *,
        query: str,
        topk: int,
        fts_topk: int,
        w_keyword: float,
        w_vector: float,
    ) -> list[ToolIndexRow]:
        started = time.perf_counter()
        normalized_query = self._normalize_text(query)
        if not normalized_query or topk <= 0:
            return []

        db_fetch_ms = 0
        fts_ms = 0
        docs_count = 0
        hits_count = 0
        with self._connect() as conn:
            cur = conn.cursor()
            db_started = time.perf_counter()
            cur.execute(
                """
                SELECT server_id, tool_name, title, text, schema_json, embedding_json
                FROM tool_docs
                WHERE enabled = 1
                """
            )
            all_docs = cur.fetchall()
            db_fetch_ms = int((time.perf_counter() - db_started) * 1000)
            docs_count = len(all_docs)

            kw_rank: dict[tuple[str, str], int] = {}
            try:
                fts_query = self._build_fts_query(normalized_query)
                fts_started = time.perf_counter()
                cur.execute(
                    """
                    SELECT
                        d.server_id,
                        d.tool_name,
                        (-bm25(tool_docs_fts)) AS kw_score
                    FROM tool_docs_fts
                    JOIN tool_docs d ON d.id = tool_docs_fts.rowid
                    WHERE tool_docs_fts MATCH ?
                      AND d.enabled = 1
                    ORDER BY kw_score DESC
                    LIMIT ?
                    """,
                    (fts_query, max(1, int(fts_topk or 80))),
                )
                hits = cur.fetchall()
                fts_ms = int((time.perf_counter() - fts_started) * 1000)
                hits_count = len(hits)
                for rank, row in enumerate(hits, start=1):
                    sid = str(row["server_id"] or "").strip().lower()
                    name = str(row["tool_name"] or "").strip().lower()
                    if sid and name:
                        kw_rank[(sid, name)] = rank
            except Exception:
                kw_rank = {}

        embed_ms = 0
        query_vec: list[float] | None = None
        embed_mode = "disabled"
        if self._embeddings_enabled and self._embedder:
            if w_vector <= 0:
                embed_mode = "skipped_w_vector=0"
            else:
                cached = self._get_cached_query_embedding(normalized_query)
                if cached is not None:
                    query_vec = cached
                    embed_mode = "cache_hit"
                else:
                    query_vec, embed_ms, embed_mode = self._embed_query_with_timeout(normalized_query)
                    if query_vec is not None:
                        self._set_cached_query_embedding(normalized_query, query_vec)

        score_started = time.perf_counter()
        rows: list[ToolIndexRow] = []
        for row in all_docs:
            sid = str(row["server_id"] or "").strip().lower()
            tool_name = str(row["tool_name"] or "").strip().lower()
            title = str(row["title"] or "").strip()
            text = str(row["text"] or "").strip()
            schema_json = self._safe_json_load(str(row["schema_json"] or "{}"), {})
            keyword_score = 0.0
            rank = kw_rank.get((sid, tool_name))
            if rank is not None:
                keyword_score = 1.0 / float(rank)

            vector_score = 0.0
            if query_vec is not None:
                emb = self._safe_json_load(str(row["embedding_json"] or "[]"), [])
                if isinstance(emb, list) and emb and all(isinstance(x, (int, float)) for x in emb):
                    vector_score = self._dot(query_vec, [float(x) for x in emb])

            score = (w_keyword * keyword_score) + (w_vector * vector_score)
            rows.append(
                ToolIndexRow(
                    server_id=sid,
                    tool_name=tool_name,
                    title=title,
                    text=text,
                    schema_json=schema_json if isinstance(schema_json, dict) else {},
                    score=float(score),
                    keyword_score=float(keyword_score),
                    vector_score=float(vector_score),
                )
            )

        score_ms = int((time.perf_counter() - score_started) * 1000)
        sort_started = time.perf_counter()
        rows.sort(key=lambda x: (-x.score, x.server_id, x.tool_name))
        sort_ms = int((time.perf_counter() - sort_started) * 1000)
        result = rows[: max(1, int(topk))]

        total_ms = int((time.perf_counter() - started) * 1000)
        if (os.getenv("BFF_RUNTIME_TIMING_LOG_ENABLED", "1") or "").strip().lower() in {"1", "true", "yes", "on"}:
            threshold_ms = int(os.getenv("BFF_MCP_TOOL_INDEX_SEARCH_TIMING_THRESHOLD_MS", "300") or 300)
            if total_ms >= threshold_ms:
                lines = [
                    f"total_ms: {total_ms}",
                    f"db_fetch_ms: {db_fetch_ms} (docs={docs_count})",
                    f"fts_ms: {fts_ms} (hits={hits_count}, fts_topk={int(fts_topk or 0)})",
                    (
                        f"embed_ms: {embed_ms} "
                        f"(embeddings_enabled={bool(self._embeddings_enabled and self._embedder)}, mode={embed_mode})"
                    ),
                    f"score_ms: {score_ms}",
                    f"sort_ms: {sort_ms}",
                    f"topk: {int(topk)}",
                ]
                logger.info("\n" + render_ascii_box("MCP TIMING (INDEX SEARCH)", lines))

        return result


def default_mcp_tool_index_sqlite_path() -> Path:
    root = Path(os.getenv("OPENMANUS_ROOT", "")).resolve() if os.getenv("OPENMANUS_ROOT") else Path.cwd()
    return root / "workspace" / "mcp" / "tool_index.sqlite3"


def create_mcp_tool_index_from_env() -> McpToolIndexSqlite:
    # Ensure a stable HuggingFace/SentenceTransformer cache to avoid partial downloads
    # in the user's home directory breaking embedding generation.
    root = Path(os.getenv("OPENMANUS_ROOT", "")).resolve() if os.getenv("OPENMANUS_ROOT") else Path.cwd()
    default_cache_dir = root / "workspace" / "mcp" / "hf-cache"
    cache_dir = Path(
        os.getenv(
            "BFF_MCP_TOOL_HF_CACHE_DIR",
            os.getenv("RAG_HF_CACHE_DIR", str(default_cache_dir)),
        )
    ).expanduser()
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        cache_dir = default_cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

    os.environ["BFF_MCP_TOOL_HF_CACHE_DIR"] = str(cache_dir)
    # Force these cache locations for the MCP tool embedding pipeline to avoid
    # accidentally reusing a partially downloaded/corrupted home cache.
    os.environ["HF_HOME"] = str(cache_dir)
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(cache_dir / "hub")
    os.environ["TRANSFORMERS_CACHE"] = str(cache_dir / "transformers")
    os.environ["SENTENCE_TRANSFORMERS_HOME"] = str(cache_dir / "sentence_transformers")

    sqlite_path = Path(
        os.getenv("BFF_MCP_TOOL_INDEX_SQLITE_PATH", str(default_mcp_tool_index_sqlite_path()))
    )
    embeddings_enabled = (os.getenv("BFF_MCP_TOOL_INDEX_EMBEDDINGS_ENABLED", "1") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    provider = os.getenv("BFF_MCP_TOOL_EMBEDDING_PROVIDER", os.getenv("RAG_EMBEDDING_PROVIDER", "local"))
    local_model = os.getenv("BFF_MCP_TOOL_EMBEDDING_MODEL", os.getenv("RAG_EMBEDDING_MODEL", "BAAI/bge-m3"))
    openai_model = os.getenv(
        "BFF_MCP_TOOL_OPENAI_EMBEDDING_MODEL",
        os.getenv("RAG_OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
    )
    query_embedding_timeout_ms = int(os.getenv("BFF_MCP_TOOL_QUERY_EMBED_TIMEOUT_MS", "2000") or 2000)
    query_embedding_cache_ttl_s = int(os.getenv("BFF_MCP_TOOL_QUERY_EMBED_CACHE_TTL_S", "900") or 900)
    query_embedding_cache_max_size = int(os.getenv("BFF_MCP_TOOL_QUERY_EMBED_CACHE_MAX_SIZE", "1024") or 1024)
    return McpToolIndexSqlite(
        sqlite_path=sqlite_path,
        embeddings_enabled=embeddings_enabled,
        embedding_provider=str(provider).strip().lower(),
        embedding_model=str(local_model).strip(),
        openai_embedding_model=str(openai_model).strip(),
        query_embedding_timeout_ms=query_embedding_timeout_ms,
        query_embedding_cache_ttl_s=query_embedding_cache_ttl_s,
        query_embedding_cache_max_size=query_embedding_cache_max_size,
    )

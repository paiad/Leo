from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from psycopg import connect as pg_connect
from psycopg.rows import dict_row


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class SourceRecord:
    source_id: int
    path: str
    checksum: str
    version: int


@dataclass(slots=True)
class ChunkRecord:
    chunk_id: str
    source_id: int
    source_path: str
    chunk_index: int
    text: str
    token_count: int
    version: int
    metadata: dict[str, Any]


@dataclass(slots=True)
class SourceSummary:
    source_id: int
    path: str
    version: int
    checksum: str
    last_indexed_at: str
    chunk_count: int


class RagMetadataStore:
    def __init__(self, sqlite_path: Path, database_url: str | None = None):
        self._database_url = (database_url or "").strip()
        self._is_postgres = self._database_url.startswith(
            ("postgresql://", "postgres://")
        )
        self._path = sqlite_path
        if not self._is_postgres:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect_sqlite(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _connect_postgres(self):
        return pg_connect(self._database_url, row_factory=dict_row)

    def _sql(self, query: str) -> str:
        if self._is_postgres:
            return query.replace("?", "%s")
        return query

    @staticmethod
    def _row_get(row: sqlite3.Row | dict[str, Any], key: str) -> Any:
        if isinstance(row, dict):
            return row[key]
        return row[key]

    def _execute(
        self,
        conn,
        query: str,
        params: tuple[Any, ...] = (),
    ):
        return conn.execute(self._sql(query), params)

    def _init_schema(self) -> None:
        if self._is_postgres:
            with self._connect_postgres() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS sources (
                            id BIGSERIAL PRIMARY KEY,
                            path TEXT NOT NULL UNIQUE,
                            checksum TEXT NOT NULL,
                            version INTEGER NOT NULL DEFAULT 1,
                            updated_at TEXT NOT NULL,
                            last_indexed_at TEXT NOT NULL
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS chunks (
                            chunk_id TEXT PRIMARY KEY,
                            source_id BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                            version INTEGER NOT NULL,
                            chunk_index INTEGER NOT NULL,
                            text TEXT NOT NULL,
                            token_count INTEGER NOT NULL,
                            metadata_json JSONB NOT NULL
                        )
                        """
                    )
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_id)"
                    )
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_chunks_source_version ON chunks(source_id, version)"
                    )
                conn.commit()
            return

        with self._connect_sqlite() as conn:
            self._execute(
                conn,
                """
                CREATE TABLE IF NOT EXISTS sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE,
                    checksum TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL,
                    last_indexed_at TEXT NOT NULL
                )
                """,
            )
            self._execute(
                conn,
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    source_id INTEGER NOT NULL,
                    version INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    token_count INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL,
                    FOREIGN KEY(source_id) REFERENCES sources(id)
                )
                """,
            )
            self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_id)")
            self._execute(
                conn,
                "CREATE INDEX IF NOT EXISTS idx_chunks_source_version ON chunks(source_id, version)",
            )

    def get_source(self, path: str) -> SourceRecord | None:
        conn_ctx = self._connect_postgres if self._is_postgres else self._connect_sqlite
        with conn_ctx() as conn:
            if self._is_postgres:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, path, checksum, version FROM sources WHERE path = %s",
                        (path,),
                    )
                    row = cur.fetchone()
            else:
                row = self._execute(
                    conn,
                    "SELECT id, path, checksum, version FROM sources WHERE path = ?",
                    (path,),
                ).fetchone()
        if row is None:
            return None
        return SourceRecord(
            source_id=int(self._row_get(row, "id")),
            path=str(self._row_get(row, "path")),
            checksum=str(self._row_get(row, "checksum")),
            version=int(self._row_get(row, "version")),
        )

    def replace_chunks(
        self,
        source_path: str,
        checksum: str,
        chunks: list[dict[str, Any]],
    ) -> SourceRecord:
        now = _now_iso()
        conn_ctx = self._connect_postgres if self._is_postgres else self._connect_sqlite
        with conn_ctx() as conn:
            if self._is_postgres:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, checksum, version FROM sources WHERE path = %s",
                        (source_path,),
                    )
                    row = cur.fetchone()
            else:
                row = self._execute(
                    conn,
                    "SELECT id, checksum, version FROM sources WHERE path = ?",
                    (source_path,),
                ).fetchone()

            if row is None:
                version = 1
                if self._is_postgres:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO sources(path, checksum, version, updated_at, last_indexed_at)
                            VALUES(%s, %s, %s, %s, %s)
                            RETURNING id
                            """,
                            (source_path, checksum, version, now, now),
                        )
                        inserted = cur.fetchone()
                    if not inserted:
                        raise RuntimeError("Failed to insert source row into PostgreSQL.")
                    source_id = int(inserted["id"])
                else:
                    cur = self._execute(
                        conn,
                        """
                        INSERT INTO sources(path, checksum, version, updated_at, last_indexed_at)
                        VALUES(?, ?, ?, ?, ?)
                        """,
                        (source_path, checksum, version, now, now),
                    )
                    source_id = int(cur.lastrowid)
            else:
                source_id = int(self._row_get(row, "id"))
                version = int(self._row_get(row, "version")) + 1
                if self._is_postgres:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE sources
                            SET checksum = %s, version = %s, updated_at = %s, last_indexed_at = %s
                            WHERE id = %s
                            """,
                            (checksum, version, now, now, source_id),
                        )
                        cur.execute("DELETE FROM chunks WHERE source_id = %s", (source_id,))
                else:
                    self._execute(
                        conn,
                        """
                        UPDATE sources
                        SET checksum = ?, version = ?, updated_at = ?, last_indexed_at = ?
                        WHERE id = ?
                        """,
                        (checksum, version, now, now, source_id),
                    )
                    self._execute(conn, "DELETE FROM chunks WHERE source_id = ?", (source_id,))

            rows_to_insert = [
                (
                    item["chunk_id"],
                    source_id,
                    version,
                    item["chunk_index"],
                    item["text"],
                    item["token_count"],
                    json.dumps(item["metadata"], ensure_ascii=False),
                )
                for item in chunks
            ]
            if self._is_postgres:
                with conn.cursor() as cur:
                    cur.executemany(
                        """
                        INSERT INTO chunks(chunk_id, source_id, version, chunk_index, text, token_count, metadata_json)
                        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        rows_to_insert,
                    )
                conn.commit()
            else:
                conn.executemany(
                    self._sql(
                        """
                        INSERT INTO chunks(chunk_id, source_id, version, chunk_index, text, token_count, metadata_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """
                    ),
                    rows_to_insert,
                )

        return SourceRecord(
            source_id=source_id,
            path=source_path,
            checksum=checksum,
            version=version,
        )

    def list_chunks(self) -> list[ChunkRecord]:
        conn_ctx = self._connect_postgres if self._is_postgres else self._connect_sqlite
        with conn_ctx() as conn:
            if self._is_postgres:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT c.chunk_id, c.source_id, c.chunk_index, c.text, c.token_count, c.version, c.metadata_json, s.path
                        FROM chunks c
                        JOIN sources s ON c.source_id = s.id
                        ORDER BY c.source_id, c.chunk_index
                        """
                    )
                    rows = cur.fetchall()
            else:
                rows = self._execute(
                    conn,
                    """
                    SELECT c.chunk_id, c.source_id, c.chunk_index, c.text, c.token_count, c.version, c.metadata_json, s.path
                    FROM chunks c
                    JOIN sources s ON c.source_id = s.id
                    ORDER BY c.source_id, c.chunk_index
                    """,
                ).fetchall()
        records: list[ChunkRecord] = []
        for row in rows:
            metadata_raw = self._row_get(row, "metadata_json")
            records.append(
                ChunkRecord(
                    chunk_id=str(self._row_get(row, "chunk_id")),
                    source_id=int(self._row_get(row, "source_id")),
                    source_path=str(self._row_get(row, "path")),
                    chunk_index=int(self._row_get(row, "chunk_index")),
                    text=str(self._row_get(row, "text")),
                    token_count=int(self._row_get(row, "token_count")),
                    version=int(self._row_get(row, "version")),
                    metadata=metadata_raw
                    if isinstance(metadata_raw, dict)
                    else json.loads(str(metadata_raw)),
                )
            )
        return records

    def list_chunks_by_ids(self, chunk_ids: list[str]) -> list[ChunkRecord]:
        if not chunk_ids:
            return []
        placeholders = ",".join("?" for _ in chunk_ids)
        conn_ctx = self._connect_postgres if self._is_postgres else self._connect_sqlite
        with conn_ctx() as conn:
            if self._is_postgres:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        SELECT c.chunk_id, c.source_id, c.chunk_index, c.text, c.token_count, c.version, c.metadata_json, s.path
                        FROM chunks c
                        JOIN sources s ON c.source_id = s.id
                        WHERE c.chunk_id IN ({self._sql(placeholders)})
                        """,
                        chunk_ids,
                    )
                    rows = cur.fetchall()
            else:
                rows = self._execute(
                    conn,
                    f"""
                    SELECT c.chunk_id, c.source_id, c.chunk_index, c.text, c.token_count, c.version, c.metadata_json, s.path
                    FROM chunks c
                    JOIN sources s ON c.source_id = s.id
                    WHERE c.chunk_id IN ({placeholders})
                    """,
                    tuple(chunk_ids),
                ).fetchall()
        output: list[ChunkRecord] = []
        for row in rows:
            metadata_raw = self._row_get(row, "metadata_json")
            output.append(
                ChunkRecord(
                    chunk_id=str(self._row_get(row, "chunk_id")),
                    source_id=int(self._row_get(row, "source_id")),
                    source_path=str(self._row_get(row, "path")),
                    chunk_index=int(self._row_get(row, "chunk_index")),
                    text=str(self._row_get(row, "text")),
                    token_count=int(self._row_get(row, "token_count")),
                    version=int(self._row_get(row, "version")),
                    metadata=metadata_raw
                    if isinstance(metadata_raw, dict)
                    else json.loads(str(metadata_raw)),
                )
            )
        return output

    def stats(self) -> dict[str, Any]:
        conn_ctx = self._connect_postgres if self._is_postgres else self._connect_sqlite
        with conn_ctx() as conn:
            if self._is_postgres:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) AS c FROM sources")
                    sources_count = cur.fetchone()["c"]
                    cur.execute("SELECT COUNT(*) AS c FROM chunks")
                    chunks_count = cur.fetchone()["c"]
                    cur.execute("SELECT MAX(last_indexed_at) AS ts FROM sources")
                    last_indexed = cur.fetchone()["ts"]
            else:
                sources_count = self._execute(
                    conn, "SELECT COUNT(*) AS c FROM sources"
                ).fetchone()["c"]
                chunks_count = self._execute(
                    conn, "SELECT COUNT(*) AS c FROM chunks"
                ).fetchone()["c"]
                last_indexed = self._execute(
                    conn, "SELECT MAX(last_indexed_at) AS ts FROM sources"
                ).fetchone()["ts"]
        return {
            "sources": int(sources_count),
            "chunks": int(chunks_count),
            "last_indexed_at": last_indexed,
        }

    def list_sources(self) -> list[SourceSummary]:
        conn_ctx = self._connect_postgres if self._is_postgres else self._connect_sqlite
        with conn_ctx() as conn:
            if self._is_postgres:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT
                            s.id,
                            s.path,
                            s.version,
                            s.checksum,
                            s.last_indexed_at,
                            COUNT(c.chunk_id) AS chunk_count
                        FROM sources s
                        LEFT JOIN chunks c ON c.source_id = s.id
                        GROUP BY s.id, s.path, s.version, s.checksum, s.last_indexed_at
                        ORDER BY s.last_indexed_at DESC, s.id DESC
                        """
                    )
                    rows = cur.fetchall()
            else:
                rows = self._execute(
                    conn,
                    """
                    SELECT
                        s.id,
                        s.path,
                        s.version,
                        s.checksum,
                        s.last_indexed_at,
                        COUNT(c.chunk_id) AS chunk_count
                    FROM sources s
                    LEFT JOIN chunks c ON c.source_id = s.id
                    GROUP BY s.id, s.path, s.version, s.checksum, s.last_indexed_at
                    ORDER BY s.last_indexed_at DESC, s.id DESC
                    """,
                ).fetchall()
        return [
            SourceSummary(
                source_id=int(self._row_get(row, "id")),
                path=str(self._row_get(row, "path")),
                version=int(self._row_get(row, "version")),
                checksum=str(self._row_get(row, "checksum")),
                last_indexed_at=str(self._row_get(row, "last_indexed_at")),
                chunk_count=int(self._row_get(row, "chunk_count")),
            )
            for row in rows
        ]

    def delete_sources_by_paths(self, paths: list[str]) -> list[SourceSummary]:
        if not paths:
            return []
        normalized = [path for path in paths if path]
        if not normalized:
            return []
        placeholders = ",".join("?" for _ in normalized)
        conn_ctx = self._connect_postgres if self._is_postgres else self._connect_sqlite
        with conn_ctx() as conn:
            if self._is_postgres:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        SELECT id, path, version, checksum, last_indexed_at
                        FROM sources
                        WHERE path IN ({self._sql(placeholders)})
                        """,
                        normalized,
                    )
                    rows = cur.fetchall()
            else:
                rows = self._execute(
                    conn,
                    f"""
                    SELECT id, path, version, checksum, last_indexed_at
                    FROM sources
                    WHERE path IN ({placeholders})
                    """,
                    tuple(normalized),
                ).fetchall()
            deleted: list[SourceSummary] = []
            for row in rows:
                source_id = int(self._row_get(row, "id"))
                if self._is_postgres:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT COUNT(*) AS c FROM chunks WHERE source_id = %s",
                            (source_id,),
                        )
                        chunk_count = int(cur.fetchone()["c"])
                        cur.execute("DELETE FROM chunks WHERE source_id = %s", (source_id,))
                        cur.execute("DELETE FROM sources WHERE id = %s", (source_id,))
                else:
                    chunk_count = int(
                        self._execute(
                            conn,
                            "SELECT COUNT(*) AS c FROM chunks WHERE source_id = ?",
                            (source_id,),
                        ).fetchone()["c"]
                    )
                    self._execute(conn, "DELETE FROM chunks WHERE source_id = ?", (source_id,))
                    self._execute(conn, "DELETE FROM sources WHERE id = ?", (source_id,))
                deleted.append(
                    SourceSummary(
                        source_id=source_id,
                        path=str(self._row_get(row, "path")),
                        version=int(self._row_get(row, "version")),
                        checksum=str(self._row_get(row, "checksum")),
                        last_indexed_at=str(self._row_get(row, "last_indexed_at")),
                        chunk_count=chunk_count,
                    )
                )
            if self._is_postgres:
                conn.commit()
        return deleted

    def delete_all_sources(self) -> list[SourceSummary]:
        all_sources = self.list_sources()
        if not all_sources:
            return []
        conn_ctx = self._connect_postgres if self._is_postgres else self._connect_sqlite
        with conn_ctx() as conn:
            if self._is_postgres:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM chunks")
                    cur.execute("DELETE FROM sources")
                conn.commit()
            else:
                self._execute(conn, "DELETE FROM chunks")
                self._execute(conn, "DELETE FROM sources")
        return all_sources

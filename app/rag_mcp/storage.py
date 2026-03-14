from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
    def __init__(self, sqlite_path: Path):
        self._path = sqlite_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE,
                    checksum TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL,
                    last_indexed_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
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
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_source_version ON chunks(source_id, version)")

    def get_source(self, path: str) -> SourceRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, path, checksum, version FROM sources WHERE path = ?",
                (path,),
            ).fetchone()
        if row is None:
            return None
        return SourceRecord(
            source_id=row["id"],
            path=row["path"],
            checksum=row["checksum"],
            version=row["version"],
        )

    def replace_chunks(
        self,
        source_path: str,
        checksum: str,
        chunks: list[dict[str, Any]],
    ) -> SourceRecord:
        now = _now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, checksum, version FROM sources WHERE path = ?",
                (source_path,),
            ).fetchone()

            if row is None:
                version = 1
                cur = conn.execute(
                    """
                    INSERT INTO sources(path, checksum, version, updated_at, last_indexed_at)
                    VALUES(?, ?, ?, ?, ?)
                    """,
                    (source_path, checksum, version, now, now),
                )
                source_id = int(cur.lastrowid)
            else:
                source_id = int(row["id"])
                version = int(row["version"]) + 1
                conn.execute(
                    """
                    UPDATE sources
                    SET checksum = ?, version = ?, updated_at = ?, last_indexed_at = ?
                    WHERE id = ?
                    """,
                    (checksum, version, now, now, source_id),
                )
                conn.execute("DELETE FROM chunks WHERE source_id = ?", (source_id,))

            conn.executemany(
                """
                INSERT INTO chunks(chunk_id, source_id, version, chunk_index, text, token_count, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
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
                ],
            )

        return SourceRecord(
            source_id=source_id,
            path=source_path,
            checksum=checksum,
            version=version,
        )

    def list_chunks(self) -> list[ChunkRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT c.chunk_id, c.source_id, c.chunk_index, c.text, c.token_count, c.version, c.metadata_json, s.path
                FROM chunks c
                JOIN sources s ON c.source_id = s.id
                ORDER BY c.source_id, c.chunk_index
                """
            ).fetchall()
        records: list[ChunkRecord] = []
        for row in rows:
            records.append(
                ChunkRecord(
                    chunk_id=row["chunk_id"],
                    source_id=row["source_id"],
                    source_path=row["path"],
                    chunk_index=row["chunk_index"],
                    text=row["text"],
                    token_count=row["token_count"],
                    version=row["version"],
                    metadata=json.loads(row["metadata_json"]),
                )
            )
        return records

    def list_chunks_by_ids(self, chunk_ids: list[str]) -> list[ChunkRecord]:
        if not chunk_ids:
            return []
        placeholders = ",".join("?" for _ in chunk_ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT c.chunk_id, c.source_id, c.chunk_index, c.text, c.token_count, c.version, c.metadata_json, s.path
                FROM chunks c
                JOIN sources s ON c.source_id = s.id
                WHERE c.chunk_id IN ({placeholders})
                """,
                chunk_ids,
            ).fetchall()
        output: list[ChunkRecord] = []
        for row in rows:
            output.append(
                ChunkRecord(
                    chunk_id=row["chunk_id"],
                    source_id=row["source_id"],
                    source_path=row["path"],
                    chunk_index=row["chunk_index"],
                    text=row["text"],
                    token_count=row["token_count"],
                    version=row["version"],
                    metadata=json.loads(row["metadata_json"]),
                )
            )
        return output

    def stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            sources_count = conn.execute("SELECT COUNT(*) AS c FROM sources").fetchone()["c"]
            chunks_count = conn.execute("SELECT COUNT(*) AS c FROM chunks").fetchone()["c"]
            last_indexed = conn.execute(
                "SELECT MAX(last_indexed_at) AS ts FROM sources"
            ).fetchone()["ts"]
        return {
            "sources": int(sources_count),
            "chunks": int(chunks_count),
            "last_indexed_at": last_indexed,
        }

    def list_sources(self) -> list[SourceSummary]:
        with self._connect() as conn:
            rows = conn.execute(
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
            ).fetchall()
        return [
            SourceSummary(
                source_id=int(row["id"]),
                path=str(row["path"]),
                version=int(row["version"]),
                checksum=str(row["checksum"]),
                last_indexed_at=str(row["last_indexed_at"]),
                chunk_count=int(row["chunk_count"]),
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
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, path, version, checksum, last_indexed_at
                FROM sources
                WHERE path IN ({placeholders})
                """,
                normalized,
            ).fetchall()
            deleted: list[SourceSummary] = []
            for row in rows:
                source_id = int(row["id"])
                chunk_count = int(
                    conn.execute(
                        "SELECT COUNT(*) AS c FROM chunks WHERE source_id = ?",
                        (source_id,),
                    ).fetchone()["c"]
                )
                conn.execute("DELETE FROM chunks WHERE source_id = ?", (source_id,))
                conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
                deleted.append(
                    SourceSummary(
                        source_id=source_id,
                        path=str(row["path"]),
                        version=int(row["version"]),
                        checksum=str(row["checksum"]),
                        last_indexed_at=str(row["last_indexed_at"]),
                        chunk_count=chunk_count,
                    )
                )
        return deleted

    def delete_all_sources(self) -> list[SourceSummary]:
        all_sources = self.list_sources()
        if not all_sources:
            return []
        with self._connect() as conn:
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM sources")
        return all_sources

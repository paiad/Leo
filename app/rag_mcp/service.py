from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import uuid
from pathlib import Path
from typing import Any

from app.rag_mcp.chunking import TextChunk, chunk_text_by_tokens
from app.rag_mcp.config import RagSettings
from app.rag_mcp.embedder import Embedder, Reranker
from app.rag_mcp.parsers import extract_text
from app.rag_mcp.storage import RagMetadataStore
from app.rag_mcp.vector_store import ChromaVectorStore, QdrantVectorStore


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _tokenize_for_bm25(text: str) -> list[str]:
    if not text:
        return []
    raw = text.lower()
    parts = re.findall(r"[\u4e00-\u9fff]+|[a-z0-9_]+", raw)
    tokens: list[str] = []
    for part in parts:
        # CJK tokenization: keep whole phrase + character bi-grams for fuzzy Chinese recall.
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            tokens.append(part)
            if len(part) > 1:
                tokens.extend(part[i : i + 2] for i in range(len(part) - 1))
            continue
        tokens.append(part)
    return tokens


def _contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _normalize_query_for_retrieval(query: str) -> str:
    normalized = (query or "").strip()
    if not normalized:
        return normalized
    normalized = re.sub(r"[？?。！!]+$", "", normalized)
    patterns = [
        r"^(什么是|请解释|解释一下|请说明|说明一下)",
        r"(是什么意思|的意思|含义|定义|概念)$",
    ]
    for pattern in patterns:
        normalized = re.sub(pattern, "", normalized).strip()
    return normalized or (query or "").strip()


def _rrf(scores_by_rank: dict[str, dict[str, int]], k: int = 60) -> dict[str, float]:
    merged: dict[str, float] = {}
    for rank_map in scores_by_rank.values():
        for chunk_id, rank in rank_map.items():
            merged[chunk_id] = merged.get(chunk_id, 0.0) + (1.0 / (k + rank))
    return merged


class RagService:
    def __init__(self, settings: RagSettings):
        self.settings = settings
        self.store = RagMetadataStore(
            sqlite_path=settings.sqlite_path,
            database_url=settings.database_url,
        )
        if settings.vector_backend == "chroma":
            self.vector_store = ChromaVectorStore(
                persist_path=settings.chroma_path,
                collection_name=settings.vector_collection,
            )
        elif settings.vector_backend == "qdrant":
            self.vector_store = QdrantVectorStore(
                url=settings.qdrant_url,
                collection_name=settings.vector_collection,
                api_key=settings.qdrant_api_key,
            )
        else:
            raise ValueError("RAG_VECTOR_BACKEND must be 'chroma' or 'qdrant'.")
        self.embedder = Embedder(
            provider=settings.embedding_provider,
            local_model=settings.embedding_model,
            openai_model=settings.openai_embedding_model,
        )
        self.reranker = Reranker(
            enabled=settings.rerank_enabled,
            model_name=settings.reranker_model,
        )

    def _collect_files(self, paths: list[str]) -> list[Path]:
        files: list[Path] = []
        patterns = self.settings.indexing_glob
        for raw in paths:
            p = Path(raw).expanduser().resolve()
            if p.is_file():
                files.append(p)
                continue
            if p.is_dir():
                for child in p.rglob("*"):
                    if not child.is_file():
                        continue
                    if patterns and not any(
                        fnmatch.fnmatch(child.name.lower(), pattern) for pattern in patterns
                    ):
                        continue
                    files.append(child)
        uniq = sorted(set(files))
        return uniq

    @staticmethod
    def _build_chunk_rows(
        source_path: str,
        chunks: list[TextChunk],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for chunk in chunks:
            cid = str(uuid.uuid4())
            metadata = {
                "source_path": source_path,
                "chunk_index": chunk.index,
            }
            rows.append(
                {
                    "chunk_id": cid,
                    "chunk_index": chunk.index,
                    "text": chunk.text,
                    "token_count": chunk.token_count,
                    "metadata": metadata,
                }
            )
        return rows

    def index(self, paths: list[str], force_reindex: bool = False) -> dict[str, Any]:
        files = self._collect_files(paths)
        result_items: list[dict[str, Any]] = []
        indexed = 0
        skipped = 0
        failed = 0

        for path in files:
            source_path = str(path)
            try:
                checksum = _checksum(path)
                existing = self.store.get_source(source_path)
                if existing and existing.checksum == checksum and not force_reindex:
                    skipped += 1
                    result_items.append(
                        {
                            "path": source_path,
                            "status": "skipped",
                            "reason": "checksum_unchanged",
                        }
                    )
                    continue

                text = extract_text(path)
                chunks = chunk_text_by_tokens(
                    text=text,
                    chunk_size=self.settings.chunk_size,
                    overlap=self.settings.chunk_overlap,
                )
                if not chunks:
                    skipped += 1
                    result_items.append(
                        {
                            "path": source_path,
                            "status": "skipped",
                            "reason": "empty_content",
                        }
                    )
                    continue

                chunk_rows = self._build_chunk_rows(source_path, chunks)
                embeddings = self.embedder.embed_texts([item["text"] for item in chunk_rows])
                if not embeddings:
                    raise RuntimeError("Embedding output is empty.")
                self.vector_store.ensure_collection(dimension=len(embeddings[0]))

                source_record = self.store.replace_chunks(
                    source_path=source_path,
                    checksum=checksum,
                    chunks=chunk_rows,
                )

                points = [
                    {
                        "id": row["chunk_id"],
                        "vector": vector,
                        "payload": {
                            "source_id": source_record.source_id,
                            "source_path": source_record.path,
                            "version": source_record.version,
                            "chunk_index": row["chunk_index"],
                            "text": row["text"],
                        },
                    }
                    for row, vector in zip(chunk_rows, embeddings, strict=True)
                ]
                self.vector_store.replace_source_vectors(source_record.source_id, points)

                indexed += 1
                result_items.append(
                    {
                        "path": source_path,
                        "status": "indexed",
                        "source_id": source_record.source_id,
                        "version": source_record.version,
                        "chunks": len(chunk_rows),
                    }
                )
            except Exception as exc:
                failed += 1
                result_items.append(
                    {
                        "path": source_path,
                        "status": "failed",
                        "error": str(exc),
                    }
                )

        return {
            "summary": {
                "requested_paths": paths,
                "resolved_files": len(files),
                "indexed": indexed,
                "skipped": skipped,
                "failed": failed,
            },
            "items": result_items,
        }

    def _vector_recall(self, query: str, top_k: int) -> list[dict[str, Any]]:
        vectors = self.embedder.embed_texts([query])
        if not vectors:
            return []
        try:
            return self.vector_store.search(vectors[0], top_k=top_k)
        except Exception:
            return []

    def _bm25_recall(self, query: str, top_k: int) -> list[dict[str, Any]]:
        from rank_bm25 import BM25Okapi

        chunks = self.store.list_chunks()
        if not chunks:
            return []
        tokenized_corpus = [_tokenize_for_bm25(item.text) for item in chunks]
        bm25 = BM25Okapi(tokenized_corpus)
        scores = bm25.get_scores(_tokenize_for_bm25(query))
        ranked = sorted(
            ((idx, float(score)) for idx, score in enumerate(scores)),
            key=lambda x: x[1],
            reverse=True,
        )[:top_k]
        results: list[dict[str, Any]] = []
        for rank_idx, (corpus_idx, score) in enumerate(ranked, start=1):
            if score <= 0:
                continue
            item = chunks[corpus_idx]
            results.append(
                {
                    "chunk_id": item.chunk_id,
                    "bm25_score": score,
                    "bm25_rank": rank_idx,
                    "chunk": item,
                }
            )
        return results

    @staticmethod
    def _bm25_to_candidates(bm25_hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for item in bm25_hits:
            chunk = item.get("chunk")
            if chunk is None:
                continue
            candidates.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "score": float(item.get("bm25_score") or 0.0),
                    "text": chunk.text,
                    "source_path": chunk.source_path,
                    "chunk_index": chunk.chunk_index,
                    "version": chunk.version,
                    "token_count": chunk.token_count,
                }
            )
        return candidates

    def search(self, query: str, top_k: int | None = None, with_rerank: bool = True) -> dict[str, Any]:
        effective_top_k = max(1, top_k or self.settings.default_top_k)
        effective_query = (
            _normalize_query_for_retrieval(query)
            if self.settings.query_rewrite_enabled
            else query
        )

        bm25_hits = self._bm25_recall(effective_query, top_k=self.settings.bm25_top_k)
        cjk_bm25_shortcut = (
            self.settings.bm25_first_for_cjk
            and _contains_cjk(query)
            and len(bm25_hits) >= max(1, self.settings.bm25_early_return_min_hits)
        )
        vector_hits: list[dict[str, Any]] = []
        if not cjk_bm25_shortcut:
            vector_hits = self._vector_recall(effective_query, top_k=self.settings.vector_top_k)

        vector_rank = {item["chunk_id"]: idx for idx, item in enumerate(vector_hits, start=1)}
        bm25_rank = {item["chunk_id"]: idx for idx, item in enumerate(bm25_hits, start=1)}
        if cjk_bm25_shortcut:
            candidates = self._bm25_to_candidates(bm25_hits)
            candidates.sort(key=lambda x: x["score"], reverse=True)
        else:
            merged_score = _rrf({"vector": vector_rank, "bm25": bm25_rank})

            unique_ids = list(merged_score.keys())
            chunk_map = {item.chunk_id: item for item in self.store.list_chunks_by_ids(unique_ids)}

            candidates = []
            for chunk_id, score in sorted(merged_score.items(), key=lambda x: x[1], reverse=True):
                chunk = chunk_map.get(chunk_id)
                if chunk is None:
                    continue
                candidates.append(
                    {
                        "chunk_id": chunk.chunk_id,
                        "score": score,
                        "text": chunk.text,
                        "source_path": chunk.source_path,
                        "chunk_index": chunk.chunk_index,
                        "version": chunk.version,
                        "token_count": chunk.token_count,
                    }
                )

        rerank_allowed = with_rerank and self.reranker.enabled and (not cjk_bm25_shortcut)
        if rerank_allowed and candidates:
            rerank_pool = candidates[: self.settings.max_candidates_for_rerank]
            reranked = self.reranker.rerank(effective_query, rerank_pool)
            untouched = candidates[self.settings.max_candidates_for_rerank :]
            candidates = reranked + untouched

        hits = candidates[:effective_top_k]
        return {
            "query": query,
            "effective_query": effective_query,
            "top_k": effective_top_k,
            "hits": hits,
            "debug": {
                "vector_hits": len(vector_hits),
                "bm25_hits": len(bm25_hits),
                "merged_candidates": len(candidates),
                "rerank_enabled": bool(rerank_allowed),
                "cjk_bm25_shortcut": cjk_bm25_shortcut,
            },
        }

    def stats(self) -> dict[str, Any]:
        base = self.store.stats()
        try:
            vector_count = self.vector_store.count()
        except Exception:
            vector_count = None
        base.update(
            {
                "vector_backend": self.settings.vector_backend,
                "collection": self.settings.vector_collection,
                "vector_count": vector_count,
                "embedding_provider": self.settings.embedding_provider,
                "embedding_model": (
                    self.settings.openai_embedding_model
                    if self.settings.embedding_provider == "openai"
                    else self.settings.embedding_model
                ),
                "reranker_enabled": self.settings.rerank_enabled,
            }
        )
        return base

    def list_sources(self) -> list[dict[str, Any]]:
        sources = self.store.list_sources()
        return [
            {
                "source_id": item.source_id,
                "path": item.path,
                "version": item.version,
                "checksum": item.checksum,
                "last_indexed_at": item.last_indexed_at,
                "chunk_count": item.chunk_count,
            }
            for item in sources
        ]

    def delete_sources(self, paths: list[str], delete_files: bool = False) -> dict[str, Any]:
        requested = [path for path in paths if path]
        existing = {item.path: item for item in self.store.list_sources()}
        deleted = self.store.delete_sources_by_paths(requested)

        deleted_paths = {item.path for item in deleted}
        not_found = [path for path in requested if path not in deleted_paths]

        deleted_files: list[str] = []
        file_delete_failed: list[dict[str, str]] = []
        for item in deleted:
            try:
                self.vector_store.delete_source_vectors(item.source_id)
            except Exception:
                # Deletion should continue even if vector cleanup partly fails.
                pass
            if not delete_files:
                continue
            try:
                if os.path.isfile(item.path):
                    os.remove(item.path)
                    deleted_files.append(item.path)
            except Exception as exc:
                file_delete_failed.append({"path": item.path, "error": str(exc)})

        return {
            "requested": requested,
            "deleted_paths": [item.path for item in deleted],
            "not_found": not_found,
            "deleted_count": len(deleted),
            "deleted_files": deleted_files,
            "file_delete_failed": file_delete_failed,
            "before_exists": [path for path in requested if path in existing],
        }

    def clear(self, delete_files: bool = False) -> dict[str, Any]:
        existing = self.store.list_sources()
        deleted = self.store.delete_all_sources()
        deleted_files: list[str] = []
        file_delete_failed: list[dict[str, str]] = []

        for item in deleted:
            try:
                self.vector_store.delete_source_vectors(item.source_id)
            except Exception:
                pass
            if not delete_files:
                continue
            try:
                if os.path.isfile(item.path):
                    os.remove(item.path)
                    deleted_files.append(item.path)
            except Exception as exc:
                file_delete_failed.append({"path": item.path, "error": str(exc)})

        return {
            "total_before": len(existing),
            "cleared": len(deleted),
            "deleted_paths": [item.path for item in deleted],
            "deleted_files": deleted_files,
            "file_delete_failed": file_delete_failed,
        }

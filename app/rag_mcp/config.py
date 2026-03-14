from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class RagSettings:
    vector_backend: str
    chroma_path: Path
    vector_collection: str
    qdrant_url: str
    qdrant_api_key: str | None
    database_url: str | None
    sqlite_path: Path
    embedding_provider: str
    embedding_model: str
    openai_embedding_model: str
    rerank_enabled: bool
    reranker_model: str
    chunk_size: int
    chunk_overlap: int
    vector_top_k: int
    bm25_top_k: int
    default_top_k: int
    max_candidates_for_rerank: int
    indexing_glob: tuple[str, ...]
    hf_cache_dir: Path
    bm25_first_for_cjk: bool
    bm25_early_return_min_hits: int
    query_rewrite_enabled: bool

    @classmethod
    def from_env(cls, root_path: Path) -> "RagSettings":
        sqlite_default = root_path / "workspace" / "rag" / "rag.sqlite3"
        chroma_default = root_path / "workspace" / "rag" / "chroma"
        hf_cache_default = root_path / "workspace" / "rag" / "hf-cache"
        vector_collection = os.getenv("RAG_VECTOR_COLLECTION", "openmanus_rag").strip()
        return cls(
            vector_backend=os.getenv("RAG_VECTOR_BACKEND", "chroma").strip().lower(),
            chroma_path=Path(os.getenv("RAG_CHROMA_PATH", str(chroma_default))),
            vector_collection=vector_collection,
            qdrant_url=os.getenv("RAG_QDRANT_URL", "http://127.0.0.1:6333").strip(),
            qdrant_api_key=os.getenv("RAG_QDRANT_API_KEY"),
            database_url=(os.getenv("RAG_DATABASE_URL", "").strip() or None),
            sqlite_path=Path(os.getenv("RAG_SQLITE_PATH", str(sqlite_default))),
            embedding_provider=os.getenv("RAG_EMBEDDING_PROVIDER", "local").strip().lower(),
            embedding_model=os.getenv("RAG_EMBEDDING_MODEL", "BAAI/bge-m3").strip(),
            openai_embedding_model=os.getenv(
                "RAG_OPENAI_EMBEDDING_MODEL",
                "text-embedding-3-small",
            ).strip(),
            rerank_enabled=_truthy(os.getenv("RAG_RERANK_ENABLED"), default=True),
            reranker_model=os.getenv(
                "RAG_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"
            ).strip(),
            chunk_size=int(os.getenv("RAG_CHUNK_SIZE", "600")),
            chunk_overlap=int(os.getenv("RAG_CHUNK_OVERLAP", "100")),
            vector_top_k=int(os.getenv("RAG_VECTOR_TOP_K", "20")),
            bm25_top_k=int(os.getenv("RAG_BM25_TOP_K", "20")),
            default_top_k=int(os.getenv("RAG_DEFAULT_TOP_K", "8")),
            max_candidates_for_rerank=int(os.getenv("RAG_RERANK_MAX_CANDIDATES", "30")),
            indexing_glob=tuple(
                token.strip().lower()
                for token in os.getenv(
                    "RAG_INDEX_GLOB",
                    "*.txt,*.md,*.pdf,*.docx,*.py,*.json,*.yaml,*.yml",
                ).split(",")
                if token.strip()
            ),
            hf_cache_dir=Path(os.getenv("RAG_HF_CACHE_DIR", str(hf_cache_default))),
            bm25_first_for_cjk=_truthy(os.getenv("RAG_BM25_FIRST_FOR_CJK"), default=True),
            bm25_early_return_min_hits=int(
                os.getenv("RAG_BM25_EARLY_RETURN_MIN_HITS", "1")
            ),
            query_rewrite_enabled=_truthy(
                os.getenv("RAG_QUERY_REWRITE_ENABLED"), default=True
            ),
        )

    def apply_model_cache_env(self) -> None:
        """
        Use one stable HuggingFace/SentenceTransformer cache location for RAG.
        Avoids repeated downloads caused by different process/env defaults.
        """
        cache_dir = self.hf_cache_dir.expanduser().resolve()
        cache_dir.mkdir(parents=True, exist_ok=True)
        os.environ["RAG_HF_CACHE_DIR"] = str(cache_dir)
        os.environ.setdefault("HF_HOME", str(cache_dir))
        os.environ.setdefault("TRANSFORMERS_CACHE", str(cache_dir / "transformers"))
        os.environ.setdefault(
            "SENTENCE_TRANSFORMERS_HOME",
            str(cache_dir / "sentence_transformers"),
        )

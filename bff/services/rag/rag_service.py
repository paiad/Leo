from __future__ import annotations

from pathlib import Path
from typing import Any

from app.rag_mcp.config import RagSettings
from app.rag_mcp.service import RagService


class RagRuntimeService:
    def __init__(self, root_path: Path):
        self._root_path = root_path
        self._rag_service: RagService | None = None

    def _get_service(self) -> RagService:
        if self._rag_service is None:
            settings = RagSettings.from_env(root_path=self._root_path)
            settings.apply_model_cache_env()
            self._rag_service = RagService(settings=settings)
        return self._rag_service

    def index(self, paths: list[str], force_reindex: bool = False) -> dict[str, Any]:
        return self._get_service().index(paths=paths, force_reindex=force_reindex)

    def search(
        self,
        query: str,
        top_k: int | None = None,
        with_rerank: bool = True,
    ) -> dict[str, Any]:
        return self._get_service().search(
            query=query,
            top_k=top_k,
            with_rerank=with_rerank,
        )

    def stats(self) -> dict[str, Any]:
        return self._get_service().stats()

    def list_sources(self) -> list[dict[str, Any]]:
        return self._get_service().list_sources()

    def delete_sources(self, paths: list[str], delete_files: bool = False) -> dict[str, Any]:
        return self._get_service().delete_sources(paths=paths, delete_files=delete_files)

    def clear(self, delete_files: bool = False) -> dict[str, Any]:
        return self._get_service().clear(delete_files=delete_files)

from __future__ import annotations

import os
from typing import Any

from openai import OpenAI
from app.logger import logger


class Embedder:
    def __init__(
        self,
        provider: str,
        local_model: str,
        openai_model: str,
    ):
        self.provider = provider
        self.local_model = local_model
        self.openai_model = openai_model
        self._local_client: Any = None
        self._openai_client: OpenAI | None = None

    def _get_local_client(self) -> Any:
        if self._local_client is None:
            from sentence_transformers import SentenceTransformer

            device = None
            dtype = None
            # Shared embedder for both RAG and MCP tool routing. Allow either env prefix.
            requested_device = (
                (os.getenv("BFF_MCP_TOOL_EMBEDDING_DEVICE") or "").strip().lower()
                or (os.getenv("RAG_EMBEDDING_DEVICE") or "").strip().lower()
            )
            if requested_device in {"cuda", "cpu"}:
                device = requested_device

            requested_dtype = (
                (os.getenv("BFF_MCP_TOOL_EMBEDDING_DTYPE") or "").strip().lower()
                or (os.getenv("RAG_EMBEDDING_DTYPE") or "").strip().lower()
            )
            if requested_dtype in {"float16", "fp16", "half"}:
                dtype = "float16"
            elif requested_dtype in {"bfloat16", "bf16"}:
                dtype = "bfloat16"

            model_kwargs: dict[str, Any] | None = None
            if dtype is not None:
                try:
                    import torch

                    if dtype == "float16":
                        model_kwargs = {"torch_dtype": torch.float16}
                    elif dtype == "bfloat16":
                        model_kwargs = {"torch_dtype": torch.bfloat16}
                except Exception:
                    model_kwargs = None

            try:
                self._local_client = SentenceTransformer(
                    self.local_model,
                    device=device,
                    model_kwargs=model_kwargs,
                )
            except Exception as exc:
                # Best-effort fallback: if CUDA / dtype settings are not supported
                # or the GPU is out of memory, fall back to CPU to keep the system usable.
                logger.warning(
                    "Embedder init failed, falling back to CPU: "
                    f"model={self.local_model}, device={device}, dtype={dtype}, error={exc}"
                )
                self._local_client = SentenceTransformer(self.local_model, device="cpu")

            logger.info(
                "Embedder initialized: "
                f"provider=local, model={self.local_model}, device={getattr(self._local_client, 'device', 'unknown')}"
            )
        return self._local_client

    def _get_openai_client(self) -> OpenAI:
        if self._openai_client is None:
            self._openai_client = OpenAI()
        return self._openai_client

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self.provider == "openai":
            return self._embed_openai(texts)
        return self._embed_local(texts)

    def _embed_local(self, texts: list[str]) -> list[list[float]]:
        model = self._get_local_client()
        vectors = model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return [vector.tolist() for vector in vectors]

    def _embed_openai(self, texts: list[str]) -> list[list[float]]:
        client = self._get_openai_client()
        response = client.embeddings.create(
            model=self.openai_model,
            input=texts,
        )
        return [item.embedding for item in response.data]


class Reranker:
    def __init__(self, enabled: bool, model_name: str):
        self.enabled = enabled
        self.model_name = model_name
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from sentence_transformers import CrossEncoder

            self._client = CrossEncoder(self.model_name)
        return self._client

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.enabled or not candidates:
            return candidates
        client = self._get_client()
        pairs = [[query, item["text"]] for item in candidates]
        scores = client.predict(pairs)
        for item, score in zip(candidates, scores, strict=False):
            item["rerank_score"] = float(score)
        return sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)

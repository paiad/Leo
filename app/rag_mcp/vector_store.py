from __future__ import annotations

from typing import Any


class ChromaVectorStore:
    def __init__(self, persist_path, collection_name: str):
        self.collection_name = collection_name
        self._client = self._build_client(persist_path)
        self._collection = None

    @staticmethod
    def _build_client(persist_path):
        from chromadb import PersistentClient

        return PersistentClient(path=str(persist_path))

    def ensure_collection(self, dimension: int) -> None:
        # Chroma infers vector dimension at first insert; no explicit dimension setup needed.
        if self._collection is None:
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )

    def replace_source_vectors(self, source_id: int, points: list[dict[str, Any]]) -> None:
        self.ensure_collection(dimension=0)
        assert self._collection is not None
        self._collection.delete(where={"source_id": source_id})
        if not points:
            return
        self._collection.upsert(
            ids=[item["id"] for item in points],
            embeddings=[item["vector"] for item in points],
            documents=[item["payload"].get("text", "") for item in points],
            metadatas=[item["payload"] for item in points],
        )

    def delete_source_vectors(self, source_id: int) -> None:
        self.ensure_collection(dimension=0)
        assert self._collection is not None
        self._collection.delete(where={"source_id": source_id})

    def search(self, query_vector: list[float], top_k: int) -> list[dict[str, Any]]:
        self.ensure_collection(dimension=0)
        assert self._collection is not None
        results = self._collection.query(
            query_embeddings=[query_vector],
            n_results=top_k,
            include=["metadatas", "distances"],
        )
        ids = (results.get("ids") or [[]])[0]
        metadatas = (results.get("metadatas") or [[]])[0]
        distances = (results.get("distances") or [[]])[0]

        output: list[dict[str, Any]] = []
        for chunk_id, metadata, distance in zip(ids, metadatas, distances, strict=False):
            score = 1.0 - float(distance)
            output.append(
                {
                    "chunk_id": str(chunk_id),
                    "vector_score": score,
                    "payload": metadata or {},
                }
            )
        return output

    def count(self) -> int:
        self.ensure_collection(dimension=0)
        assert self._collection is not None
        return int(self._collection.count())


class QdrantVectorStore:
    def __init__(self, url: str, collection_name: str, api_key: str | None = None):
        self.collection_name = collection_name
        self._client = self._build_client(url=url, api_key=api_key)
        self._dimension: int | None = None

    @staticmethod
    def _build_client(url: str, api_key: str | None):
        from qdrant_client import QdrantClient

        return QdrantClient(url=url, api_key=api_key, timeout=15)

    def ensure_collection(self, dimension: int) -> None:
        from qdrant_client.http import models

        if self._dimension == dimension:
            return

        collections = self._client.get_collections().collections
        names = {item.name for item in collections}
        if self.collection_name not in names:
            self._client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=dimension,
                    distance=models.Distance.COSINE,
                ),
            )
        self._dimension = dimension

    def replace_source_vectors(self, source_id: int, points: list[dict[str, Any]]) -> None:
        from qdrant_client.http import models

        self._client.delete(
            collection_name=self.collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="source_id",
                            match=models.MatchValue(value=source_id),
                        )
                    ]
                )
            ),
        )
        if not points:
            return

        payload_points = [
            models.PointStruct(
                id=item["id"],
                vector=item["vector"],
                payload=item["payload"],
            )
            for item in points
        ]
        self._client.upsert(collection_name=self.collection_name, points=payload_points)

    def delete_source_vectors(self, source_id: int) -> None:
        from qdrant_client.http import models

        self._client.delete(
            collection_name=self.collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="source_id",
                            match=models.MatchValue(value=source_id),
                        )
                    ]
                )
            ),
        )

    def search(self, query_vector: list[float], top_k: int) -> list[dict[str, Any]]:
        results = self._client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            limit=top_k,
            with_payload=True,
        )
        output: list[dict[str, Any]] = []
        for point in results.points:
            payload = point.payload or {}
            output.append(
                {
                    "chunk_id": str(point.id),
                    "vector_score": float(point.score),
                    "payload": payload,
                }
            )
        return output

    def count(self) -> int:
        info = self._client.count(collection_name=self.collection_name, exact=True)
        return int(info.count)

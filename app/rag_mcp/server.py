from __future__ import annotations

import argparse
import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from app.logger import logger
from app.rag_mcp.config import RagSettings
from app.rag_mcp.service import RagService


class RagMCPServer:
    def __init__(self, name: str = "rag"):
        self.server = FastMCP(name)
        root_path = Path(__file__).resolve().parents[2]
        settings = RagSettings.from_env(root_path=root_path)
        settings.apply_model_cache_env()
        logger.info(f"RAG model cache directory: {settings.hf_cache_dir.expanduser().resolve()}")
        self.rag = RagService(settings=settings)
        self._register_tools()

    def _register_tools(self) -> None:
        @self.server.tool()
        async def index(paths: list[str], force_reindex: bool = False) -> str:
            """Index local files/directories for retrieval."""
            payload = self.rag.index(paths=paths, force_reindex=force_reindex)
            return json.dumps(payload, ensure_ascii=False)

        @self.server.tool()
        async def search(query: str, top_k: int = 8, with_rerank: bool = True) -> str:
            """Hybrid retrieval (vector + BM25) with optional rerank."""
            payload = self.rag.search(query=query, top_k=top_k, with_rerank=with_rerank)
            return json.dumps(payload, ensure_ascii=False)

        @self.server.tool()
        async def stats() -> str:
            """Show RAG index statistics."""
            payload = self.rag.stats()
            return json.dumps(payload, ensure_ascii=False)

    def run(self, transport: str = "stdio") -> None:
        self.server.run(transport=transport)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAG MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio"],
        default="stdio",
        help="Communication method",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    RagMCPServer().run(transport=args.transport)

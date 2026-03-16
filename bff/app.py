from __future__ import annotations

import asyncio
import sys
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.logger import logger
from bff.core.response import err
from bff.utils.env import load_dotenv_into_environ

# Load .env before importing modules that initialize singleton services/stores.
load_dotenv_into_environ(override=False)

def _apply_hf_cache_env_from_env() -> None:
    """
    Apply a stable HuggingFace cache directory as early as possible.

    Why: some libraries (e.g. faster-whisper / huggingface_hub) may resolve cache
    locations at import-time. If the default user cache contains a partial/corrupt
    download (e.g. 0-byte `model.bin`), ASR/embeddings can fail until the cache
    is redirected or cleaned.

    Priority:
    1) FEISHU_AUDIO_ASR_HF_CACHE_DIR (force)
    2) BFF_MCP_TOOL_HF_CACHE_DIR
    3) RAG_HF_CACHE_DIR
    """
    from pathlib import Path

    forced_cache_dir = os.getenv("FEISHU_AUDIO_ASR_HF_CACHE_DIR")
    cache_dir = forced_cache_dir or os.getenv("BFF_MCP_TOOL_HF_CACHE_DIR") or os.getenv("RAG_HF_CACHE_DIR")
    if not cache_dir:
        return

    base = Path(str(cache_dir)).expanduser()
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        return

    force = bool(forced_cache_dir)
    if force or not os.getenv("HF_HOME"):
        os.environ["HF_HOME"] = str(base)
    if force or not os.getenv("HUGGINGFACE_HUB_CACHE"):
        os.environ["HUGGINGFACE_HUB_CACHE"] = str(base / "hub")
    if force or not os.getenv("TRANSFORMERS_CACHE"):
        os.environ["TRANSFORMERS_CACHE"] = str(base / "transformers")
    if force or not os.getenv("SENTENCE_TRANSFORMERS_HOME"):
        os.environ["SENTENCE_TRANSFORMERS_HOME"] = str(base / "sentence_transformers")


_apply_hf_cache_env_from_env()

from bff.api.router import router
from bff.repositories.store import store
from bff.services.container import tooling_service
from bff.services.integration.feishu_long_connection import feishu_long_connection_service
from bff.services.runtime.mcp_routing.runtime_tool_index_sqlite import create_mcp_tool_index_from_env


def create_app() -> FastAPI:
    app = FastAPI(title='Leo BFF', version='0.2.0')

    app.add_middleware(
        CORSMiddleware,
        allow_origins=['*'],
        allow_credentials=True,
        allow_methods=['*'],
        allow_headers=['*'],
    )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, exc: HTTPException):
        if isinstance(exc.detail, dict) and {'success', 'data', 'error'} <= set(exc.detail.keys()):
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code, content=err(str(exc.detail)))

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_request: Request, exc: Exception):
        return JSONResponse(status_code=500, content=err(str(exc)))

    @app.on_event("startup")
    async def loop_runtime_check() -> None:
        loop = asyncio.get_running_loop()
        loop_type = type(loop).__name__
        if sys.platform == "win32" and "Proactor" not in loop_type:
            logger.warning(
                "Detected non-Proactor event loop on Windows. "
                "Playwright/MCP-stdio subprocess tools may fail with NotImplementedError. "
                "Run uvicorn without --reload."
            )
        feishu_long_connection_service.start(loop)

        auto_discover = (os.getenv("BFF_MCP_AUTO_DISCOVER_ON_STARTUP", "0") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if auto_discover:
            async def _auto_discover_and_index() -> None:
                try:
                    limit = int(os.getenv("BFF_MCP_AUTO_DISCOVER_LIMIT", "20") or 20)
                except Exception:
                    limit = 20
                result = await tooling_service.auto_discover_enabled_servers(
                    only_if_empty=True,
                    limit=limit,
                )
                if result.get("discovered"):
                    try:
                        index = create_mcp_tool_index_from_env()
                        index.refresh_from_store(store)
                        logger.info(
                            "MCP startup: auto-discover complete and tool index refreshed; "
                            f"discovered={result.get('discovered')}"
                        )
                    except Exception as exc:
                        logger.warning(f"MCP startup: tool index refresh failed: {exc}")
                else:
                    logger.info(
                        "MCP startup: auto-discover finished; "
                        f"discovered=0, failed={len(result.get('failed') or [])}"
                    )
                failed = result.get("failed") or []
                if failed:
                    logger.warning(f"MCP startup: auto-discover failures: {failed}")

            asyncio.create_task(_auto_discover_and_index())

        warmup_enabled = (os.getenv("BFF_MCP_TOOL_EMBED_WARMUP_ON_STARTUP", "1") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if warmup_enabled:
            async def _warmup_mcp_tool_query_embedding() -> None:
                try:
                    index = create_mcp_tool_index_from_env()
                    warmup_query = (
                        os.getenv("BFF_MCP_TOOL_EMBED_WARMUP_QUERY", "打开网页并点击播放视频")
                        or "打开网页并点击播放视频"
                    )
                    warmup_timeout_ms = int(
                        os.getenv("BFF_MCP_TOOL_EMBED_WARMUP_TIMEOUT_MS", "8000") or 8000
                    )
                    result = await asyncio.to_thread(
                        index.warmup_query_embedding,
                        warmup_query,
                        timeout_ms=warmup_timeout_ms,
                    )
                    logger.info(
                        "MCP startup: query embedding warmup finished; "
                        f"query='{warmup_query}', timeout_ms={warmup_timeout_ms}, result={result}"
                    )
                except Exception as exc:
                    logger.warning(f"MCP startup: query embedding warmup failed: {exc}")

            asyncio.create_task(_warmup_mcp_tool_query_embedding())

    @app.on_event("shutdown")
    async def shutdown_services() -> None:
        feishu_long_connection_service.stop()

    app.include_router(router)
    return app


app = create_app()

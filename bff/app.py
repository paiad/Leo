from __future__ import annotations

import asyncio
import sys

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.logger import logger
from bff.core.response import err
from bff.utils.env import load_dotenv_into_environ

# Load .env before importing modules that initialize singleton services/stores.
load_dotenv_into_environ(override=False)

from bff.api.router import router
from bff.services.integration.feishu_long_connection import feishu_long_connection_service


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

    @app.on_event("shutdown")
    async def shutdown_services() -> None:
        feishu_long_connection_service.stop()

    app.include_router(router)
    return app


app = create_app()

from __future__ import annotations

from fastapi import APIRouter

from bff.api.chat.chat import router as chat_router
from bff.api.integration.feishu import router as feishu_router
from bff.api.models.models import router as models_router
from bff.api.rag.rag import router as rag_router
from bff.api.system.health import router as health_router
from bff.api.system.runtime import router as runtime_router
from bff.api.tooling.mcp import router as mcp_router
from bff.api.tooling.tools import router as mcp_catalog_router

router = APIRouter()
router.include_router(health_router)
router.include_router(runtime_router)
router.include_router(chat_router)
router.include_router(feishu_router)
router.include_router(mcp_catalog_router)
router.include_router(mcp_router)
router.include_router(models_router)
router.include_router(rag_router)

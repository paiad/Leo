from __future__ import annotations

from fastapi import APIRouter

from bff.api.chat import router as chat_router
from bff.api.feishu import router as feishu_router
from bff.api.health import router as health_router
from bff.api.mcp import router as mcp_router
from bff.api.models import router as models_router
from bff.api.tools import router as mcp_catalog_router

router = APIRouter()
router.include_router(health_router)
router.include_router(chat_router)
router.include_router(feishu_router)
router.include_router(mcp_catalog_router)
router.include_router(mcp_router)
router.include_router(models_router)

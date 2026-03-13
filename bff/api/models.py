from __future__ import annotations

from fastapi import APIRouter, HTTPException

from bff.core.response import err, ok
from bff.domain.models import WorkspaceModelCreate, WorkspaceModelUpdate
from bff.services.container import model_service

router = APIRouter(prefix="/api/v1/models", tags=["models"])


@router.get("")
async def list_models() -> dict:
    return ok(model_service.list_models())


@router.post("")
async def create_model(payload: WorkspaceModelCreate) -> dict:
    return ok(model_service.create_model(payload))


@router.put("/{model_id}")
async def update_model(model_id: str, payload: WorkspaceModelUpdate) -> dict:
    model = model_service.update_model(model_id, payload)
    if model is None:
        raise HTTPException(status_code=404, detail=err("模型不存在"))
    return ok(model)


@router.delete("/{model_id}")
async def delete_model(model_id: str) -> dict:
    deleted = model_service.delete_model(model_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=err("模型不存在"))
    return ok({"deleted": True})


@router.get("/active")
async def get_active_model() -> dict:
    active = model_service.get_active_model()
    if active is None:
        return ok(None)
    return ok(active)


@router.put("/active/{model_id}")
async def set_active_model(model_id: str) -> dict:
    active = model_service.set_active_model(model_id)
    if active is None:
        raise HTTPException(status_code=404, detail=err("模型不存在"))
    return ok(active)

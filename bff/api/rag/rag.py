from __future__ import annotations

import os
import shutil
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from bff.core.response import err, ok
from bff.services.container import rag_service

router = APIRouter(prefix="/api/v1/rag", tags=["rag"])


class RagIndexRequest(BaseModel):
    paths: list[str] = Field(default_factory=list)
    forceReindex: bool = False


class RagSearchRequest(BaseModel):
    query: str
    topK: int = 8
    withRerank: bool = True


class RagDeleteRequest(BaseModel):
    paths: list[str] = Field(default_factory=list)
    deleteFiles: bool = False


class RagClearRequest(BaseModel):
    deleteFiles: bool = False


def _resolve_upload_root() -> Path:
    default_path = Path.cwd() / "workspace" / "rag" / "uploads"
    raw = os.getenv("RAG_UPLOAD_DIR", str(default_path))
    return Path(raw).expanduser().resolve()


@router.post("/upload")
async def upload_files(files: list[UploadFile] = File(...)) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail=err("至少上传一个文件"))

    upload_root = _resolve_upload_root()
    upload_root.mkdir(parents=True, exist_ok=True)

    saved_paths: list[str] = []
    for file in files:
        if not file.filename:
            continue
        filename = Path(file.filename).name
        target = upload_root / filename
        with target.open("wb") as output:
            shutil.copyfileobj(file.file, output)
        saved_paths.append(str(target))

    if not saved_paths:
        raise HTTPException(status_code=400, detail=err("没有可保存的有效文件"))

    return ok({"savedPaths": saved_paths, "uploadDir": str(upload_root)})


@router.post("/index")
async def index_documents(payload: RagIndexRequest) -> dict:
    if not payload.paths:
        raise HTTPException(status_code=400, detail=err("paths 不能为空"))
    try:
        return ok(
            rag_service.index(
                paths=payload.paths,
                force_reindex=payload.forceReindex,
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=err(f"RAG index 失败: {exc}")) from exc


@router.post("/search")
async def search_documents(payload: RagSearchRequest) -> dict:
    if not payload.query.strip():
        raise HTTPException(status_code=400, detail=err("query 不能为空"))
    try:
        return ok(
            rag_service.search(
                query=payload.query.strip(),
                top_k=payload.topK,
                with_rerank=payload.withRerank,
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=err(f"RAG search 失败: {exc}")) from exc


@router.get("/stats")
async def rag_stats() -> dict:
    try:
        return ok(rag_service.stats())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=err(f"RAG stats 失败: {exc}")) from exc


@router.get("/sources")
async def rag_sources() -> dict:
    try:
        return ok({"sources": rag_service.list_sources()})
    except Exception as exc:
        raise HTTPException(status_code=400, detail=err(f"RAG sources 失败: {exc}")) from exc


@router.post("/delete")
async def delete_sources(payload: RagDeleteRequest) -> dict:
    if not payload.paths:
        raise HTTPException(status_code=400, detail=err("paths 不能为空"))
    try:
        return ok(
            rag_service.delete_sources(
                paths=payload.paths,
                delete_files=payload.deleteFiles,
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=err(f"RAG delete 失败: {exc}")) from exc


@router.post("/clear")
async def clear_sources(payload: RagClearRequest) -> dict:
    try:
        return ok(rag_service.clear(delete_files=payload.deleteFiles))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=err(f"RAG clear 失败: {exc}")) from exc

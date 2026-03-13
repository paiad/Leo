from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get('/healthz')
async def healthz() -> dict[str, object]:
    return {'ok': True, 'service': 'leo-bff'}

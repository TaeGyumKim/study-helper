"""헬스 체크 및 버전 엔드포인트."""

from fastapi import APIRouter

from src.config import APP_VERSION

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/version")
def version():
    return {"version": APP_VERSION}

"""헬스 체크 및 버전 엔드포인트."""

from fastapi import APIRouter

from src.config import APP_VERSION

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """서버 가동 여부 체크."""
    return {"status": "ok"}


@router.get("/version")
def version() -> dict[str, str]:
    """애플리케이션 버전 조회."""
    return {"version": APP_VERSION}

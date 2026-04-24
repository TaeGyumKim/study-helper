"""URL 공용 유틸리티."""

from urllib.parse import urlparse


def safe_url(url: str) -> str:
    """URL에서 쿼리 파라미터를 제거하여 세션 토큰 노출을 방지한다."""
    return urlparse(url)._replace(query="", fragment="").geturl()

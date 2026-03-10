FROM python:3.11-slim-bookworm

# 시스템 의존성 설치 및 보안 패치 적용
# - apt-get upgrade: 알려진 CVE (jpeg-xl, freetype, tar 등) 수정
# - ffmpeg: mp4 → mp3 변환 (사용자 다운로드용)
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# uv 설치 (빠른 패키지 관리)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 의존성 파일 먼저 복사 (레이어 캐시 활용)
COPY pyproject.toml uv.lock* ./

# pip/wheel/setuptools 최신 버전으로 업그레이드 (CVE-2025-8869, CVE-2026-24049 대응)
RUN pip install --no-cache-dir --upgrade pip wheel setuptools

# torch CPU 전용 설치 — GPU 없는 Docker 환경 최적화
# full torch 대비 이미지 크기 ~1/3 수준으로 감소 (~700MB vs ~2.5GB)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# 나머지 패키지 설치 (torch는 위에서 설치했으므로 제외)
RUN uv sync --frozen --no-dev --no-install-package torch

# Chrome(H.264 포함)을 우선 설치, ARM64 등 미지원 환경에서는 Chromium으로 fallback.
# Google Chrome은 Linux amd64만 지원 — Apple Silicon(arm64) Docker에서는 Chromium 사용.
RUN uv run playwright install --with-deps chrome 2>/dev/null \
    || uv run playwright install --with-deps chromium

# 소스 코드 복사
COPY src/ ./src/
COPY CHANGELOG.md ./

# 다운로드 경로 및 캐시 디렉토리 생성
RUN mkdir -p /data/downloads \
    && mkdir -p /root/.cache/whisper \
    && mkdir -p /root/.cache/ms-playwright

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright

ENTRYPOINT ["uv", "run", "--no-sync", "python", "src/main.py"]

FROM python:3.11-slim-bookworm

# 시스템 의존성 설치 및 보안 패치 적용
# - apt-get upgrade: 알려진 CVE (jpeg-xl, freetype, tar 등) 수정
# - ffmpeg: mp4 → mp3 변환 (사용자 다운로드용)
# - curl: HEALTHCHECK 용
# - tini: init process (PID 1) — Playwright 자식 프로세스 좀비 수집
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    tini \
    && rm -rf /var/lib/apt/lists/*

# TZ=Asia/Seoul 을 compose 없이 docker run 직접 실행 시에도 적용
ENV TZ=Asia/Seoul

WORKDIR /app

# uv 설치 (빠른 패키지 관리)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 의존성 파일 먼저 복사 (레이어 캐시 활용)
COPY pyproject.toml uv.lock* ./

# pip/wheel/setuptools 최신 버전으로 업그레이드 (CVE-2025-8869, CVE-2026-24049 대응)
RUN pip install --no-cache-dir --upgrade pip wheel setuptools

# 패키지 설치
RUN uv sync --frozen --no-dev

# Chrome(H.264 포함)을 우선 설치, ARM64 등 미지원 환경에서는 Chromium으로 fallback.
# Google Chrome은 Linux amd64만 지원 — Apple Silicon(arm64) Docker에서는 Chromium 사용.
RUN uv run playwright install --with-deps chrome 2>/dev/null \
    || uv run playwright install --with-deps chromium

# 소스 코드 복사
COPY src/ ./src/
COPY CHANGELOG.md ./

# 다운로드 경로 및 캐시 디렉토리 생성
RUN mkdir -p /data/downloads \
    && mkdir -p /root/.cache/huggingface \
    && mkdir -p /root/.cache/ms-playwright

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright

# API 서버 모드 전용 HEALTHCHECK — CUI 모드에서는 /health 미제공하므로 실패로
# 판정되지만, 이는 컨테이너를 API 서버로 구동하지 않았다는 정보 신호로 충분.
# --start-period=20s 로 Playwright 초기화 시간 확보.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fs http://127.0.0.1:${STUDY_HELPER_API_PORT:-18090}/health || exit 1

# tini 로 Playwright/Chrome 자식 프로세스 signal forwarding + 좀비 수집
ENTRYPOINT ["/usr/bin/tini", "--", "uv", "run", "--no-sync", "python", "src/main.py"]

"""
스케줄 관리 서비스.

자동 모드의 스케줄 로직을 UI 독립적으로 제공한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from src.config import KST

# 기본 스케줄 (KST 시각, 정각)
DEFAULT_SCHEDULE_HOURS = [9, 13, 18, 23]


def next_schedule_time(schedule_hours: list[int], now: datetime | None = None) -> datetime:
    """다음 스케줄 실행 시각(KST)을 반환한다."""
    if now is None:
        now = datetime.now(KST)
    today_schedules = [now.replace(hour=h, minute=0, second=0, microsecond=0) for h in sorted(schedule_hours)]
    for t in today_schedules:
        if t > now:
            return t
    # 오늘 스케줄이 모두 지난 경우 → 내일 첫 번째 스케줄
    tomorrow = now + timedelta(days=1)
    return tomorrow.replace(hour=sorted(schedule_hours)[0], minute=0, second=0, microsecond=0)


def fmt_remaining(target: datetime, now: datetime | None = None) -> str:
    """현재 시각부터 target까지 남은 시간을 'H시간 M분 S초' 형식으로 반환한다."""
    if now is None:
        now = datetime.now(KST)
    delta = target - now
    total = max(0, int(delta.total_seconds()))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}시간 {m}분 {s}초"
    if m > 0:
        return f"{m}분 {s}초"
    return f"{s}초"


def check_auto_prerequisites(config) -> list[str]:
    """자동 모드 필수 조건을 확인하고 미충족 항목 목록을 반환한다.

    Args:
        config: Config 클래스 (속성 접근용)
    """
    issues = []
    if config.STT_ENABLED != "true":
        issues.append("STT 미활성화")
    if config.AI_ENABLED != "true":
        issues.append("AI 요약 미활성화")
    if config.TELEGRAM_ENABLED != "true":
        issues.append("텔레그램 알림 미활성화")
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        issues.append("텔레그램 봇 토큰 또는 Chat ID 미설정")
    api_key = config.GOOGLE_API_KEY if config.AI_AGENT == "gemini" else config.OPENAI_API_KEY
    if not api_key:
        issues.append("AI API 키 미설정")
    return issues


def parse_schedule_input(raw: str) -> list[int] | None:
    """스케줄 입력 문자열을 파싱한다.

    Args:
        raw: 쉼표로 구분된 시간 문자열 (예: "8,12,18,22")

    Returns:
        정렬된 시간 목록. 파싱 실패 시 None.
    """
    if not raw.strip():
        return list(DEFAULT_SCHEDULE_HOURS)
    try:
        hours = [int(h.strip()) for h in raw.split(",")]
        if not hours or any(h < 0 or h > 23 for h in hours):
            return None
        return sorted(set(hours))
    except ValueError:
        return None

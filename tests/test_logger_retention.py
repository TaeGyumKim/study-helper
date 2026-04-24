"""LOG-SYS-2 회귀 방지: 14일 초과 에러 로그 자동 삭제."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import src.logger as logger_mod
from src.logger import _cleanup_old_error_logs


def _make_log(path: Path, stem: str) -> Path:
    f = path / f"{stem}.log"
    f.write_text("x", encoding="utf-8")
    return f


def test_cleanup_deletes_old_error_logs(tmp_path: Path, monkeypatch) -> None:
    """14일 이전 파일은 삭제되고 최근 파일/study_helper.log 는 보존."""
    # retention cache 리셋
    monkeypatch.setattr(logger_mod, "_error_retention_cleaned", False)

    today = datetime.now(logger_mod._KST).date()
    old_date = today - timedelta(days=20)
    recent_date = today - timedelta(days=3)

    old_file = _make_log(tmp_path, f"{old_date.strftime('%Y%m%d')}_120000_download")
    recent_file = _make_log(tmp_path, f"{recent_date.strftime('%Y%m%d')}_120000_play")
    protected = _make_log(tmp_path, "study_helper")
    protected_rotation = _make_log(tmp_path, "study_helper.log.2026-03-01")

    _cleanup_old_error_logs(tmp_path)

    assert not old_file.exists(), "14일 초과 파일은 삭제되어야 함"
    assert recent_file.exists(), "최근 파일은 보존"
    assert protected.exists(), "study_helper 파일은 보존"
    assert protected_rotation.exists(), "TimedRotatingFileHandler 로테이션 파일은 보존"


def test_cleanup_runs_once_per_process(tmp_path: Path, monkeypatch) -> None:
    """한 프로세스 내 중복 호출 시 i/o 반복 없이 조기 리턴."""
    monkeypatch.setattr(logger_mod, "_error_retention_cleaned", False)
    _cleanup_old_error_logs(tmp_path)
    assert logger_mod._error_retention_cleaned is True

    # 2회차: 이미 True 라 glob 도 수행 안 하는지 간접 검증 — 새 파일을 만들고 다시 호출
    today = datetime.now(logger_mod._KST).date()
    old = today - timedelta(days=30)
    old_file = _make_log(tmp_path, f"{old.strftime('%Y%m%d')}_120000_download")
    _cleanup_old_error_logs(tmp_path)
    # retention 이 다시 안 돌았으므로 파일은 남아있어야 함
    assert old_file.exists()


def test_cleanup_skips_malformed_names(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(logger_mod, "_error_retention_cleaned", False)
    weird = _make_log(tmp_path, "not_a_date_format")
    _cleanup_old_error_logs(tmp_path)
    assert weird.exists(), "날짜 파싱 실패 시 삭제하지 않음"

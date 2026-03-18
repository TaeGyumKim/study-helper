"""자동 모드 progress 정리 로직 단위 테스트."""

import json
from pathlib import Path
from unittest.mock import patch

from src.scraper.models import Course, CourseDetail, LectureItem, LectureType, Week
from src.ui.auto import _load_progress, _save_progress


def _make_lec(url: str, completion: str = "incomplete") -> LectureItem:
    return LectureItem(
        title="test",
        item_url=url,
        lecture_type=LectureType.MOVIE,
        completion=completion,
    )


def test_load_progress_empty(tmp_path: Path):
    """파일이 없으면 빈 set을 반환한다."""
    with patch("src.ui.auto._PROGRESS_FILE", tmp_path / "no_exist.json"):
        assert _load_progress() == set()


def test_load_save_roundtrip(tmp_path: Path):
    """저장 후 로드하면 동일한 데이터를 반환한다."""
    pfile = tmp_path / "progress.json"
    urls = {"https://canvas.ssu.ac.kr/a", "https://canvas.ssu.ac.kr/b"}
    with patch("src.ui.auto._PROGRESS_FILE", pfile):
        _save_progress(urls)
        assert _load_progress() == urls


def test_load_progress_corrupted(tmp_path: Path):
    """파손된 JSON은 빈 set을 반환한다."""
    pfile = tmp_path / "progress.json"
    pfile.write_text("not valid json", encoding="utf-8")
    with patch("src.ui.auto._PROGRESS_FILE", pfile):
        assert _load_progress() == set()


def test_stale_progress_removed(tmp_path: Path):
    """LMS에서 여전히 미완료인 강의는 progress에서 제거되어야 한다."""
    pfile = tmp_path / "progress.json"
    # 3개 URL이 처리 완료로 기록됨
    completed_urls = {
        "https://canvas.ssu.ac.kr/courses/1/modules/items/100",
        "https://canvas.ssu.ac.kr/courses/1/modules/items/200",
        "https://canvas.ssu.ac.kr/courses/1/modules/items/300",
    }
    pfile.write_text(json.dumps(sorted(completed_urls)), encoding="utf-8")

    # LMS에서는 items/100만 여전히 미완료 (needs_watch=True)
    lec_still_incomplete = _make_lec("/courses/1/modules/items/100", "incomplete")
    lec_actually_done = _make_lec("/courses/1/modules/items/200", "completed")
    lec_also_done = _make_lec("/courses/1/modules/items/300", "completed")

    # 시뮬레이션: auto.py의 로직 재현
    with patch("src.ui.auto._PROGRESS_FILE", pfile):
        completed = _load_progress()
        still_incomplete: set[str] = set()

        all_lecs = [lec_still_incomplete, lec_actually_done, lec_also_done]
        pending = []
        for lec in all_lecs:
            if lec.needs_watch:
                if lec.full_url in completed:
                    still_incomplete.add(lec.full_url)
                pending.append(lec)

        stale = completed & still_incomplete
        if stale:
            completed -= stale
            _save_progress(completed)

        # items/100은 LMS 미완료 → progress에서 제거됨 → pending에 포함
        assert lec_still_incomplete.full_url not in completed
        assert len(pending) == 1
        assert pending[0] is lec_still_incomplete

        # items/200, 300은 LMS 완료 → progress에 유지
        assert lec_actually_done.full_url in completed
        assert lec_also_done.full_url in completed

        # 파일에도 반영됨
        saved = _load_progress()
        assert lec_still_incomplete.full_url not in saved

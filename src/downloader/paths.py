"""강의 다운로드 경로 계산 — 서비스/UI 레이어가 공통으로 사용하는 순수 함수 모음.

`LectureItem`에 메서드로 추가하면 scraper → downloader 역방향 의존이 되어
`src/scraper/models.py`가 이 파일을 import해야 한다. 그래서 모델 대신 downloader 레이어에
두고, 호출자(service/ui)가 lecture + 과목 + 경로를 넘기도록 한다.

경로 구조 `과목명/N주차/강의명.mp4`는 `make_filepath`가 단일 소스 오브 트루스.

BUG-7 (course id fallback): LMS 가 학기 도중 `course.long_name`을 가공된 cohort
코드 등으로 변경하면 디렉토리 매칭이 mismatch 가 되어 "재다운로드" 무한 루프나
"이미 받은 파일을 못 찾는" drift 가 발생할 수 있다. 마이그레이션 없이 안전하게
방어하기 위해, 디렉토리 안에 `.course_id` 마커 파일을 두고 long_name 매칭이
실패하면 마커 기반으로 fallback. 마커 stamp 는 long_name 매칭이 성공한 디렉토리에
opportunistic 으로 추가되어 다음 cycle 부터 fallback 가능.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.scraper.models import Course, LectureItem


_COURSE_ID_MARKER = ".course_id"


def _sanitize_segment(name: str) -> str:
    """경로 segment 한 단계의 이름 새니타이즈.

    `video_downloader._sanitize_filename` 과 같은 규칙을 사용해야 디렉토리/파일 이름이
    일관된다. video_downloader 가 playwright 의존이라 host 단위 테스트에서 import
    불가능하므로 동일 규칙을 paths.py 에도 두되, 두 곳이 drift 하지 않도록
    동일 정규식을 사용한다 (둘 중 하나만 변경하면 디렉토리 매칭이 깨지므로 함께 수정).
    """
    sanitized = re.sub(r'[<>:"/\\|?*]', "", name)
    sanitized = re.sub(r"\.{2,}", "", sanitized)
    sanitized = sanitized.strip(" .")
    sanitized = re.sub(r"\s+", " ", sanitized)
    return sanitized or "lecture"


def _stamp_course_id_marker(course_dir: Path, course_id: str) -> None:
    """디렉토리에 `.course_id` 마커 파일 생성 (idempotent, 추가만).

    이미 마커가 있으면 no-op. 권한 등으로 쓰기 실패하면 silently 무시 — fallback
    효과만 약화될 뿐 다운로드 본 흐름은 영향 없음.
    """
    marker = course_dir / _COURSE_ID_MARKER
    if marker.exists():
        return
    try:
        marker.write_text(course_id, encoding="utf-8")
    except OSError:
        pass


def _find_course_dir(
    download_dir: Path,
    course_long_name: str,
    course_id: str,
) -> Path:
    """course 의 디렉토리 경로 결정.

    1차: `_sanitize_segment(course_long_name)` 기반. 존재하면 마커 stamp + 반환.
    2차: `.course_id` 마커가 동일 course_id 인 디렉토리 검색 (longName 변경 fallback).
    3차: 1차 경로 반환 (호출자가 다운로드 시점에 mkdir).

    부수효과: 1차 매치 시 마커가 자동으로 추가됨 (idempotent).
    """
    primary_name = _sanitize_segment(course_long_name)
    primary = download_dir / primary_name

    if primary.exists() and primary.is_dir():
        _stamp_course_id_marker(primary, course_id)
        return primary

    # 2차: 마커 기반 fallback (longName 변경 후 디렉토리 매칭)
    if download_dir.is_dir():
        for d in download_dir.iterdir():
            if not d.is_dir():
                continue
            marker = d / _COURSE_ID_MARKER
            if not marker.exists():
                continue
            try:
                if marker.read_text(encoding="utf-8").strip() == str(course_id):
                    return d
            except OSError:
                continue

    # 3차: 새로 생성될 경로 (호출자가 mkdir)
    return primary


def _week_segment(week_label: str) -> str:
    """`6주차(총 8주 중)` → `6주차` 같은 규칙. 숫자주차 없으면 sanitize 결과 또는 '기타'."""
    week_match = re.match(r"(\d+주차)", week_label or "")
    if week_match:
        return week_match.group(1)
    sanitized = _sanitize_segment(week_label or "")
    return sanitized or "기타"


def expected_paths(
    download_dir: str | Path,
    course: Course,
    lec: LectureItem,
) -> tuple[Path, Path]:
    """`(mp4, mp3)` 절대 경로 튜플.

    course.long_name 기반 디렉토리가 우선이지만, LMS longName 변경 시
    `.course_id` 마커 기반 fallback 으로 기존 디렉토리를 발견한다 (BUG-7).
    """
    course_dir = _find_course_dir(Path(download_dir), course.long_name, str(course.id))
    week_dir = _week_segment(lec.week_label)
    title = _sanitize_segment(lec.title)
    mp4 = (course_dir / week_dir / f"{title}.mp4").resolve()
    mp3 = mp4.with_suffix(".mp3")
    return mp4, mp3


def file_present(
    download_dir: str | Path,
    course: Course,
    lec: LectureItem,
    rule: str,
) -> bool:
    """DOWNLOAD_RULE에 따라 기대되는 파일이 모두 존재하는지 확인한다."""
    mp4, mp3 = expected_paths(download_dir, course, lec)
    if rule == "video":
        return mp4.exists()
    if rule == "audio":
        return mp3.exists()
    if rule == "both":
        return mp4.exists() and mp3.exists()
    # 규칙 미설정 — 둘 중 하나만 있어도 present 간주
    return mp4.exists() or mp3.exists()

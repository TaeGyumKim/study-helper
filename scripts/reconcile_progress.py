"""auto_progress.json 과 파일시스템 간 drift 정리 CLI.

LMS 에 로그인해서 현재 강의 목록 + URL 매핑을 불러와 `reconcile_store_with_filesystem`
을 실행한다. 과거 recover/auto 가 서로 다른 상태 소스를 쓰면서 쌓인 drift (파일은
있지만 reason=suspicious_stub 남아있음, learningx 인데 downloadable=None 등) 를
한 방에 정정한다.

사용법:
    python -m scripts.reconcile_progress            # dry-run
    python -m scripts.reconcile_progress --apply    # 실제 수정

환경변수:
    FFMPEG_PATH                ffmpeg 실행파일 또는 bin 디렉토리
    STUDY_HELPER_DOWNLOAD_DIR  다운로드 루트 override
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from collections import Counter

# ffmpeg 동적 탐지 (recover_missing.py 와 동일 정책)
_ffmpeg_env = os.environ.get("FFMPEG_PATH", "").strip()
if _ffmpeg_env:
    _candidate = _ffmpeg_env
    if os.path.isfile(_candidate):
        _candidate = os.path.dirname(_candidate)
    if os.path.isdir(_candidate):
        os.environ["PATH"] = _candidate + os.pathsep + os.environ.get("PATH", "")
elif not shutil.which("ffmpeg"):
    print("[경고] ffmpeg 를 찾을 수 없습니다. (reconcile 자체엔 영향 없으나 환경 정합성 확인용)")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# sys.path 주입 후 src.* import 해야 하므로 E402 비활성
from src.config import Config, get_data_path  # noqa: E402
from src.logger import get_logger  # noqa: E402
from src.scraper.course_scraper import CourseScraper  # noqa: E402
from src.service.download_state import reconcile_store_with_filesystem  # noqa: E402
from src.service.progress_store import ProgressStore  # noqa: E402

_log = get_logger("reconcile_progress_cli")


def _print_store_summary(store: ProgressStore, label: str) -> None:
    total = len(store.entries)
    played = sum(1 for e in store.entries.values() if e.played)
    downloaded = sum(1 for e in store.entries.values() if e.downloaded is True)
    failed = sum(1 for e in store.entries.values() if e.downloaded is False)
    unsupported = sum(1 for e in store.entries.values() if e.downloadable is False)

    reasons: Counter[str] = Counter()
    for e in store.entries.values():
        if e.reason:
            reasons[e.reason] += 1

    print(f"── {label} ──")
    print(f"  entries: {total}")
    print(f"  played={played}  downloaded={downloaded}  failed={failed}  unsupported={unsupported}")
    if reasons:
        print("  reason 분포:")
        for r, n in reasons.most_common():
            print(f"    - {r}: {n}")


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="auto_progress.json 과 파일시스템 drift 정리 (LMS 기반)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="실제 store 를 수정한다. 미지정 시 dry-run.",
    )
    args = parser.parse_args()

    if not Config.has_credentials():
        print("[오류] LMS 자격증명이 설정되지 않았습니다. .env 를 확인하세요.")
        return 1

    # download_dir 우선순위: env > Config
    env_override = os.environ.get("STUDY_HELPER_DOWNLOAD_DIR", "").strip()
    if env_override:
        Config.DOWNLOAD_DIR = env_override
    download_dir = Config.get_download_dir()
    rule = Config.DOWNLOAD_RULE or "both"

    store_path = get_data_path("auto_progress.json")
    print(f"  store:     {store_path}")
    print(f"  downloads: {download_dir}")
    print(f"  rule:      {rule}")
    print()

    store = ProgressStore(path=store_path)
    store.load()
    _print_store_summary(store, "BEFORE")
    print()

    scraper = CourseScraper(username=Config.LMS_USER_ID, password=Config.LMS_PASSWORD)
    try:
        print("  LMS 로그인 중...")
        await scraper.start()
        print("  → 로그인 완료")
        courses = await scraper.fetch_courses()
        print(f"  과목 {len(courses)}개 강의 정보 로딩 중...")
        details = await scraper.fetch_all_details(courses, concurrency=3)
    finally:
        await scraper.close()
    print()

    # dry-run 의 경우 store 복제본에 적용해 변화량 파악, 원본은 미수정
    if args.apply:
        target_store = store
    else:
        # 깊은 복사 대신 별도 인스턴스 로드 (같은 파일에서 다시 읽기)
        target_store = ProgressStore(path=store_path)
        target_store.load()

    unsupported_n, confirmed_n = reconcile_store_with_filesystem(
        courses, details, target_store,
        download_dir=download_dir, rule=rule,
    )

    if args.apply:
        try:
            target_store.save()
        except Exception as e:
            print(f"[ERROR] store.save 실패: {e}", file=sys.stderr)
            return 1
        print(f"APPLIED: unsupported 정정 {unsupported_n}건, downloaded 확정 {confirmed_n}건")
    else:
        print(f"DRY-RUN: unsupported 정정 {unsupported_n}건, downloaded 확정 {confirmed_n}건 예상")
        print("         --apply 로 실제 반영")
    print()

    _print_store_summary(target_store, "AFTER")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

"""ProgressStore 로드/저장/마이그레이션/상태 전이 단위 테스트."""

import json
from pathlib import Path

from src.service.progress_store import ProgressEntry, ProgressStore


def _new_store(tmp_path: Path) -> ProgressStore:
    return ProgressStore(path=tmp_path / "auto_progress.json")


def test_load_missing_file(tmp_path: Path):
    """파일이 없으면 빈 entries 로 초기화된다."""
    store = _new_store(tmp_path)
    store.load()
    assert store.entries == {}


def test_load_corrupted_json(tmp_path: Path):
    """파손된 JSON은 빈 entries 로 안전 복구된다."""
    store = _new_store(tmp_path)
    store.path.write_text("not valid json", encoding="utf-8")
    store.load()
    assert store.entries == {}


def test_v1_to_v2_migration(tmp_path: Path):
    """v1 legacy 리스트 포맷은 played=True, downloaded=None 으로 마이그레이션된다."""
    store = _new_store(tmp_path)
    v1_urls = ["https://canvas.ssu.ac.kr/a", "https://canvas.ssu.ac.kr/b"]
    store.path.write_text(json.dumps(v1_urls), encoding="utf-8")
    store.load()
    assert set(store.entries.keys()) == set(v1_urls)
    for url in v1_urls:
        entry = store.entries[url]
        assert entry.played is True
        assert entry.downloaded is None
        assert entry.downloadable is None


def test_v2_roundtrip(tmp_path: Path):
    """v2 저장 후 로드하면 동일 상태로 복원된다."""
    store = _new_store(tmp_path)
    url = "https://canvas.ssu.ac.kr/courses/1/modules/items/100"
    store.mark_played(url)
    store.mark_download_success(url)
    store.save()

    reloaded = _new_store(tmp_path)
    reloaded.load()
    entry = reloaded.get(url)
    assert entry is not None
    assert entry.played is True
    assert entry.downloaded is True
    assert entry.downloadable is True


def test_unknown_format_fallback(tmp_path: Path):
    """version 키가 없거나 알 수 없는 포맷은 빈 entries 로 안전 복구된다."""
    store = _new_store(tmp_path)
    store.path.write_text(json.dumps({"some": "garbage"}), encoding="utf-8")
    store.load()
    assert store.entries == {}


def test_mark_played_then_failed(tmp_path: Path):
    """재생 완료 후 다운로드 실패 시 reason 이 기록되고 downloaded=False."""
    store = _new_store(tmp_path)
    url = "u1"
    store.mark_played(url)
    store.mark_download_failed(url, reason="network")
    entry = store.get(url)
    assert entry.played is True
    assert entry.downloaded is False
    assert entry.downloadable is True
    assert entry.reason == "network"


def test_mark_unsupported(tmp_path: Path):
    """구조적 다운로드 불가(learningx 등) 항목은 downloadable=False 로 고정."""
    store = _new_store(tmp_path)
    store.mark_unsupported("u2", reason="unsupported")
    entry = store.get("u2")
    assert entry.downloadable is False
    assert entry.downloaded is False
    assert entry.reason == "unsupported"


def test_is_fully_done(tmp_path: Path):
    """재생 완료 + (다운로드 완료 OR 다운로드 불가)이면 True."""
    store = _new_store(tmp_path)
    # case A: 재생 + 다운로드 완료
    store.mark_played("a")
    store.mark_download_success("a")
    assert store.is_fully_done("a") is True

    # case B: 재생 + 구조적 다운로드 불가
    store.mark_unsupported("b")
    assert store.is_fully_done("b") is True

    # case C: 재생만 완료, 다운로드 미완
    store.mark_played("c")
    assert store.is_fully_done("c") is False

    # case D: 미존재
    assert store.is_fully_done("nonexistent") is False


def test_needs_download_retry(tmp_path: Path):
    """재생 완료 + downloadable≠False + downloaded≠True 이면 재시도 대상."""
    store = _new_store(tmp_path)
    store.mark_played("x")
    store.mark_download_failed("x", reason="network")
    assert store.needs_download_retry("x") is True

    store.mark_download_success("x")
    assert store.needs_download_retry("x") is False


def test_retain_only_removes_orphans(tmp_path: Path):
    """LMS 에서 사라진 URL 은 store 에서도 정리된다."""
    store = _new_store(tmp_path)
    for url in ("keep1", "keep2", "orphan"):
        store.mark_played(url)

    removed = store.retain_only({"keep1", "keep2"})
    assert removed == 1
    assert set(store.entries.keys()) == {"keep1", "keep2"}


def test_retain_only_empty_set_is_safe(tmp_path: Path):
    """BUG-2 안전망: 빈 set 으로 호출되면 catastrophic delete 대신 0 반환.

    호출자(auto.py)가 fetch 부분 실패 가드를 거치는 것이 1차 방어선이지만,
    회귀로 빈 set 이 흘러들어와도 store 가 통째로 비워지지 않게 한다.
    """
    store = _new_store(tmp_path)
    for url in ("a", "b", "c"):
        store.mark_played(url)
        store.mark_download_success(url)

    removed = store.retain_only(set())
    assert removed == 0
    assert set(store.entries.keys()) == {"a", "b", "c"}


def test_mark_incomplete_resets_played(tmp_path: Path):
    """LMS가 항목을 다시 미완료로 바꾸면 played=False 및 downloaded=None 복귀."""
    store = _new_store(tmp_path)
    store.entries["u"] = ProgressEntry(played=True, downloaded=True, downloadable=True)
    store.mark_incomplete("u")
    entry = store.get("u")
    assert entry.played is False
    assert entry.downloaded is None


def test_save_atomic_no_tmp_remains(tmp_path: Path):
    """save() 성공 후 .tmp 파일이 남지 않는다 (atomic replace 확인)."""
    store = _new_store(tmp_path)
    store.mark_played("u")
    store.save()
    tmp_file = store.path.with_suffix(store.path.suffix + ".tmp")
    assert not tmp_file.exists()
    assert store.path.exists()

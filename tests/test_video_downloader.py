"""video_downloader 의 stub URL 필터링 및 재시도 정책 regression 테스트.

2026-04-18 관측된 활성 버그: intro.mp4 / preloader.mp4 stub URL 이 Plan A DOM
폴링에서 실제 영상 URL 로 오인되어 재시도 불가 처리로 강의가 영구 누락됨.
이 테스트는 해당 fix 의 회귀 방지를 담당한다.
"""

import pytest

from src.downloader.result import (
    REASON_PATH_INVALID,
    REASON_SSRF_BLOCKED,
    REASON_SUSPICIOUS_STUB,
    REASON_UNKNOWN,
    REASON_UNSUPPORTED,
    REASON_URL_EXTRACT_FAILED,
    SSRFBlockedError,
    SuspiciousStubError,
    is_no_retry_reason,
)
from src.downloader.video_downloader import (
    _sanitize_filename,
    _validate_media_url,
    make_filepath,
)

# ── L1 regression: stub URL 사전 차단 ────────────────────────────

class TestStubUrlFiltering:
    """Plan A DOM 폴링 + Plan B 네트워크 후킹 양쪽에서 stub 패턴이 제외되는지 검증.

    extract_video_url 내부의 클로저 _is_valid_mp4 를 직접 호출할 수 없으므로
    동일 exclude_patterns 튜플을 재구성해 로직을 재검증한다. 실제 함수 수정
    시 이 튜플도 함께 업데이트되어야 한다.
    """

    # extract_video_url 내부 클로저와 동일해야 하는 패턴
    _EXCLUDE_PATTERNS = ("preloader.mp4", "preview.mp4", "thumbnail.mp4", "intro.mp4")

    @staticmethod
    def _is_valid_mp4(url: str, patterns: tuple[str, ...]) -> bool:
        return ".mp4" in url and not any(p in url for p in patterns)

    def test_intro_mp4_rejected(self):
        """BUG-FIX: intro.mp4 stub URL 이 exclude 되어야 한다 (2026-04-18 관측)."""
        url = "https://commons.ssu.ac.kr/settings/viewer/uniplayer/intro.mp4"
        assert not self._is_valid_mp4(url, self._EXCLUDE_PATTERNS)

    def test_preloader_mp4_rejected(self):
        url = "https://commons.ssu.ac.kr/viewer/uniplayer/preloader.mp4"
        assert not self._is_valid_mp4(url, self._EXCLUDE_PATTERNS)

    def test_preview_mp4_rejected(self):
        assert not self._is_valid_mp4("https://commons.ssu.ac.kr/x/preview.mp4", self._EXCLUDE_PATTERNS)

    def test_thumbnail_mp4_rejected(self):
        assert not self._is_valid_mp4("https://x/thumbnail.mp4", self._EXCLUDE_PATTERNS)

    def test_real_video_accepted(self):
        """main_* 로 시작하는 실제 CDN URL 은 허용되어야 한다."""
        url = "https://ssu-toast.commonscdn.com/contents31/ssu1000001/abc/contents/media_files/main_(uuid).mp4"
        assert self._is_valid_mp4(url, self._EXCLUDE_PATTERNS)

    def test_non_mp4_rejected(self):
        assert not self._is_valid_mp4("https://x/file.m3u8", self._EXCLUDE_PATTERNS)


# ── 재시도 정책 (L1 두 번째 fix) ──────────────────────────────

class TestIsNoRetryReason:
    """SUSPICIOUS_STUB 은 재시도 허용 대상임을 명시적으로 검증."""

    def test_unsupported_is_no_retry(self):
        assert is_no_retry_reason(REASON_UNSUPPORTED) is True

    def test_path_invalid_is_no_retry(self):
        assert is_no_retry_reason(REASON_PATH_INVALID) is True

    def test_ssrf_blocked_is_no_retry(self):
        assert is_no_retry_reason(REASON_SSRF_BLOCKED) is True

    def test_suspicious_stub_is_retriable(self):
        """BUG-FIX: DOM 타이밍 이슈라 재시도로 회복 가능해야 한다."""
        assert is_no_retry_reason(REASON_SUSPICIOUS_STUB) is False

    def test_url_extract_failed_is_retriable(self):
        assert is_no_retry_reason(REASON_URL_EXTRACT_FAILED) is False

    def test_unknown_is_retriable(self):
        assert is_no_retry_reason(REASON_UNKNOWN) is False

    def test_none_or_empty_is_retriable(self):
        """분류 실패 시 안전하게 재시도 대상으로 간주."""
        assert is_no_retry_reason(None) is False
        assert is_no_retry_reason("") is False


# ── SSRF 방어 ────────────────────────────────────────────────

class TestValidateMediaUrl:
    def test_allowed_ssu_host(self):
        _validate_media_url("https://commons.ssu.ac.kr/x/main.mp4")  # no raise

    def test_allowed_toast_cdn(self):
        _validate_media_url("https://ssu-toast.commonscdn.com/x/main.mp4")

    def test_disallowed_host_raises(self):
        with pytest.raises(SSRFBlockedError):
            _validate_media_url("https://evil.example.com/video.mp4")

    def test_disallowed_scheme_raises(self):
        with pytest.raises(SSRFBlockedError):
            _validate_media_url("file:///etc/passwd")

    def test_ftp_scheme_raises(self):
        with pytest.raises(SSRFBlockedError):
            _validate_media_url("ftp://commons.ssu.ac.kr/x.mp4")


# ── 파일명 sanitization ───────────────────────────────────────

class TestSanitizeFilename:
    def test_strip_invalid_chars(self):
        """Windows 금지 문자가 모두 제거된다."""
        assert _sanitize_filename('a<b>c:"d/e\\f|g?h*i') == "abcdefghi"

    def test_strip_parent_traversal(self):
        """연속된 `..` 는 제거된다 (디렉토리 순회 방지)."""
        assert ".." not in _sanitize_filename("../../evil")

    def test_trailing_dots_stripped(self):
        """Windows 는 trailing dot 금지."""
        assert not _sanitize_filename("lecture...").endswith(".")

    def test_korean_preserved(self):
        assert "디지털스토리텔링" in _sanitize_filename("디지털스토리텔링(2150012601)")

    def test_empty_fallback(self):
        """완전히 비면 'lecture' 로 대체."""
        assert _sanitize_filename("...") == "lecture"
        assert _sanitize_filename("///") == "lecture"


# ── 경로 생성 ────────────────────────────────────────────────

class TestMakeFilepath:
    def test_basic_structure(self):
        """과목명/N주차/강의명.mp4 구조."""
        p = make_filepath("수학", "1주차(총 8주 중)", "미적분 입문")
        assert p.parts == ("수학", "1주차", "미적분 입문.mp4")

    def test_week_label_without_prefix(self):
        """주차 패턴 없는 라벨은 sanitize 후 그대로 사용."""
        p = make_filepath("수학", "공지사항", "개강 안내")
        assert p.parts == ("수학", "공지사항", "개강 안내.mp4")

    def test_empty_week_fallback(self):
        """빈 week_label 은 _sanitize_filename 의 'lecture' 기본값으로 fallback.

        make_filepath 내부 `_sanitize_filename(...) or "기타"` 구문은 현재
        _sanitize_filename 자체에 "lecture" fallback 이 있어 실질적으로
        "기타" 로 떨어지지 않음. 이 사실을 테스트로 고정해 미래 변경 시 drift
        감지 포인트로 사용.
        """
        p = make_filepath("수학", "", "강의")
        assert p.parts[1] == "lecture"


# ── 예외 계층 ────────────────────────────────────────────────

class TestExceptions:
    def test_ssrf_is_value_error(self):
        """SSRFBlockedError 는 ValueError 서브클래스 — 기존 except ValueError 에 걸림."""
        assert issubclass(SSRFBlockedError, ValueError)

    def test_suspicious_stub_is_runtime_error(self):
        assert issubclass(SuspiciousStubError, RuntimeError)

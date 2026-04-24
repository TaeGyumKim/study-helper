"""LOG-SYS-3 회귀 방지: SensitiveFilter 가 PII/OAuth 를 마스킹하는지."""

from __future__ import annotations

import logging

from src.logger import SensitiveFilter


def _make_record(msg: str, args: tuple | dict | None = None) -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="x",
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


def test_filter_masks_plain_kv() -> None:
    f = SensitiveFilter()
    record = _make_record("user_email=foo@bar.com extra")
    f.filter(record)
    assert "foo@bar.com" not in record.msg
    assert "REDACTED" in record.msg


def test_filter_masks_urlencoded_kv() -> None:
    f = SensitiveFilter()
    record = _make_record("oauth_signature%3DabCdEf123 trailing")
    f.filter(record)
    assert "abCdEf123" not in record.msg


def test_filter_masks_args_tuple() -> None:
    f = SensitiveFilter()
    record = _make_record("body=%s", ("user_email=x@y.com",))
    f.filter(record)
    assert "x@y.com" not in record.args[0]


def test_filter_idempotent() -> None:
    """이미 마스킹된 값은 재적용해도 안전해야 한다."""
    f = SensitiveFilter()
    once = "***REDACTED***"
    record = _make_record(once)
    f.filter(record)
    assert record.msg == once


def test_filter_passes_through_non_sensitive() -> None:
    f = SensitiveFilter()
    original = "safe message with no secrets"
    record = _make_record(original)
    f.filter(record)
    assert record.msg == original

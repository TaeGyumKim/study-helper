"""pytest 공용 fixture."""

from pathlib import Path

import pytest


@pytest.fixture
def tmp_downloads_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """테스트용 임시 다운로드 디렉토리.

    STUDY_HELPER_DATA_DIR / DOWNLOAD_DIR 환경변수를 tmp_path 로 설정해
    실제 사용자 디렉토리를 오염시키지 않는다.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("STUDY_HELPER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("DOWNLOAD_DIR", str(data_dir / "downloads"))
    return data_dir

"""STY-002/003 일부 해소: fake webm 생성을 background_player 에서 분리.

Chromium headless 가 H.264 를 미지원하므로 commonscdn MP4 요청을
VP8/WebM 더미 영상으로 교체해 Plan A(video DOM 폴링) 를 성립시킨다.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path


async def create_fake_webm(duration_sec: float) -> bytes:
    """VP8 WebM 더미 영상 생성 (Chromium H.264 미지원 우회).

    TemporaryDirectory 사용 — context manager 종료 시 자동 삭제.
    2x2 픽셀 검정 프레임, 1fps, 극소 용량.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "fake.webm"
        dur = str(int(duration_sec) + 2)
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=2x2:r=1",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=8000:cl=mono",
            "-t",
            dur,
            "-c:v",
            "libvpx",
            "-b:v",
            "1k",
            "-c:a",
            "libopus",
            "-b:a",
            "8k",
            "-map",
            "0:v",
            "-map",
            "1:a",
            str(output_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            # SEC-010: subprocess 에 민감 env 상속 차단 — PATH 만 전달.
            env={"PATH": os.environ.get("PATH", "")},
        )
        await proc.communicate()
        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError("ffmpeg 더미 영상 생성 실패")
        return output_path.read_bytes()

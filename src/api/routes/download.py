"""다운로드/변환/STT/요약 엔드포인트."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from src.config import Config
from src.service.download_pipeline import (
    PipelineProgress,
    convert_to_audio,
    resolve_download_path,
    run_pipeline,
    summarize_text,
    transcribe_audio,
)

router = APIRouter()


class ConvertRequest(BaseModel):
    mp4_path: str
    delete_original: bool = False


class TranscribeRequest(BaseModel):
    audio_path: str
    model_size: str = "base"
    language: str = "ko"


class SummarizeRequest(BaseModel):
    txt_path: str
    agent: str = "gemini"
    api_key: str = ""
    model: str = ""
    extra_prompt: str = ""


class PipelineRequest(BaseModel):
    mp4_path: str
    course_name: str
    week_label: str = ""
    lecture_title: str = ""
    audio_only: bool = False
    both: bool = False


class ResolvePathRequest(BaseModel):
    course_name: str
    week_label: str = ""
    lecture_title: str = ""


@router.post("/resolve-path")
def resolve_path(body: ResolvePathRequest):
    """다운로드 경로를 결정한다."""
    download_dir = Config.get_download_dir()
    result = resolve_download_path(download_dir, body.course_name, body.week_label, body.lecture_title)
    if result is None:
        return {"error": "잘못된 경로"}
    return {"path": str(result)}


@router.post("/convert")
def convert(body: ConvertRequest):
    """mp4를 mp3로 변환한다."""
    mp3_path = convert_to_audio(Path(body.mp4_path), delete_original=body.delete_original)
    return {"mp3_path": str(mp3_path)}


@router.post("/transcribe")
async def transcribe(body: TranscribeRequest):
    """음성을 텍스트로 변환한다 (STT)."""
    loop = asyncio.get_running_loop()
    txt_path = await loop.run_in_executor(
        None,
        lambda: transcribe_audio(Path(body.audio_path), model_size=body.model_size, language=body.language),
    )
    return {"txt_path": str(txt_path)}


@router.post("/summarize")
async def summarize(body: SummarizeRequest):
    """텍스트를 AI로 요약한다."""
    loop = asyncio.get_running_loop()
    summary_path = await loop.run_in_executor(
        None,
        lambda: summarize_text(
            Path(body.txt_path),
            agent=body.agent,
            api_key=body.api_key,
            model=body.model,
            extra_prompt=body.extra_prompt,
        ),
    )
    return {"summary_path": str(summary_path)}


@router.websocket("/pipeline")
async def pipeline_ws(ws: WebSocket):
    """다운로드 후속 파이프라인을 WebSocket으로 실행한다.

    클라이언트가 JSON으로 PipelineRequest를 보내면,
    각 단계의 진행 상태를 JSON 메시지로 스트리밍한다.
    """
    await ws.accept()
    try:
        data = await ws.receive_json()
        req = PipelineRequest(**data)

        async def _on_progress(p: PipelineProgress):
            await ws.send_json(
                {
                    "type": "progress",
                    "stage": p.stage.value,
                    "progress": p.progress,
                    "current": p.current,
                    "total": p.total,
                    "message": p.message,
                }
            )

        # Config에서 설정 로드
        tg = Config.get_telegram_credentials()
        result = await run_pipeline(
            mp4_path=Path(req.mp4_path),
            course_name=req.course_name,
            week_label=req.week_label,
            lecture_title=req.lecture_title,
            audio_only=req.audio_only,
            both=req.both,
            stt_enabled=Config.STT_ENABLED == "true",
            stt_model=Config.WHISPER_MODEL or "base",
            stt_language=Config.STT_LANGUAGE,
            ai_enabled=Config.AI_ENABLED == "true",
            ai_agent=Config.AI_AGENT or "gemini",
            ai_api_key=Config.GOOGLE_API_KEY if Config.AI_AGENT == "gemini" else Config.OPENAI_API_KEY,
            ai_model=Config.GEMINI_MODEL,
            ai_extra_prompt=Config.SUMMARY_PROMPT_EXTRA,
            tg_token=tg[0] if tg else "",
            tg_chat_id=tg[1] if tg else "",
            tg_auto_delete=Config.TELEGRAM_AUTO_DELETE == "true",
            on_progress=_on_progress,
        )

        await ws.send_json(
            {
                "type": "complete",
                "success": result.success,
                "mp4_path": str(result.mp4_path) if result.mp4_path else None,
                "mp3_path": str(result.mp3_path) if result.mp3_path else None,
                "txt_path": str(result.txt_path) if result.txt_path else None,
                "summary_path": str(result.summary_path) if result.summary_path else None,
                "error": result.error,
                "stage_errors": result.stage_errors,
            }
        )
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass

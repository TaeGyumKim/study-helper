"""
백그라운드 재생 모듈.

LMS 강의 페이지를 headless 브라우저로 열고, 영상을 재생하여
LMS가 수강 완료로 인식하도록 실제 재생 시간을 유지한다.

재생 흐름:
1. 강의 페이지 이동 (item_url)
2. 중첩 iframe 탐색 (tool_content → commons.ssu.ac.kr)
3. 이어보기 다이얼로그 자동 처리 (처음부터 재생)
4. 재생 버튼 클릭 후 video 요소의 currentTime / duration 폴링
5. 영상 끝날 때까지 진행 콜백 호출
"""

import asyncio
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlparse, parse_qs, unquote

from playwright.async_api import Page, Frame


# ── 상수 ─────────────────────────────────────────────────────────
_POLL_INTERVAL = 1.0          # 진행 폴링 주기 (초)
_FRAME_FIND_TIMEOUT = 30      # iframe 탐색 최대 대기 (초)
_PLAY_TIMEOUT = 20            # 재생 버튼/영상 시작 대기 (초)
_END_THRESHOLD = 3            # 영상 끝 판정 여유 (초)
_RESUME_BTN = ".confirm-ok-btn"
_RESTART_BTN = ".confirm-cancel-btn"
_DIALOG_SEL = ".confirm-msg-box"
_PLAY_BTN = ".vc-front-screen-play-btn"
_VIDEO_SEL = "video.vc-vplay-video1"


@dataclass
class PlaybackState:
    current: float = 0.0   # 현재 재생 위치 (초)
    duration: float = 0.0  # 전체 길이 (초)
    ended: bool = False
    error: Optional[str] = None


# ── 내부 헬퍼 ────────────────────────────────────────────────────

async def _find_player_frame(page: Page) -> Optional[Frame]:
    """
    tool_content 아래 commons.ssu.ac.kr frame을 찾는다.
    재생 버튼이 있는 초기 플레이어 선택 화면 frame.
    flashErrorPage는 제외한다.
    """
    for _ in range(_FRAME_FIND_TIMEOUT):
        outer = page.frame(name="tool_content")
        if outer:
            for frame in page.frames:
                if (frame.parent_frame == outer
                        and "commons.ssu.ac.kr" in frame.url
                        and "flashErrorPage" not in frame.url):
                    return frame
        await asyncio.sleep(1)
    return None


async def _find_video_frame(page: Page) -> Optional[Frame]:
    """
    실제 video 태그가 있는 frame을 찾는다.
    재생 버튼 클릭 후 page 전체를 재스캔한다.

    commons.ssu.ac.kr에 속한 모든 frame (flashErrorPage 포함) 중
    video 태그가 존재하는 것을 반환한다.
    flashErrorPage 자체가 HTML5 video를 동적으로 생성할 수 있으므로 포함한다.
    """
    for _ in range(_FRAME_FIND_TIMEOUT):
        for frame in page.frames:
            if "commons.ssu.ac.kr" not in frame.url:
                continue
            try:
                count = await frame.evaluate(
                    "() => document.querySelectorAll('video').length"
                )
                if count > 0:
                    return frame
            except Exception:
                pass
        await asyncio.sleep(1)
    return None


async def _dismiss_dialog(frame: Frame, restart: bool = True) -> bool:
    """이어보기 다이얼로그가 표시되면 처리한다. 처리했으면 True 반환."""
    try:
        dialog = await frame.query_selector(_DIALOG_SEL)
        if not dialog or not await dialog.is_visible():
            return False
        # 처음부터 재생 (restart=True) 또는 이어보기 (restart=False)
        btn_sel = _RESTART_BTN if restart else _RESUME_BTN
        btn = await frame.query_selector(btn_sel)
        if btn:
            await btn.click()
            return True
    except Exception:
        pass
    return False


async def _click_play(frame: Frame) -> bool:
    """재생 버튼을 클릭한다. 성공 시 True."""
    try:
        btn = await frame.wait_for_selector(_PLAY_BTN, timeout=_PLAY_TIMEOUT * 1000)
        if btn:
            await btn.click()
            return True
    except Exception:
        pass
    return False


async def _get_video_state(frame: Frame) -> Optional[dict]:
    """video 요소의 현재 상태(currentTime, duration, ended, paused)를 반환한다."""
    try:
        return await frame.evaluate(f"""() => {{
            const v = document.querySelector('{_VIDEO_SEL}');
            if (!v) return null;
            return {{
                current: v.currentTime,
                duration: v.duration || 0,
                ended: v.ended,
                paused: v.paused
            }};
        }}""")
    except Exception:
        return None


async def _ensure_playing(frame: Frame):
    """일시정지 상태면 JS로 강제 재생한다."""
    try:
        await frame.evaluate(f"""() => {{
            const v = document.querySelector('{_VIDEO_SEL}');
            if (v && v.paused && !v.ended) v.play();
        }}""")
    except Exception:
        pass


async def _create_fake_webm(duration_sec: float) -> bytes:
    """VP8 WebM 더미 영상 생성 (Chromium H.264 미지원 우회).

    2×2 픽셀 검정 프레임, 1fps, 극소 용량.
    Chromium headless는 H.264를 지원하지 않지만 VP8/WebM은 기본 지원한다.
    commonscdn MP4 요청을 이 영상으로 교체하면 Plan A(video DOM 폴링)가 동작한다.
    """
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "fake.webm")
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=black:s=2x2:r=1",
            "-t", str(int(duration_sec) + 2),
            "-c:v", "libvpx", "-b:v", "0", "-crf", "10", "-an",
            output_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError("ffmpeg 더미 영상 생성 실패")
        with open(output_path, "rb") as f:
            return f.read()


# ── 진도 API 직접 호출 (Plan B) ──────────────────────────────────

def _parse_player_url(player_url: str) -> dict:
    """
    commons.ssu.ac.kr/em/ URL에서 재생 정보를 파싱한다.

    반환:
        {
            "content_id": str,
            "duration": float,       # endat 파라미터 (초)
            "progress_url": str,     # TargetUrl 디코딩값
        }
    """
    parsed = urlparse(player_url)
    qs = parse_qs(parsed.query)

    duration = float(qs.get("endat", ["0"])[0])
    target_url = unquote(qs.get("TargetUrl", [""])[0])

    # content_id는 path의 마지막 세그먼트
    content_id = parsed.path.rstrip("/").split("/")[-1]

    return {
        "content_id": content_id,
        "duration": duration,
        "progress_url": target_url,
    }


async def _call_progress_jsonp(frame: Frame, report_url: str, callback: str) -> str:
    """
    commons 프레임 내부에서 JSONP 스크립트 태그를 주입해 진도 API를 호출한다.

    실제 플레이어(uni-player.min.js)와 동일하게 commons.ssu.ac.kr origin에서
    canvas.ssu.ac.kr progress 엔드포인트를 호출함으로써 ErrAlreadyInView를 우회한다.
    """
    result = await frame.evaluate("""
        (args) => new Promise((resolve) => {
            var url = args[0];
            var cbName = args[1];
            window[cbName] = function(data) {
                delete window[cbName];
                resolve(JSON.stringify(data));
            };
            var s = document.createElement('script');
            s.src = url;
            s.onerror = function() {
                delete window[cbName];
                resolve(JSON.stringify({error: 'script_error'}));
            };
            document.head.appendChild(s);
            setTimeout(function() {
                delete window[cbName];
                resolve(JSON.stringify({error: 'timeout'}));
            }, 10000);
        })
    """, [report_url, callback])
    return result


async def _play_via_progress_api(
    page: Page,
    player_url: str,
    on_progress: Optional[Callable[[PlaybackState], None]],
    log: Callable,
    fallback_duration: float = 0.0,
) -> PlaybackState:
    """
    headless에서 플레이어 로드에 실패할 때 사용하는 Plan B.

    진도 API(TargetUrl)를 주기적으로 호출해서 LMS가 수강 완료로 인식하도록 한다.

    ErrAlreadyInView 우회 전략:
    - sl=1 파라미터로 commons.ssu.ac.kr에 뷰 세션이 등록된 상태에서
      canvas.ssu.ac.kr 컨텍스트에서 직접 progress API를 호출하면 ErrAlreadyInView가 반환됨.
    - commons 프레임(flashErrorPage.html)이 아직 살아있을 때,
      그 프레임 내부에서 JSONP 스크립트 태그를 주입해 호출하면
      실제 플레이어와 동일한 commons.ssu.ac.kr origin으로 요청이 전송되어 우회 가능.
    - commons 프레임이 없으면 대시보드로 이동 후 page.request.get으로 폴백.
    """
    state = PlaybackState()
    info = _parse_player_url(player_url)
    duration = info["duration"]
    progress_url = info["progress_url"]

    if not progress_url:
        log("  [API] TargetUrl 파싱 실패 — 재생 불가")
        state.error = "진도 API URL을 파싱하지 못했습니다."
        return state

    if duration <= 0:
        if fallback_duration > 0:
            log(f"  [API] endat 파라미터 없음 — LectureItem.duration 사용: {fallback_duration:.1f}s")
            duration = fallback_duration
        else:
            log("  [API] duration 파싱 실패 — URL에 endat 파라미터 없음, fallback도 없음")
            state.error = "영상 길이를 알 수 없습니다."
            return state

    # commons 프레임 탐색: flashErrorPage.html을 포함한 모든 commons.ssu.ac.kr 프레임
    # _play_via_progress_api 호출 시점에는 아직 프레임이 살아있음
    commons_frame: Optional[Frame] = None
    for f in page.frames:
        if "commons.ssu.ac.kr" in f.url:
            commons_frame = f
            break

    if commons_frame:
        log(f"  [API] commons 프레임 발견 ({commons_frame.url})")
        log(f"  [API] JSONP 방식으로 진도 보고 (ErrAlreadyInView 우회)")
    else:
        # commons 프레임이 없으면 대시보드로 이동해 플레이어 세션 종료 시도
        log("  [API] commons 프레임 없음 — 대시보드로 이동 후 직접 호출")
        try:
            await page.goto("https://canvas.ssu.ac.kr/", wait_until="networkidle", timeout=15000)
            await asyncio.sleep(2)
        except Exception as e:
            log(f"  [API] 대시보드 이동 실패 (무시하고 계속): {e}")

    log(f"  [API] 진도 API 방식으로 재생 시뮬레이션")
    log(f"  [API] duration={duration:.1f}s  progress_url={progress_url}")

    state.duration = duration
    current = 0.0
    report_interval = 30.0   # 30초마다 진도 보고
    next_report = report_interval

    # 총 페이지 수는 실제 요청에서 totalpage=15로 고정 (LMS 플레이어 기본값)
    total_page = 15

    while current < duration:
        await asyncio.sleep(_POLL_INTERVAL)
        current = min(current + _POLL_INTERVAL, duration)
        state.current = current

        if on_progress:
            on_progress(state)

        # 30초마다 진도 API 호출
        if current >= next_report or current >= duration:
            try:
                import time
                ts = int(time.time() * 1000)
                callback = f"jQuery111_{ts}"

                cumulative_page = total_page if current >= duration else int(current / duration * total_page)
                page_num = min(cumulative_page, total_page)

                sep = "&" if "?" in progress_url else "?"
                report_target = (
                    f"{progress_url}{sep}"
                    f"callback={callback}"
                    f"&state=3"
                    f"&duration={duration}"
                    f"&currentTime={current:.2f}"
                    f"&cumulativeTime={current:.2f}"
                    f"&page={page_num}"
                    f"&totalpage={total_page}"
                    f"&cumulativePage={cumulative_page}"
                    f"&_={ts}"
                )
                log(f"  [API] 진도 보고: {int(current)}s/{int(duration)}s")

                if commons_frame:
                    # commons 프레임 내부에서 JSONP 스크립트 태그 주입
                    try:
                        body = await _call_progress_jsonp(commons_frame, report_target, callback)
                        log(f"  [API] 응답 (JSONP): {body[:200]!r}")
                    except Exception as e:
                        log(f"  [API] JSONP 오류 ({e}) — page.request.get으로 폴백")
                        commons_frame = None
                        # 폴백: page.request.get
                        response = await page.request.get(
                            report_target,
                            headers={"Referer": "https://commons.ssu.ac.kr/"},
                        )
                        body = await response.text()
                        log(f"  [API] 응답 (fallback): {response.status}  body={body[:200]!r}")
                else:
                    response = await page.request.get(
                        report_target,
                        headers={"Referer": "https://commons.ssu.ac.kr/"},
                    )
                    body = await response.text()
                    log(f"  [API] 응답: {response.status}  body={body[:200]!r}")
            except Exception as e:
                log(f"  [API] 진도 보고 실패: {e}")
            next_report = current + report_interval

    state.ended = True
    if on_progress:
        on_progress(state)
    return state


# ── 공개 API ─────────────────────────────────────────────────────

async def _debug_page_state(page: Page, frame: Optional[Frame], log: Callable):
    """현재 페이지/프레임 상태를 상세 출력한다."""
    log(f"  [현재 URL] {page.url}")
    log(f"  [전체 프레임 수] {len(page.frames)}")
    for i, f in enumerate(page.frames):
        parent_name = f.parent_frame.name if f.parent_frame else "root"
        log(f"    frame[{i}] name={f.name!r}  parent={parent_name!r}  url={f.url}")

    # 모든 commons.ssu.ac.kr frame에 대해 video 조회
    log("  [commons frame별 video 조회]")
    for i, f in enumerate(page.frames):
        if "commons.ssu.ac.kr" not in f.url:
            continue
        try:
            all_videos = await f.evaluate("""() => {
                return Array.from(document.querySelectorAll('video')).map(v => ({
                    class: v.className,
                    src: v.src || v.currentSrc || '(없음)',
                    readyState: v.readyState,
                    duration: v.duration,
                    paused: v.paused,
                    error: v.error ? v.error.code : null
                }));
            }""")
            body_html = await f.evaluate("() => document.body ? document.body.innerHTML.slice(0, 500) : '(body 없음)'")
            log(f"    frame[{i}] url={f.url}")
            log(f"      video 수={len(all_videos)}")
            for j, v in enumerate(all_videos):
                log(f"      video[{j}] class={v['class']!r}  src={v['src'][:100]!r}  "
                    f"readyState={v['readyState']}  duration={v['duration']}  "
                    f"paused={v['paused']}  error={v['error']}")
            log(f"      body(첫 500자)={body_html!r}")
        except Exception as e:
            log(f"    frame[{i}] 조회 오류: {e}")

    if frame is None:
        log("  [지정 video frame] 없음")
        return

    log(f"  [지정 video frame] url={frame.url}")

    # 재생 버튼 존재 여부
    try:
        play_btn = await frame.query_selector(_PLAY_BTN)
        log(f"  [재생 버튼] {'있음' if play_btn else '없음'}")
    except Exception as e:
        log(f"  [재생 버튼 조회 오류] {e}")

    # 이어보기 다이얼로그 존재 여부
    try:
        dialog = await frame.query_selector(_DIALOG_SEL)
        visible = await dialog.is_visible() if dialog else False
        log(f"  [이어보기 다이얼로그] {'표시 중' if visible else ('DOM 있음(숨김)' if dialog else '없음')}")
    except Exception as e:
        log(f"  [다이얼로그 조회 오류] {e}")


async def play_lecture(
    page: Page,
    lecture_url: str,
    on_progress: Optional[Callable[[PlaybackState], None]] = None,
    debug: bool = False,
    fallback_duration: float = 0.0,
) -> PlaybackState:
    """
    강의 URL을 headless 브라우저로 재생한다.

    Args:
        page:         CourseScraper가 관리하는 Playwright Page.
        lecture_url:  LectureItem.full_url
        on_progress:  재생 진행 시 주기적으로 호출되는 콜백. PlaybackState 전달.
        debug:        True이면 단계별 진단 로그를 출력한다.

    Returns:
        최종 PlaybackState.
    """
    log = print if debug else (lambda *a, **k: None)
    state = PlaybackState()

    # 0. H.264 우회: VP8 WebM 더미 영상으로 commonscdn MP4 인터셉트
    # Chromium headless(ARM64 포함)는 H.264 미지원 → flashErrorPage.html 로드 → Plan A 실패
    # VP8 WebM을 대신 제공하면 Chromium이 정상 재생 → Plan A 동작 → LTI 세션 내에서 progress 보고
    _using_fake_video = False
    if fallback_duration > 0:
        log(f"[0] H.264 우회: VP8 더미 영상 생성 중 (duration={fallback_duration:.0f}s)...")
        try:
            _fake_video_bytes = await _create_fake_webm(fallback_duration)
            log(f"[0] 더미 영상 생성 완료 ({len(_fake_video_bytes):,} bytes)")

            async def _serve_fake(route, request):
                await route.fulfill(
                    status=200,
                    headers={"Content-Type": "video/webm"},
                    body=_fake_video_bytes,
                )

            await page.route("**/*.mp4", _serve_fake)
            # canPlayType / isTypeSupported 오버라이드:
            # Chromium은 H.264 미지원 → canPlayType("video/mp4; codecs=avc1") = ""
            # 플레이어가 이 값을 보고 MP4 요청 없이 바로 flashErrorPage로 분기.
            # init script로 'probably'를 반환하게 속이면 MP4를 실제로 요청하고,
            # 그 요청을 위 route가 VP8 WebM으로 대체한다.
            await page.add_init_script("""
                (function() {
                    if (window.MediaSource && MediaSource.isTypeSupported) {
                        var _origMSE = MediaSource.isTypeSupported.bind(MediaSource);
                        MediaSource.isTypeSupported = function(type) {
                            if (type && (type.indexOf('avc') !== -1 || type.indexOf('mp4') !== -1)) return true;
                            return _origMSE(type);
                        };
                    }
                    var _origCPT = HTMLVideoElement.prototype.canPlayType;
                    HTMLVideoElement.prototype.canPlayType = function(type) {
                        if (type && (type.indexOf('mp4') !== -1 || type.indexOf('avc') !== -1 || type.indexOf('h264') !== -1)) return 'probably';
                        return _origCPT.call(this, type);
                    };
                })();
            """)
            _using_fake_video = True
            log("[0] MP4 인터셉트 (*.mp4 전체) + canPlayType 오버라이드 등록 완료")
        except Exception as e:
            log(f"[0] 더미 영상 생성 실패 ({e}) — 원본 스트림으로 계속")

    # 1. 강의 페이지로 이동
    log(f"[1] 강의 페이지 이동: {lecture_url}")

    # 네트워크 요청/응답 스니핑 (commons.ssu.ac.kr + canvas learningx 전체)
    # page 객체가 재사용되므로 리스너는 반드시 finally에서 제거해야 누적 방지
    _on_request = None
    _on_response = None
    if debug:
        def _on_request(request):
            url = request.url
            if "google-analytics" in url or "gtm" in url:
                return
            if "commons.ssu.ac.kr" in url or "learningx" in url:
                log(f"  [SNIFF→REQ] {request.method} {url}")
                if request.post_data:
                    log(f"  [SNIFF→REQ] body={request.post_data!r}")

        _FULL_BODY_KEYWORDS = ("attendance_items", "content.php", "chapter.xml", "progress", "lessons")

        async def _on_response(response):
            url = response.url
            if "google-analytics" in url or "gtm" in url:
                return
            if "commons.ssu.ac.kr" in url or "learningx" in url:
                try:
                    body = await response.text()
                except Exception:
                    body = "(읽기 실패)"
                headers = dict(response.headers)
                set_cookie = headers.get("set-cookie", "")
                log(f"  [SNIFF←RES] {response.status} {url}")
                if set_cookie:
                    log(f"  [SNIFF←RES] set-cookie={set_cookie}")
                # 중요 API는 전체 body 출력, 나머지는 500자 제한
                if body:
                    if any(kw in url for kw in _FULL_BODY_KEYWORDS):
                        log(f"  [SNIFF←RES] body={body!r}")
                    elif len(body) < 500:
                        log(f"  [SNIFF←RES] body={body!r}")

        page.on("request", _on_request)
        page.on("response", _on_response)

    async def _cleanup():
        if _on_request:
            try:
                page.remove_listener("request", _on_request)
            except Exception:
                pass
        if _on_response:
            try:
                page.remove_listener("response", _on_response)
            except Exception:
                pass
        if _using_fake_video:
            try:
                await page.unroute("**/*.mp4")
            except Exception:
                pass

    await page.goto(lecture_url, wait_until="networkidle")
    log(f"    → 현재 URL: {page.url}")

    # 2. 초기 플레이어 선택 화면 frame 탐색 (재생 버튼이 있는 곳)
    log("[2] 플레이어 선택 화면 frame 탐색 중...")
    player_frame = await _find_player_frame(page)
    if not player_frame:
        log("    → 실패: tool_content 또는 commons.ssu.ac.kr frame 없음")
        log("    → 현재 프레임 목록:")
        for f in page.frames:
            log(f"       name={f.name!r}  url={f.url}")
        state.error = "비디오 프레임을 찾지 못했습니다."
        await _cleanup()
        return state
    # frame이 나중에 navigate되면 URL이 바뀌므로 지금 즉시 저장
    player_url_snapshot = player_frame.url
    log(f"    → 성공: {player_url_snapshot}")

    # 3. 이어보기 다이얼로그 처리 (처음부터 재생)
    await asyncio.sleep(1)
    dismissed = await _dismiss_dialog(player_frame, restart=True)
    log(f"[3] 이어보기 다이얼로그: {'처리됨' if dismissed else '없음'}")

    # 4. 재생 버튼 클릭
    log(f"[4] 재생 버튼({_PLAY_BTN}) 클릭 시도...")
    clicked = await _click_play(player_frame)
    log(f"    → {'클릭 성공' if clicked else '버튼 없음 또는 타임아웃'}")

    # 이어보기 다이얼로그가 재생 버튼 클릭 후 뜨는 경우도 처리
    await asyncio.sleep(1)
    dismissed2 = await _dismiss_dialog(player_frame, restart=True)
    log(f"[4b] 재생 후 이어보기 다이얼로그: {'처리됨' if dismissed2 else '없음'}")

    # 5. 재생 버튼 클릭 후 video 태그가 있는 frame을 새로 탐색
    log("[5] video 태그가 있는 frame 재스캔 중 (재생 후 frame 구조 변경 대응)...")
    log(f"    → 현재 전체 frame 목록:")
    for f in page.frames:
        log(f"       name={f.name!r}  url={f.url}")

    # video frame 탐색: 최대 10초만 기다림 (실패 시 빠르게 진단)
    frame = None
    for _ in range(10):
        for f in page.frames:
            if "commons.ssu.ac.kr" not in f.url:
                continue
            try:
                count = await f.evaluate("() => document.querySelectorAll('video').length")
                if count > 0:
                    frame = f
                    break
            except Exception:
                pass
        if frame:
            break
        await asyncio.sleep(1)

    if not frame:
        log("    → video frame 없음. 진도 API 직접 호출 방식으로 전환...")
        log(f"    → player URL: {player_url_snapshot}")
        await _cleanup()
        return await _play_via_progress_api(page, player_url_snapshot, on_progress, log, fallback_duration)
    log(f"    → video frame 발견: {frame.url}")

    # 6. video 요소 duration 대기
    log(f"[6] video 요소({_VIDEO_SEL}) duration 대기 (최대 {_PLAY_TIMEOUT}초)...")
    deadline = asyncio.get_event_loop().time() + _PLAY_TIMEOUT
    while asyncio.get_event_loop().time() < deadline:
        info = await _get_video_state(frame)
        if debug:
            log(f"    poll: info={info}")
        if info and info["duration"] > 0:
            log(f"    → 영상 시작 확인: duration={info['duration']:.1f}s")
            break
        await asyncio.sleep(0.5)
    else:
        log("[6] 타임아웃. 페이지 상태 진단:")
        await _debug_page_state(page, frame, log)
        _cleanup()
        state.error = "영상이 시작되지 않았습니다."
        return state

    # 7. 재생 완료까지 폴링
    log("[7] 재생 루프 시작")
    while True:
        info = await _get_video_state(frame)
        if info is None:
            # frame이 언로드된 경우
            log("[7] video state가 None — frame 언로드됨")
            break

        state.current = info["current"]
        state.duration = info["duration"]
        state.ended = info["ended"]

        if on_progress:
            on_progress(state)

        if info["ended"]:
            log("[7] 영상 ended=True — 완료")
            break

        # duration - threshold 이상 재생됐으면 완료로 간주
        if state.duration > 0 and state.current >= state.duration - _END_THRESHOLD:
            state.ended = True
            if on_progress:
                on_progress(state)
            log("[7] 재생 완료 기준 도달")
            break

        # 일시정지 상태면 강제 재생 (LMS 자동 정지 방지)
        if info["paused"]:
            log("[7] 일시정지 감지 → 강제 재생")
            await _ensure_playing(frame)

        await asyncio.sleep(_POLL_INTERVAL)

    await _cleanup()
    return state

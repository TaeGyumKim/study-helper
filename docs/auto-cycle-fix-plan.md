# 자동 모드 사이클 drift 수정 계획

## 배경

자동 모드를 N 사이클 돌리면 다음 두 증상이 발생.

1. **신규 동영상 미감지 / 진행상태 갱신 누락** — 사이클 1 에서 처리한 강의가 사이클 2 에서 다시 큐에 올라오거나, 신규 강의를 못 잡음.
2. **이미 다운로드한 mp4 인식 실패** — 디스크 96 vs `auto_progress.json` `downloaded=True` 83 (drift 13건).

근본 원인 10건은 [analyze 리포트 / 사이클 진단 채팅 로그] 참고.

## 목표

- fs ↔ store ↔ LMS 3 자 일관성 회복.
- 일시적 fetch 실패가 누적적 store 손실로 이어지지 않게.
- 영구 실패 강의가 사이클을 무한히 잡아먹지 않게.

## 우선순위

| PR | 범위 | BUG | 위험 | 비고 |
|----|------|-----|------|------|
| PR-1 | drift fix | BUG-1, BUG-2, BUG-4 + 보조 | 낮음 | 동작 핵심 — 우선 진행 |
| PR-2 | resilience | BUG-5, BUG-6, BUG-8 | 낮음 | 운영 효율 — 후속 |
| PR-3 | course identity | BUG-7, BUG-9 | **마이그레이션 위험** | user decision — 보류 |

## PR-1 범위 (이번 작업)

### 변경 1: BUG-4 (reconcile completion 가드 제거)
**파일**: `src/service/download_state.py`

`reconcile_store_with_filesystem()` 의 `if lec.completion != "completed": continue` 가드를 제거. 파일이 디스크에 실재한다면 LMS completion 상태와 무관하게 store 의 `downloaded` 만 정정. `played` 는 LMS 신호 우선이므로 별도 흐름이 처리.

`list_missing_items()` 의 동일 가드는 **유지** — 누락 알림은 LMS completion 기준이어야 사용자에게 의미 있음.

### 변경 2: BUG-2 (retain_only 부분실패 가드)
**파일**: `src/ui/auto.py`

`details` 안에 `None` 이 1개라도 있으면 `retain_only` 호출 보류. 사이클 도중 일시적 fetch 실패로 store entry 가 영구 삭제되는 것 방지.

### 변경 3: BUG-1 (사이클당 courses 갱신)
**파일**: `src/ui/auto.py`

매 사이클 시작 시 `scraper.fetch_courses()` 를 호출해 LMS 의 신규/제거 과목을 in-place 로 동기화. 실패 시 이전 목록 유지 (graceful degradation).

### 변경 4: 보조 (retain_only 빈 set 방어)
**파일**: `src/service/progress_store.py`

`retain_only(set())` 으로 호출되면 catastrophic delete 가 발생하므로 0 반환하고 skip. 호출자 버그/regression 의 안전망.

### 테스트
- `tests/test_download_state.py` — completion=incomplete 강의의 fs 가 reconcile 로 정정되는지
- `tests/test_auto_progress.py` (또는 신규) — `retain_only(set())` 가 0 반환하는지
- 가능한 경우: details 부분 None 시뮬레이션 통합 테스트 (auto.py 핵심 로직 단위 분리 후)

### 회귀 검증
- 기존 test 13개 모두 통과
- 신규 test 케이스 3개 추가

## PR-2 범위 (후속)

- BUG-8: 사이클 시작/종료 logger.info 추가 (로그만으로 사이클 추적 가능)
- BUG-5: `ProgressEntry.play_fail_count` + 임계 초과 시 격리 (텔레그램 1회 알림)
- BUG-6: driver crash 감지 시 강의 단위 즉시 abort, 다음 강의로 진행

## PR-3 (보류 — user decision 필요)

- BUG-7: course.id 기반 디렉토리 매핑 — 기존 디렉토리 마이그레이션 필요
- BUG-9: `_sanitize_filename` 강화 (emoji/제어문자 옵션)

## Rollout

- PR-1 머지 후 1~2 사이클 회기 검증 → `다운로드만 N개` 가 37 → <5 로 떨어지는지 확인
- 떨어지면 PR-2 진행, 안 떨어지면 추가 진단 (BUG-7 가능성 재검토)

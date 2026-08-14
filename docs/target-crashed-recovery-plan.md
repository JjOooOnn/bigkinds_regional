# `Target crashed` 복구 개선 구현 계획

## 1. 요구사항과 업무 규칙

- 메인 BigKinds 페이지 crash 시 같은 browser context에서 페이지 교체를 먼저 시도한다.
- 페이지 복구 실패 시 Playwright·Chromium 전체 세션을 재생성한다.
- 두 단계가 모두 실패하면 이후 지역을 처리하지 않고 작업을 `partial_failed` 또는 `failed`로 종료한다.
- 진행 중 이슈는 `failed`로 확정하지 않고 `started`로 유지해 재실행 대상이 되게 한다.
- 기존 기사당 복구 1회, 작업당 복구 2회 제한은 유지한다.
- 공개 API, DB 스키마, 체크포인트 형식, UI, 의존성은 변경하지 않는다.
- 직접 관련된 파일만 수정하고 단계별 검증 후 진행한다.

## 2. 현재 데이터 흐름

웹 요청 → worker process → `AuditService` → `RegionalCollector.run()` → 날짜 → 지역 → 이슈 → 출처 링크 순으로 실행된다. 결과는 JSONL 체크포인트에 즉시 기록되고 종료 후 Excel과 작업 상태가 생성된다.

현재 실패 흐름은 다음과 같다.

`기사 제한시간 초과 → 메인 페이지 crash → 다음 지역의 combo.inner_text()가 Target crashed → 지역 예외가 복구 경로를 거치지 않고 False 반환 → 같은 죽은 페이지로 다음 지역 계속 → 0/0 연쇄 실패`

## 3. 확인된 원인과 원인 후보

- 페이지 수명주기는 `close`만 추적하고 Playwright의 `crash` 이벤트는 추적하지 않는다.
- `_browser_session_failure()`은 `Target crashed`를 분류하지 않는다.
- browser가 연결된 채 renderer만 죽으면 현재 연결·종료 검사로 장애를 찾지 못할 수 있다.
- 지역 선택·이슈 목록 조회 단계는 장애를 분류하거나 복구하지 않고 `False`를 반환한다.
- 최초 renderer crash의 직접 원인은 메모리 부족, 자원 누적, task 취소와 정리 경합 중 하나일 수 있으나 현재 증거만으로 확정하지 않는다.

## 4. 요구사항과 현재 구현의 차이

- `crash` 이벤트 및 `Target crashed` 문자열 분류가 없다.
- 지역 선택 단계에서 복구 후 같은 지역을 재시도하지 않는다.
- 메인 페이지 교체 실패 시 전체 세션 fallback이 없다.
- 복구 불가 상태에서도 이후 지역을 계속 처리한다.
- 복구 상한 초과 시 진행 이슈를 `failed`로 확정한다.

## 5. 재사용 가능한 기존 코드

- `_track_page_lifecycle()` 이벤트 등록과 진행 상태 기록 패턴
- `_browser_session_failure()` 상태 우선 분류
- `_recover_browser_session()` 페이지·세션 복구 및 위치 복원 코드
- `_bounded_cleanup()`, `_close_page()`, `_cleanup_browser_resources()`
- `MAX_ARTICLE_RECOVERIES`, `MAX_JOB_RECOVERIES`
- 체크포인트의 `started` 상태와 행 중복 제거
- `browser_state`, `browser_restart_count`
- `AuditService`의 기존 `partial_failed`/`failed` 처리와 부분 결과 저장

## 6. 수정 대상 파일

- `src/regional_collector.py`: crash 관측·분류, 2단계 복구, 같은 지역/이슈 재시도, 복구 불가 시 작업 중단
- `tests/test_fault_injection.py`: crash 이벤트·분류·fallback·재시도·최종 중단 회귀 테스트
- `tests/test_checkpoint.py`: 치명적 장애 후 `started` 유지 검증

`checkpoint.py`, API, DB, frontend, 의존성 파일은 수정하지 않는다.

## 7. 구현 대안

1. 오류 문자열만 추가: 변경은 작지만 메시지 변경과 페이지 구분에 취약하다.
2. crash 이벤트 추적 + 페이지 우선 복구 + 전체 세션 fallback: 정확한 페이지를 구분하고 기존 구조를 재사용할 수 있다.
3. 항상 전체 Playwright 재시작: 보수적이지만 실행 비용과 변경 범위가 크다.
4. worker/job 전체 자동 재시작: 포괄적이지만 프로세스 관리와 재개 정책까지 범위가 확대된다.

## 8. 권장안

대안 2를 적용한다. 이벤트 정보를 우선하고 `Target crashed` 문자열은 보조 근거로 사용한다. 검사 페이지 crash는 같은 기사, 메인 페이지 crash는 같은 context의 새 페이지에서 재시도한다. 페이지 복구 실패 시 전체 세션을 한 번 재생성하고, 이마저 실패하면 작업을 종료하되 진행 이슈는 `started`로 유지한다.

## 9. 단계별 구현 계획

1. 회귀 테스트 작성
   - 메인/검사 페이지 crash 이벤트 추적, 문자열 fallback, 지역 선택 crash 후 동일 지역 재시도 시나리오를 추가한다.
   - 현재 코드에서 예상한 이유로 실패하는지 확인한다.
2. crash 관측과 분류 구현
   - `_crashed_pages`와 `Page.on("crash")`를 추가한다.
   - `page_crashed` 상태를 분류하고 source page를 inspection page보다 우선한다.
   - 이벤트가 없을 때만 `Target crashed` 문자열을 보조 근거로 사용한다.
3. 2단계 복구와 제어 흐름 수정
   - 메인 페이지 교체·위치 복원 후 실패 시 전체 세션을 재생성한다.
   - 지역 준비와 이슈 처리에서 같은 작업 단위를 재시도한다.
   - 최종 실패는 상위로 전달하고 `mark_issue_failed()`를 호출하지 않는다.
4. 전체 회귀 검증
   - 관련 테스트, 전체 Python 테스트, 제한된 실제 사이트 smoke test를 실행한다.

## 10. 단계별 완료 기준

1. 새 테스트가 기존 구현의 crash 미감지 또는 지역 복구 누락 때문에 실패한다.
2. 메인/검사 페이지 crash가 올바른 `page_crashed`와 `inspection_page_only`로 분류되고 기존 close/disconnected 테스트도 통과한다.
3. 페이지 교체 성공 시 전체 세션을 재시작하지 않고, 실패 시 전체 세션 fallback을 정확히 한 번 실행한다. 최종 실패 후 이후 지역을 호출하지 않고 진행 이슈는 `started`로 남는다.
4. 전체 테스트가 정상 종료하고 계획된 파일만 변경된다.

## 11. 테스트 및 검증 방법

관련 테스트:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_fault_injection.py tests\test_checkpoint.py -q -p no:cacheprovider --basetemp work\pytest-target-crash-targeted
```

전체 테스트:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp work\pytest-target-crash-full
```

실제 사이트 smoke test는 날짜 1개, 지역 2개, 지역별 이슈 1개로 제한한다. 자연 발생 crash는 재현성이 낮으므로 핵심 합격 기준은 fault-injection 테스트로 둔다.

## 12. 회귀 위험과 롤백

- 문자열 오탐은 정확한 page event 우선과 제한적인 `target crashed` 검사로 낮춘다.
- source/inspection page 오분류는 page 객체 집합과 source 우선순위로 방지한다.
- context 손상은 전체 세션 fallback으로 대응한다.
- 기존 복구 상한으로 무한 반복을 방지한다.
- 기존 체크포인트 중복 제거와 재시도 테스트로 중복 결과를 검증한다.
- DB·API·체크포인트 형식 변경이 없으므로 수정한 세 코드·테스트 파일을 되돌리면 롤백된다.

## 13. 결정 사항

- 메인 페이지 crash는 페이지 교체를 우선한다.
- 페이지 복구 실패 시 전체 세션 복구를 추가로 시도한다.
- 최종 복구 실패 시 진행 이슈는 `started`로 유지한다.
- 최종 복구 실패 후 이후 지역을 계속하지 않고 작업을 종료한다.
- 페이지 교체와 전체 세션 fallback은 하나의 논리적 복구 사건으로 계산한다.
- 공개 API, DB 스키마, UI, 의존성은 변경하지 않는다.

# 구현계획: 링크 오탐·Playwright Locator 오류·재시도 결과 중복 수정

## 1. 조사 결과

### 현재 처리 흐름

`출처 카드 클릭 → 기사별 제한시간 제어 → BrowserLinkChecker 렌더링 근거 수집 → 판정 → AuditRow 저장 → Excel/API 결과 생성`

핵심 구현은 다음 파일에 있다.

- `src/regional_collector.py`: 출처 순회, 기사 제한시간, 브라우저 복구, 결과 저장
- `src/link_checker.py`: 페이지 이동, 렌더링 근거 수집, 판정 입력 생성
- `src/verdict.py`: 정상·접근제한·빈화면·타임아웃 등의 최종 판정
- `src/checkpoint.py`: 결과 및 디버그로그의 append-only 체크포인트 저장과 재개

### 경기연구원 Locator 오류

경기연구원 두 건은 HTTP 200과 올바른 최종 URL을 확보했고 스크린샷에도 제목, 보고서 정보, PDF가 표시된다. 그러나 외부 페이지가 네이티브 `Map` 또는 `Map.prototype.set`을 덮어쓰는 환경에서 `Locator.all_inner_texts()`를 호출하면 Playwright 내부 selector engine 초기화가 깨진다.

로컬에서 `window.Map = Object` 및 `Map.prototype.set = null`을 적용한 페이지로 다음 오류를 동일하게 재현했다.

```text
Locator.all_inner_texts: TypeError: this._engines.set is not a function
```

현재 `BrowserLinkChecker.inspect_open_page()`는 이 내부 검사 예외를 일반 예외로 수집한 뒤 재시도한다. 재시도가 모두 실패하면 HTTP 200과 유효한 URL이 있더라도 제목과 본문을 빈 값으로 판정기에 전달하여 `빈화면`을 생성하고, 실제 빈 화면 메시지 대신 Locator 예외를 `오류내용`에 기록한다. 따라서 실제 화면 상태가 아니라 검사 도구의 실패가 기사 오류로 잘못 승격된다.

관련 첨부 근거:

- `output/bigkinds_regional_link_audit_2026-08-27_2026-08-27_20260828_091548_job_cd267b05.xlsx`
  - `점검결과!A9:U9`
  - `디버그로그!A10:AJ11`
- `output/bigkinds_regional_link_audit_2026-08-28_2026-08-28_20260828_100407_job_4f9f9400.xlsx`
  - `점검결과!A55:U55`
  - `디버그로그!A66:AJ67`

### 정상 기사 본문의 접근제한 문구 오탐

인천 디지털타임스 건은 정상 기사 본문의 “시민들의 접근이 제한됐던 내항”이라는 문장을 접근차단 문구로 오인했다.

현재 판정기는 `접근 제한`, `접근이 제한`을 문맥 없이 부분 문자열로 검색한다. `article_rendered=True`인 경우에도 해당 문구가 주요 본문에 포함되면 `ACCESS_STRONG_TEXT_PRIMARY`로 접근제한을 우선한다. 디버그로그의 감지 위치도 `div#article-body > p.align-l`과 `main#container`로 기록되어 차단 UI가 아니라 기사 본문임을 보여 준다.

관련 첨부 근거:

- `output/bigkinds_regional_link_audit_2026-08-20_2026-08-20_20260828_092234_job_a4f72e4f.xlsx`
  - `점검결과!A4:U5`
  - `디버그로그!A5:AJ10`
- `artifacts/screenshots/2026-08-20_인천광역시_1_접근제한_20260828_092455.png`

### 브라우저 복구 후 타임아웃 행 잔존

IPARK 기사와 인천 기사에 남은 타임아웃은 브라우저 page crash 이후 발생한 임시 결과다.

현재 page crash 이벤트는 상태 집합과 로그만 갱신한다. 진행 중인 기사 task가 예외로 즉시 종료되지 않으면 `RegionalCollector._run_article_with_controls()`가 105초 제한시간까지 기다린 뒤 타임아웃 결과를 저장한다. 그다음 브라우저 복구 경로가 같은 이슈를 처음부터 재실행하여 정상 결과를 추가한다.

`CheckpointStore.add_row()`의 중복 키는 URL 중심이다. URL을 얻지 못한 타임아웃 행은 기사제목을 fallback 키로 사용하고, 정상 재시도 행은 원본 URL을 사용하므로 같은 출처가 서로 다른 결과로 취급된다. 이 때문에 다음 영역에 임시 실패와 최종 성공이 함께 남는다.

- Excel `점검결과` 및 `오류목록`
- Excel 요약 통계
- SQLite/API 결과와 오류 필터
- 실행 중 `processed_links`, `normal_count`, `error_count`

관련 첨부 근거:

- `output/bigkinds_regional_link_audit_2026-08-28_2026-08-28_20260828_100407_job_4f9f9400.xlsx`
  - `점검결과!A41:U42`
  - `디버그로그!A42:AJ43`
  - `디버그로그!A53:AJ53`
- `work/audit_2026-08-28_2026-08-28_20260828_100407_job_4f9f9400.log`

## 2. 권장 변경 방향

### 외부 페이지 DOM 근거 수집 안정화

- `src/link_checker.py`에서 외부 기사 페이지에 대한 `all_inner_texts()` 호출을 제거한다.
- 제목, 본문, 문단, 헤딩, `main`/`article` 후보, 기사 DOM 존재 여부를 한 번의 DOM snapshot으로 수집한다.
- 페이지 내부에서는 결과를 `JSON.stringify(...)`한 문자열로 만들고, Playwright 경계에는 문자열만 통과시킨 뒤 Python에서 역직렬화한다.
- 페이지가 `Map`을 훼손한 경우에도 문자열 반환은 정상 동작함을 로컬 재현으로 확인했다.
- 비뉴스 상세페이지 검사와 접근제한 문구 위치 검사도 객체나 배열을 직접 반환하지 않고 같은 문자열 직렬화 경로를 사용한다.
- 페이지 내부 전역 객체의 추가 오염에 대비하여 DOM snapshot 코드는 `Map`에 의존하지 않고 단순 반복문과 배열만 사용한다.
- 렌더 근거 수집 자체가 실패하면 실제 빈 화면으로 판정하지 않는다. 기존 판정값 `확인필요`와 내부 사유 코드 `INSPECTION_INTERNAL_ERROR`를 사용하고 검사 예외는 진단 필드에 남긴다.

외부 API와 Excel의 판정 종류는 추가하지 않는다.

### 접근제한 판정의 증거 우선순위 수정

- 실제 기사 제목과 충분한 본문이 확인된 경우 기사 본문에 등장한 “접근 제한” 등의 서술 문구는 오류 근거로 사용하지 않는다.
- 접근제한은 다음과 같은 페이지 수준의 강한 근거가 있을 때 확정한다.
  - HTTP 401 또는 403
  - CAPTCHA, robot check, 브라우저 보안 차단 화면
  - 기사 렌더링 근거가 없는 로그인 또는 전용 오류 화면
- 정상 기사 문맥, 댓글·구독·보조 기능의 권한 문구는 진단 정보로 남기되 최종 정상 판정을 덮어쓰지 않는다.
- 실제 CAPTCHA, 로그인, 403 화면에 대한 기존 오류 검출은 유지한다.

### crash를 타임아웃보다 먼저 처리

- page crash, unexpected close, browser disconnect 이벤트가 기사 제한시간 제어를 직접 깨울 수 있도록 `asyncio.Event` 또는 동등한 수명주기 signal을 추가한다.
- 기사 task, 사용자 취소 task, 브라우저 실패 signal을 함께 기다린다.
- 브라우저 실패가 먼저 발생하면 기사 task를 제한시간 내 정리하고 `BrowserSessionFailure`를 발생시켜 기존 복구 경로로 전달한다.
- 이 경우 `ARTICLE_DEADLINE_EXCEEDED` 결과나 `link_completed: 타임아웃` 이벤트를 생성하지 않는다.
- 실제로 브라우저가 살아 있으면서 모든 시도가 제한시간을 초과한 경우에는 현재 타임아웃 판정을 유지한다.

### 출처 단위 결과 upsert

- 결과의 안정적인 식별 키를 `(조회요청일, 지역명, 이슈순번, 출처순번)`으로 정의한다.
- 같은 출처를 재검사하면 기존 활성 행을 새 결과로 교체한다. URL 유무나 URL 변경은 동일 출처 식별에 영향을 주지 않는다.
- 체크포인트 파일은 append-only 형식을 유지한다. 교체 결과도 새 `row` 레코드로 추가하고, 로드 시 같은 출처의 마지막 레코드만 활성 결과로 복원한다.
- `source_order=0`인 과거 체크포인트 행은 호환성을 위해 현재 URL/기사제목 기반 키를 사용한다.
- 삽입, 교체, 변화 없음 상태를 구분하여 collector가 카운터를 잘못 누적하지 않도록 한다.
- 교체 후 진행 수치는 현재 활성 체크포인트 행을 기준으로 다시 계산한다.

### 재시도 이력 정책

사용자 선택에 따라 이전 실패는 디버그 영역에만 보존한다.

- `점검결과`, `오류목록`, 요약, SQLite/API 결과에는 최종 활성 결과만 포함한다.
- `디버그로그`와 기술 로그에는 최초 실패 및 브라우저 복구 이력을 유지한다.
- 정상 결과가 이전 실패를 교체한 시점에 다음 내용을 갖는 별도 디버그 이벤트를 추가한다.
  - 실행단계: `링크재시도`
  - 이벤트: `이전 판정 대체`
  - 상세내용: 이전 판정, 최종 판정, 최종 결과에서 제외되었음을 명시
- 기존 Excel 열이나 외부 API schema는 변경하지 않는다.

## 3. 변경 대상 파일

### 반드시 변경

- `src/link_checker.py`
  - 외부 페이지 DOM snapshot
  - 비뉴스 상세 근거 및 접근제한 위치의 문자열 직렬화
  - 내부 검사 실패와 실제 빈 화면 구분
- `src/verdict.py`
  - 정상 기사 근거와 문맥성 접근 문구의 우선순위 조정
- `src/regional_collector.py`
  - 브라우저 수명주기 signal의 즉시 전파
  - 결과 upsert 결과에 따른 카운터 갱신
  - 판정 대체 디버그 이벤트
- `src/checkpoint.py`
  - 출처 단위 upsert
  - append-only 체크포인트의 최신 결과 복원
- `src/url_utils.py`
  - 안정적인 결과 식별 키와 최신 결과 병합 규칙

### 테스트 변경

- `tests/test_browser_link_flow.py`
- `tests/test_verdict_classification.py`
- `tests/test_checkpoint.py`
- `tests/test_fault_injection.py`
- `tests/test_excel_writer.py`
- `tests/test_api_jobs.py`

### 회귀 확인만 필요

- `src/excel_writer.py`: 활성 결과 목록을 정상적으로 직렬화하는지 확인
- `src/application/audit_service.py`: 최종 카운터가 정리된 체크포인트 행을 사용하는지 확인
- `src/application/job_repository.py`: API용 결과 교체 시 정리된 행만 저장하는지 확인

프런트엔드, SQLite schema, 외부 API 응답 형식은 변경하지 않는다.

## 4. 단계별 구현 계획

### 1단계: DOM snapshot과 내부 검사 오류 분리

1. `link_checker.py`에 외부 페이지의 표시 상태를 한 번에 수집하는 내부 snapshot 자료구조와 함수를 추가한다.
2. `inspect_rendered_page()`의 `all_inner_texts()` 호출을 snapshot 사용으로 교체한다.
3. `_wait_for_render_signal()`, `_locate_marker()`, `_inspect_non_news_detail()`이 페이지 객체나 배열을 직접 반환하지 않도록 문자열 직렬화 경로로 통일한다.
4. generic inspection exception이 모두 소진된 경우 `빈화면` 대신 `확인필요/INSPECTION_INTERNAL_ERROR`를 반환한다.

검증:

- `window.Map = Object`인 연구보고서 페이지가 `정상/Y`로 판정된다.
- `Map.prototype.set = null`인 연구보고서 페이지도 `정상/Y`로 판정된다.
- 제목, 본문, PDF 첨부 근거가 디버그 필드에 남는다.
- 진짜 빈 페이지는 계속 `빈화면/N`이다.

### 2단계: 접근제한 판정 우선순위 수정

1. `article_rendered=True`인 정상 기사 본문에 포함된 문맥성 접근 문구가 최종 판정을 덮어쓰지 않도록 순서를 조정한다.
2. 강한 차단 화면은 HTTP 상태, 기사 렌더링 부재, challenge UI 근거를 조합하여 유지한다.
3. 감지 문구와 위치는 정상 판정에서도 진단 정보로 보존한다.

검증:

- “오랜 기간 항만 보안구역으로 시민들의 접근이 제한됐던 내항”을 포함한 정상 기사는 `정상/Y`다.
- HTTP 403 차단 화면은 `접근제한/N`이다.
- CAPTCHA와 로그인 요구 화면은 `접근제한/N`이다.
- 정상 기사의 댓글 권한 문구는 계속 무시된다.

### 3단계: crash 조기 감지

1. 브라우저와 page 수명주기 callback에서 기사 제어 루틴을 깨우는 signal을 설정한다.
2. `_run_article_with_controls()`가 signal을 감지하면 기사 task를 정리하고 즉시 `BrowserSessionFailure`로 전환한다.
3. 기존 기사별 및 작업 전체 복구 횟수 제한은 그대로 사용한다.
4. 정상적인 네트워크 지연에는 기존 deadline 정책을 유지한다.

검증:

- page crash가 발생하면 105초 타임아웃 결과를 생성하지 않고 복구가 시작된다.
- 실제로 끝나지 않는 네트워크 요청은 기존대로 deadline 후 `타임아웃/N`이다.
- 사용자 중단이 브라우저 실패와 경합해도 중단 acknowledgment와 정리가 유지된다.

### 4단계: 체크포인트 upsert와 카운터 정합성

1. 출처순번 기반 결과 키와 upsert 결과 타입을 추가한다.
2. `CheckpointStore`의 메모리 행과 체크포인트 재로드가 마지막 출처 결과를 활성화하도록 변경한다.
3. collector는 신규 삽입과 교체를 구분하고, 교체 후 현재 행에서 진행 카운터를 다시 계산한다.
4. 오류가 정상으로 교체되면 `링크재시도/이전 판정 대체` 디버그 이벤트를 추가한다.

검증:

- 타임아웃 행에 URL이 없고 정상 행에 URL이 있어도 최종 활성 행은 하나다.
- 체크포인트를 다시 열어도 정상 행 하나만 복원된다.
- 최종 수치는 `processed_links=1`, `normal_count=1`, `error_count=0`이다.
- 디버그로그에는 최초 실패, 정상 결과, 대체 이벤트가 모두 남는다.

### 5단계: Excel/API 통합 검증

1. 최종 활성 행으로 Excel을 생성한다.
2. 웹 작업 완료 시 SQLite `job_results`에 최종 활성 행만 교체 저장되는지 확인한다.
3. 결과 요약과 오류 필터가 교체된 실패를 집계하지 않는지 확인한다.

검증:

- `점검결과`에는 정상 행 하나만 존재한다.
- `오류목록`에는 해당 출처가 존재하지 않는다.
- 요약과 API는 전체 1, 정상 1, 오류 0을 반환한다.
- `디버그로그`에는 과거 실패가 복구 이력으로 남는다.

## 5. 테스트 및 회귀 검증 계획

집중 테스트:

```powershell
.\.venv\Scripts\python.exe -m pytest -q \
  tests/test_browser_link_flow.py \
  tests/test_verdict_classification.py \
  tests/test_checkpoint.py \
  tests/test_fault_injection.py \
  tests/test_excel_writer.py \
  tests/test_api_jobs.py
```

전체 제품 테스트:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

수동 소규모 검증 시에는 경기연구원 연구보고서, 디지털타임스 기사, 머니투데이 IPARK 기사를 각각 포함하는 날짜와 지역으로 실행하여 다음을 확인한다.

- 경기연구원 두 건이 정상으로 판정되는지
- 디지털타임스 기사 본문의 문맥성 접근 문구가 오탐되지 않는지
- 브라우저 복구 뒤 최종 결과에 임시 타임아웃이 남지 않는지
- 디버그로그에는 복구 과정이 식별 가능하게 남는지

## 6. 가정과 위험요소

- 첨부된 완성 XLSX는 소급 수정하지 않는다. 수정된 규칙은 이후 실행과 재개되는 체크포인트부터 적용한다.
- 저장된 HTML이 없어 경기연구원 사이트가 `window.Map`을 교체했는지 `Map.prototype.set`만 훼손했는지는 구분할 수 없다. 두 조건 모두 동일 오류가 재현되고 권장 snapshot 방식은 양쪽을 회피한다.
- 출처순번은 같은 날짜·지역·이슈를 재실행하는 동안 BigKinds 출처 카드 순서가 유지된다는 기존 collector 계약을 사용한다.
- 외부 사이트가 실행 도중 출처 카드 순서를 변경할 가능성은 낮지만, 디버그 이벤트에 언론사와 기사제목을 함께 기록하여 교체 근거를 추적할 수 있게 한다.
- 새로운 dependency, 데이터베이스 migration, 프런트엔드 변경은 필요하지 않다.
- 저장소의 사용자 소유 미추적 파일 `bigkinds_regional (2).zip`은 변경하지 않는다.
- 현재 문서화된 `pytest -q`는 접근 권한이 없는 기존 `work/pytest-*` 디렉터리까지 수집하여 실패한다. 이 작업에서는 관련 없는 pytest 설정을 수정하지 않고 `pytest tests -q`로 전체 제품 테스트를 검증한다.

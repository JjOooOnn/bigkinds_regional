# Git Diff 독립 검토 결과

- 검토일: 2026-08-04
- 검토 대상: 현재 작업 트리의 추적 파일 변경과 관련 미추적 파일
- 검토 기준: 최초 요구사항, `AGENTS.md`, `docs/implementation-plan.md`, 실제 변경 코드, 테스트 결과
- 검토 전제: 저장소에서 별도의 최초 요구사항 원문을 찾지 못해 `docs/implementation-plan.md`의 목표와 1단계를 기준으로 판단했다.
- 결론: 수정이 필요한 문제가 발견되어 커밋하지 않았다.

## Critical

없음.

## High

### 1. 서버 로그에 비밀값이 평문으로 저장될 수 있음

`src/logging_utils.py`의 마스킹 규칙은 URL 쿼리와 일부 헤더 형식만 처리한다. 새 stdout/stderr 저장 기능을 통해 다음과 같은 일반적인 비밀값 표기가 그대로 파일에 기록되는 것을 재현했다.

- `api_key=supersecret`
- `password: supersecret`
- `Bearer supersecret`

이는 구현 계획의 “로그에는 비밀값·쿠키·인증 헤더를 남기지 않는다”는 조건을 위반한다. 서버 출력 전체를 파일로 보존하도록 변경되었기 때문에 기존보다 비밀값 노출 범위가 넓어진다.

현재 테스트는 `Authorization:` 헤더 한 형식만 확인하므로 일반 key/value, JSON 형식, 단독 Bearer 토큰에 대한 회귀 테스트가 필요하다.

## Medium

### 1. 실패한 이슈를 완료된 것으로 관측함

`src/regional_collector.py`의 `_audit_region()`은 `_audit_issue()`가 반환하면 즉시 `issue_completed` 이벤트를 기록한다. 그러나 `_audit_issue()`는 상세 모달 열기에 실패했을 때 실패를 반환하거나 예외를 전파하지 않고 그대로 반환한다.

그 결과 다음 문제가 발생한다.

- 실패한 이슈가 `issue_completed`로 기록된다.
- `last_progress_at`이 정상 완료처럼 갱신된다.
- 장애 원인 판정 시 실제 실패·정지 시점을 잘못 판단할 수 있다.

모달 열기 실패 경로에서 완료 이벤트가 발생하지 않는지 검증하는 테스트가 필요하다.

### 2. SQLite 마이그레이션 전 백업이 없음

`src/application/job_repository.py`는 저장소 초기화 시 기존 `jobs` 테이블에 즉시 `ALTER TABLE`을 실행한다. 반면 구현 계획의 SQLite 스키마 호환성 대응에는 적용 전 DB 백업을 생성하도록 명시되어 있다.

추가형 마이그레이션이므로 직접적인 데이터 삭제 가능성은 낮지만, 운영 DB 마이그레이션 실패 시 계획된 복구 수단이 없다. 다음 검증이 필요하다.

- 기존 스키마 DB의 마이그레이션 테스트
- 적용 전 DB와 `-wal`, `-shm` 파일의 일관된 백업 확인
- 마이그레이션 실패 시 복구 절차 확인

## Low

### 1. 날짜별 서버 로그가 자정에 회전하지 않음

`run_web.py`는 서버 시작 시 `date.today()`로 로그 파일명을 한 번 정하고 같은 파일을 계속 연다. 서버가 자정을 넘어 실행되면 이후 출력도 시작일 파일에 저장되므로 구현 계획의 날짜별 파일 보존 요구를 완전히 충족하지 않는다.

날짜 변경 시 새 파일로 전환되는지 검증하는 테스트가 필요하다.

### 2. 변경 목적과 무관한 대용량 산출물이 미추적 상태임

다음 파일은 현재 코드 diff에는 포함되지 않지만, `git add .` 사용 시 함께 커밋될 수 있다.

- `bigkinds_regional.zip`: 약 32.7MB이며 `frontend/node_modules`와 프런트엔드 빌드 결과를 포함한다.
- `ICON-N-B(ico).ico`: 약 268KB이다.

의도적으로 배포할 파일이 아니라면 커밋 범위에서 제외해야 한다.

## 검증 결과

### 백엔드

```powershell
.venv\Scripts\python.exe -m pytest -q
```

- 결과: 121개 통과
- 경고: Starlette `TestClient` 관련 deprecation 경고 1개
- 최초 샌드박스 실행에서는 Chromium `spawn EPERM`으로 브라우저 테스트 4개가 실행되지 않았으나, 샌드박스 밖에서 전체 테스트를 다시 실행해 모두 통과함

### 프런트엔드

```powershell
cd frontend
npm test
npm run build
```

- 테스트: 4개 통과
- TypeScript/Vite 빌드: 성공

### Git 검사

```powershell
git diff --check
git status --short
```

- `git diff --check`: 오류 없음
- 테스트와 빌드 후 기존 제품 코드 변경 범위가 늘어나지 않았음

## 수행하지 않은 검증

- 실제 BigKinds 사이트 제한 실행
- 느린 응답, 브라우저 종료, 작업 프로세스 종료 등 장애 주입 시나리오
- 전체 17개 지역 장시간 실행
- 체크포인트, SQLite, Excel 결과 건수 대조

## 커밋 여부

High 및 Medium 문제가 확인되었으므로 커밋하지 않았다.

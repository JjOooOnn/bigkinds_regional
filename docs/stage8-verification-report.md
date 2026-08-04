# 8단계 전체 검증 기록

- 검증일: 2026-08-04
- 기준 문서: `docs/implementation-plan.md` 8단계
- 상태: 자동화 검증 완료, 실제 사이트의 기사 행 생성 및 전체 17지역 장시간 검증은 보류

## 자동화 검증

| 구분 | 명령 | 결과 |
| --- | --- | --- |
| 백엔드 단위·로컬 브라우저·장애 주입 | `.venv\Scripts\python.exe -m pytest -q tests -p no:cacheprovider --basetemp "work/pytest-stage8-<timestamp>"` | 종료 코드 0, 168 passed, Starlette `TestClient` deprecation 경고 1건 |
| 프런트엔드 DOM 테스트 | `cd frontend; npm.cmd test -- --run` | 종료 코드 0, 9 passed |
| 프런트엔드 production build | `cd frontend; npm.cmd run build` | 종료 코드 0 |
| 결과 건수 대조 도구 | `.venv\Scripts\python.exe scripts\verify_result_counts.py --help` | 종료 코드 0 |

Chromium을 사용하는 백엔드 테스트는 기본 샌드박스에서 `spawn EPERM`으로 실행되지 않으므로, 승인된 로컬 실행 환경에서 다시 수행했다. 이는 코드 테스트 실패가 아니다.

## 결과 건수 대조

`scripts/verify_result_counts.py`는 하나의 웹 작업 ID를 기준으로 다음 네 값을 읽는다.

1. 작업 레코드의 `processed_links`
2. JSONL 체크포인트의 결과 행 수
3. SQLite `job_results` 행 수
4. Excel `점검결과` 시트의 데이터 행 수

네 값이 모두 같으면 종료 코드 0, 하나라도 다르면 종료 코드 1이다.

```powershell
.venv\Scripts\python.exe scripts\verify_result_counts.py `
  --db work\web_jobs.sqlite3 --job-id <job-id>
```

도구 자체는 `tests/test_run_verification.py`에서 일치·불일치 경우를 모두 검증했다.

## 실제 BigKinds 제한 실행

| 범위 | 결과 | 생성 Excel |
| --- | --- | --- |
| 2025-07-08, 서울특별시, 이슈 1개 | 사이트가 요청일과 다른 `2026-02-25`를 표시해 날짜 불일치 보호 로직이 이슈 점검을 건너뜀. 종료 코드 0, 기사 0건 | `output/bigkinds_regional_link_audit_2025-07-08_2025-07-08_20260804_181246.xlsx` |
| 2026-02-25, 서울특별시, 이슈 1개 | 날짜 일치. 사이트가 이슈 0개를 반환해 기사 0건. 종료 코드 0 | `output/bigkinds_regional_link_audit_2026-02-25_2026-02-25_20260804_181423.xlsx` |
| 2026-02-25, 서울·부산·대구, 지역별 이슈 1개 | 세 지역 모두 이슈 0개, 기사 0건. 종료 코드 0 | `output/bigkinds_regional_link_audit_2026-02-25_2026-02-25_20260804_181738.xlsx` |

실제 실행은 CLI 경로이므로 SQLite `job_results`를 만들지 않는다. 따라서 기사가 하나 이상 생성되는 실제 날짜·지역을 확보한 뒤에는 웹 작업으로 실행하고 위 대조 도구를 사용해야 한다.

## 2026-08-04 전체 17지역 실제 실행

```powershell
.venv\Scripts\python.exe main.py `
  --start-date 2026-08-04 --end-date 2026-08-04 --regions 전체
```

- CLI 종료 코드: 0
- Excel: `output/bigkinds_regional_link_audit_2026-08-04_2026-08-04_20260805_081704.xlsx`
- 체크포인트: `work/checkpoint_2026-08-04_2026-08-04_all.jsonl`
- 로그: `work/audit_2026-08-04_2026-08-04_20260805_081704.log`
- 서울특별시 처리 결과: 정상 29건, 오류 1건
- 체크포인트 결과 행: 30건
- Excel `점검결과` 행: 30건
- 완료 날짜×지역 단위: 0건

서울특별시의 세 번째 이슈를 처리하는 중 출처 12/15에서 타임아웃이 발생했다. 이후 브라우저 대상이 충돌해(`Locator.inner_text: Target crashed`) 나머지 16개 지역은 지역 선택 단계에서 모두 실패했다. 이 실행은 CLI 경로이므로 SQLite `job_results`를 생성하지 않아 네 저장소 대조 대상은 아니다.

CLI의 현재 종료 코드 정책은 `failed`에만 1을 반환하며, 부분 실패는 0을 반환한다. 따라서 위 종료 코드 0은 전체 17지역 점검 성공을 뜻하지 않는다.

## 보류 항목과 다음 조건

웹 작업 경로에서의 전체 17지역 장시간 실행 및 네 저장소 결과 행 수 대조는 수행하지 않았다. 제한 실행 두 단계 모두 기사 행이 0건이었고, 이후 CLI 전체 실행은 브라우저 충돌로 17지역을 완주하지 못했기 때문이다.

다음 조건이 충족되면 재개한다.

1. 실제 사이트에서 이슈와 기사 카드가 존재하는 날짜·지역을 지정한다.
2. 1일·1지역·이슈 1개 웹 작업이 최종 상태에 도달하고 기사 행을 하나 이상 저장한다.
3. 같은 날짜의 복수 지역 실행 후 결과 건수 대조를 통과한다.
4. 마지막으로 전체 17지역 장시간 실행을 수행하고 결과 건수 대조를 통과한다.

Windows 절전 후 복귀는 계획서대로 별도 통제 환경에서만 검증하며, 이번 단계에서는 자동 재개 기능을 추가하지 않았다.

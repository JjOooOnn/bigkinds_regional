# 버전 표시 및 커밋 제목 버전 규칙 구현 계획

## 결정 사항

- 제품 버전은 `frontend/package.json`의 `version`을 단일 기준으로 사용한다.
- 이번 미커밋 개선을 포함한 버전은 표준 Semantic Versioning 표기인 `v1.0.1`로 한다. manifest에는 접두사 없는 `1.0.1`을 저장하고 화면·커밋 제목에는 `v`를 붙인다.
- 모든 커밋 제목은 `[v1.0.1] fix: 요약` 형식으로 현재 제품 버전을 포함한다.
- 같은 배포 버전에 속한 여러 커밋은 같은 버전을 사용할 수 있다.

## 현재 구조

- `frontend/package.json`과 `frontend/package-lock.json`은 현재 `1.0.0`을 가진다.
- `frontend/src/App.tsx`의 footer에는 개인정보 안내만 표시된다.
- `src/api/app.py`는 FastAPI OpenAPI 버전을 문자열 `1.0.0`으로 별도 선언한다.
- 활성 Git hook이나 커밋 제목 규칙은 없다.

## 단계별 구현 계획

### 1단계: 버전 기준과 화면·API 표시 동기화

1. `frontend/package.json`과 lockfile의 프로젝트 버전을 `1.0.1`로 올린다.
2. 프런트엔드는 manifest의 버전을 읽어 기존 footer에 `버전 v1.0.1`을 함께 표시한다.
3. Python의 작은 버전 조회 모듈이 같은 manifest를 읽고, FastAPI OpenAPI 버전에 사용한다.
4. 버전 누락·형식 오류는 서버 시작 시 원인을 알 수 있는 오류로 처리한다.

완료 기준:

- footer와 `/openapi.json`의 버전이 manifest의 `1.0.1`과 일치한다.
- 기존 footer 문구, API 계약, 작업 데이터는 변경하지 않는다.

### 2단계: 버전 동기화 회귀 테스트

1. 프런트엔드 렌더링 테스트가 footer의 `버전 v1.0.1` 표시를 확인한다.
2. Python API 테스트가 OpenAPI `info.version`과 manifest 버전의 일치를 확인한다.
3. 버전 조회 모듈의 누락·잘못된 형식 처리를 단위 테스트로 확인한다.

완료 기준:

- 버전 변경으로 UI와 API가 불일치하면 자동 테스트가 실패한다.
- 정상 manifest와 오류 manifest 모두 기대한 결과를 낸다.

### 3단계: 모든 커밋의 버전 표기 검사

1. `frontend/package.json`의 버전과 커밋 제목을 검사하는 저장소 내 Python 스크립트를 추가한다.
2. 제목이 `[vMAJOR.MINOR.PATCH] type: 요약` 형식이며 현재 manifest 버전과 일치하는지 확인한다.
3. `.githooks/commit-msg`에서 이 스크립트를 호출한다.
4. 올바른 제목, 버전 누락, 버전 불일치, 잘못된 type의 자동 테스트를 추가한다.

완료 기준:

- 현재 버전과 일치하는 제목만 커밋 전에 통과한다.
- 오류 메시지는 올바른 형식을 제시한다.

### 4단계: hook 활성화와 사용 규칙 문서화

1. README에 버전 상승 기준과 커밋 제목 예시를 문서화한다.
2. `git config core.hooksPath .githooks`로 로컬 hook을 활성화하는 절차를 문서화한다.
3. 버전 변경 시 manifest와 lockfile을 함께 갱신하도록 안내한다.

완료 기준:

- 새 개발 환경에서도 hook 설치와 버전·커밋 규칙을 재현할 수 있다.

### 5단계: 전체 검증

1. 프런트엔드 테스트와 production build를 실행한다.
2. 전체 Python 테스트를 실행한다.
3. 정적 빌드 화면과 `/openapi.json`에서 버전을 수동 확인한다.
4. 올바른·잘못된 커밋 제목으로 hook 검사를 확인한다.

## 변경 대상 파일

- `frontend/package.json`, `frontend/package-lock.json`
- `frontend/src/App.tsx`, `frontend/src/App.test.tsx`
- `src/version.py` (신규), `src/api/app.py`
- `tests/test_version.py` (신규)
- 3단계 이후: `scripts/`, `.githooks/commit-msg`, `README.md`

## 검증 명령

```powershell
cd frontend
npm.cmd test
npm.cmd run build
cd ..

.\.venv\Scripts\python.exe -m pytest tests\test_version.py tests\test_api_jobs.py -q -p no:cacheprovider --basetemp work\pytest-version-targeted
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp work\pytest-version-full
```

## 범위와 롤백

- 1·2단계는 버전 표시·동기화와 테스트만 변경한다. 커밋 hook, README 규칙, 새 의존성은 포함하지 않는다.
- 문제가 생기면 이 단계의 버전·UI·API·테스트 파일만 되돌리고, SQLite·체크포인트·Excel·기존 Target-crash 변경은 건드리지 않는다.

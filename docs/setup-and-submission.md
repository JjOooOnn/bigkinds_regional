# 설치·제출 안내

## 지원 환경

- Windows 10/11 64비트
- Python 3.12.x
- Node.js `^20.19.0` 또는 `22.12.0` 이상
- 의존성 설치와 실제 링크 점검을 위한 인터넷 연결

시스템 Chrome을 별도로 준비할 필요는 없습니다. Playwright 명령으로 프로젝트 전용 Chromium을 설치합니다.

## 다른 PC 설치

압축을 푼 프로젝트 루트에서 다음 명령을 실행합니다.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.lock.txt
python -m playwright install chromium

cd frontend
npm ci
npm run build
cd ..
```

잠금파일 설치가 특정 환경에서 불가능한 경우에만 `pip install -r requirements.txt`로 호환 범위 안의 최신 의존성을 설치하고 전체 테스트를 다시 실행합니다.

## 실행

웹 화면은 다음 중 하나로 실행합니다.

```powershell
start_web.bat
python run_web.py
```

기본 주소는 `http://127.0.0.1:8000`입니다. 브라우저를 자동으로 열지 않으려면 `python run_web.py --no-browser`를 사용합니다.

CLI는 다음과 같이 실행합니다.

```powershell
python main.py
python main.py --start-date 2026-07-08 --end-date 2026-07-08 --regions 충청북도 --max-issues 1
```

실행 중 생성되는 Excel, 체크포인트, SQLite, 로그와 스크린샷은 각각 `output/`, `work/`, `artifacts/`에 저장됩니다.

## 설치 검증

```powershell
python -m pytest -q

cd frontend
npm test
npm run build
cd ..
```

Chromium 실행 오류가 나오면 `python -m playwright install chromium`을 다시 실행합니다. 프런트엔드가 없다는 안내가 나오면 `frontend`에서 `npm ci`와 `npm run build`를 실행합니다.

## 제출 ZIP 생성

프로젝트 루트에서 다음 명령을 실행합니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\package_source.ps1
```

`release/bigkinds_regional-v1.0.3-source.zip`과 같은 이름의 `.sha256` 파일이 생성됩니다.

제출본에는 실행 진입점, `src/`, 프런트엔드 소스와 설정, 테스트, 스크립트, 의존성 파일 및 현재 문서만 포함됩니다. 다음 로컬·생성 자료는 포함하지 않습니다.

- `.git`, `.idea`, `.venv`, `AGENTS.md`
- `frontend/node_modules`, `frontend/dist`
- `output`, `work`, `artifacts`, `release`
- `docs/archive`, `.ai`, `.playwright-mcp`, 캐시
- Excel, DB, 로그, 스크린샷, 기존 ZIP, HWP, ICO

제출 전 ZIP의 SHA-256 값과 함께 전달하면 파일 손상 여부를 확인할 수 있습니다.

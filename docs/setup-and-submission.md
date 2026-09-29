# 설치·제출 안내

## 일반 사용자: 압축 해제 후 실행

대상은 Windows 10/11 64비트입니다. **사용자용 `windows-x64.zip`**에는 Python, Python 패키지, Playwright Chromium과 빌드된 화면이 포함됩니다. Python·Node.js·Chrome을 따로 설치할 필요가 없습니다. 최초 실행 시 추가 설치나 다운로드도 하지 않습니다. 실제 링크 점검에는 인터넷 연결이 필요합니다.

1. 제작자가 전달한 `bigkinds_regional-v1.1.0-windows-x64.zip`을 받습니다. `source.zip`은 개발자용 소스이므로 구분해 주세요.
2. 문서 폴더 등 쓰기 가능한 위치에 **전체 압축을 풉니다**. ZIP 미리보기 안에서 실행하거나 `실행.bat`만 꺼내 실행하지 마세요.
3. 풀린 폴더의 **`실행.bat`을 더블클릭**합니다. 기본 브라우저에 점검 화면이 열립니다.
4. 실행 창은 열어 둡니다. 작업을 마친 뒤 실행 창에서 `Ctrl+C`로 서버를 종료합니다.

화면이 열리지 않으면 브라우저에서 `http://127.0.0.1:8000`에 접속하세요. 실행 파일 누락은 ZIP 전체를 다시 풀고, 저장 권한 오류는 쓰기 가능한 폴더로 옮겨 해결합니다. 포트가 사용 중이면 기존 점검기 창을 확인하세요. 다른 프로그램과 충돌하면 터미널에서 `실행.bat --port 8080`으로 실행할 수 있습니다.

결과는 `output/`, 기록과 로그는 `work/`, 스크린샷은 `artifacts/`에 저장됩니다. 실행 오류를 문의할 때는 실행 창의 오류와 `work/server_logs/` 로그를 함께 전달하세요.

### 기존 기록을 유지하며 업데이트

현재 DB에는 보고서와 체크포인트의 **절대경로**가 저장됩니다. 기록을 유지할 때는 최종 설치 경로를 그대로 사용해야 합니다.

1. 진행 중인 점검을 중단하고 저장이 끝난 뒤 서버를 종료합니다.
2. 기존 프로그램 폴더를 백업합니다.
3. 새 ZIP을 풀고, 새 프로그램 폴더가 **기존 프로그램과 동일한 경로**에 위치하게 합니다.
4. 백업에서 `output`, `work`, `artifacts` 폴더를 새 프로그램 폴더로 함께 복사합니다.
5. 다시 실행해 기록과 Excel 다운로드를 확인한 뒤 백업 보관 여부를 결정합니다.

서로 다른 두 설치본을 같은 데이터로 동시에 실행하지 마세요. 다른 경로로 옮기면 과거 결과의 다운로드·재개 경로가 자동 변경되지 않습니다. 자동 업데이트는 제공하지 않습니다.

## 제작자: 사용자용 ZIP 생성

Windows x64 Python 3.12와 Node.js가 있는 제작 PC에서 실행합니다. 기존 `.venv`를 기본 빌드용 Python으로 사용하며, 다른 경로는 `-BuildPython`으로 지정합니다.

별도로 준비한 **공식 Windows x64 Python 3.12 embeddable ZIP**과 신뢰할 수 있는 출처에서 확인한 SHA-256이 필요합니다. 아래 경로와 해시는 실제 값으로 바꿉니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\package_portable.ps1 `
  -PythonArchive "C:\Downloads\python-3.12.x-embed-amd64.zip" `
  -PythonArchiveSha256 "실제_64자리_SHA256"
```

스크립트는 다음을 수행합니다.

- ZIP 해시와 Python 3.12·64비트를 검증하고 별도 임시 폴더에 실행 환경 구성
- `requirements.lock.txt`의 정확한 버전 설치 및 해당 Playwright의 Chromium 설치
- `npm ci`, `npm run build` 실행 및 화면·라이선스·버전 정보 포함
- 번들 Python의 spawn 프로세스에서 Chromium headless/headed, SQLite와 Excel 검사
- 실제 로컬 서버의 API와 HTML·JS·CSS 제공 검사
- 생성 ZIP을 한글·공백 경로에 다시 풀고 Python·Node.js를 PATH에서 제외한 상태로 재검사
- 모든 검사가 통과한 경우에만 `release/`에 ZIP과 `.sha256` 게시

제작 과정에는 패키지 다운로드용 인터넷과 충분한 임시 디스크 공간이 필요합니다. 잠금 버전 설치가 실패하면 최신 버전으로 대체하지 않고 중단합니다. 같은 이름의 기존 배포물은 덮어쓰지 않습니다. 다시 제작할 때는 `-OutputDirectory`로 새 출력 폴더를 지정할 수 있습니다.

ZIP의 `release-info.json`에는 제품·Python 버전, 입력 ZIP과 의존성 파일 해시 및 제작 시각이 기록됩니다. `.venv`, 기존 결과·DB·로그, 개발자용 `node_modules`는 포함하지 않습니다. 라이선스 위치는 `THIRD-PARTY-NOTICES.txt`에서 확인합니다.

### 배포 전 최종 확인

자동 검증 외에 Python·Node.js가 없는 Windows 일반 사용자 PC에서 다음을 확인한 뒤 배포하세요. 제작 PC의 PATH 격리 검사는 새 PC 검증을 대신하지 않습니다.

- ZIP 전체 압축 해제 후 `실행.bat` 더블클릭과 화면 자동 열기
- 짧은 날짜 범위·한 지역 점검의 진행 표시, 중단, Excel 다운로드
- 서버 재실행 후 기록 복원과 이전 Excel 다운로드
- 설치 경로를 유지한 업데이트 후 기록·다운로드 보존

## 개발자용 지원 환경

- Windows 10/11 64비트
- Python 3.12.x
- Node.js `^20.19.0` 또는 `22.12.0` 이상
- 의존성 설치와 실제 링크 점검을 위한 인터넷 연결

시스템 Chrome을 별도로 준비할 필요는 없습니다. Playwright 명령으로 프로젝트 전용 Chromium을 설치합니다.

## 개발자용 소스 설치

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

`release/bigkinds_regional-v1.1.0-source.zip`과 같은 이름의 `.sha256` 파일이 생성됩니다. 이 ZIP은 사용자용 실행 배포물과 별개입니다.

제출본에는 실행 진입점, `src/`, 프런트엔드 소스와 설정, 테스트, 스크립트, 의존성 파일 및 현재 문서만 포함됩니다. 다음 로컬·생성 자료는 포함하지 않습니다.

- `.git`, `.idea`, `.venv`, `AGENTS.md`
- `frontend/node_modules`, `frontend/dist`
- `output`, `work`, `artifacts`, `release`
- `docs/archive`, `.ai`, `.playwright-mcp`, 캐시
- Excel, DB, 로그, 스크린샷, 기존 ZIP, HWP, ICO

제출 전 ZIP의 SHA-256 값과 함께 전달하면 파일 손상 여부를 확인할 수 있습니다.

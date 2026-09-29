# Railway 배포 설정

이 저장소의 `Dockerfile`은 Node 24.18.0에서 프런트엔드를 빌드한 뒤 Playwright Python `v1.61.0-noble` 이미지에 FastAPI, Chromium, Xvfb와 빌드 결과를 담습니다. Python 패키지는 이미지 안의 가상환경에 설치합니다. `requirements.lock.txt`의 `playwright==1.61.0`과 이미지 버전을 함께 유지하세요. 서버는 이미지의 `tini`를 거쳐 `python run_web.py`로 시작하며, `PORT`에 바인딩하고 프런트엔드와 API를 같은 서비스에서 제공합니다.

## Railway에서 설정할 항목

1. 저장소로 **웹 서비스 하나**를 만들고 Dockerfile 빌드를 선택합니다. `railway.toml`이 `/api/health` 검사, 실패 시 재시작(최대 3회)을 지정합니다. Railway의 **Start Command는 비워 두어** 이미지의 `tini` 진입점을 사용합니다.
2. 서비스에 Volume 하나를 만들고 **`/data`**에 연결합니다. 이미지에는 `BIGKINDS_RUNTIME=server`, `BIGKINDS_DATA_DIR=/data`가 설정되어 있습니다. `PORT`는 Railway가 제공합니다. 환경변수 `ALLOWED_HOSTS`에는 실제 서비스 도메인을 정확히 지정합니다. Railway health check의 Host가 `healthcheck.railway.app`이라면 이 값도 쉼표로 추가합니다. `BIGKINDS_ACCESS_PASSWORD`에는 추측하기 어려운 비밀번호를 설정합니다. 예: `ALLOWED_HOSTS=example.up.railway.app,healthcheck.railway.app`. 배포 도메인이 바뀌면 이 값도 갱신합니다.
3. **Replica 1개**, **RAM 최소 2 GB**로 시작하고 브라우저 충돌이나 메모리 부족이 확인되면 **4 GB**로 높입니다. 서비스의 자동 수평 확장은 사용하지 않습니다. SQLite와 단일 작업 관리자 구조는 여러 replica 간 동시 실행을 지원하지 않습니다.
4. Railway에서 `/dev/shm` 크기를 직접 조정할 수 있다면 최소 256 MB를 지정합니다. 해당 설정을 제공하지 않는 환경에서도 서버 Chromium은 `--disable-dev-shm-usage`로 공유 메모리 부족 시 `/tmp`를 사용하도록 설정되어 있습니다. `/tmp` 사용량과 메모리를 모니터링하세요.

`/data/work/web_jobs.sqlite3`에 작업 이력, `/data/work/`에 체크포인트와 로그, `/data/output/`에 Excel, `/data/artifacts/`에 스크린샷과 필요한 artifact가 저장됩니다. Volume이 연결되지 않으면 재배포 후 파일이 사라질 수 있습니다. 기본 인증 사용자 이름은 `bigkinds`, 비밀번호는 `BIGKINDS_ACCESS_PASSWORD`입니다. `/api/health`만 인증 없이 응답합니다.

## 첫 배포 점검

이미지 빌드가 끝나면 Dockerfile이 `scripts/playwright_runtime_smoke.py`를 실행해 Chromium 시작, context/page 생성, `data:` 문서의 DOM 확인과 종료까지 검사합니다. 이 검사는 외부 사이트에 접속하지 않으며 Railway health check 때는 실행하지 않습니다. 필요하면 이미지에서 `docker run --rm bigkinds-regional:railway python scripts/playwright_runtime_smoke.py`로 다시 실행할 수 있습니다.

배포가 완료되면 `/api/health`가 HTTP 200인지 확인하고, 공개 도메인에서 기본 인증 후 화면과 `/api/config/regions`가 열리는지 확인합니다.

로컬 Docker 검증 시에는 다음처럼 `/data` Volume과 `--shm-size=256m`을 사용합니다. 저장소 루트에 Git에서 제외되는 `.env.railway.local` 파일을 만들고 `ALLOWED_HOSTS=localhost,127.0.0.1`과 `BIGKINDS_ACCESS_PASSWORD=로컬_검증용_비밀번호`를 적습니다.

```powershell
docker build -t bigkinds-regional:railway .
docker volume create bigkinds-regional-data
docker run --rm -p 8000:8000 --shm-size=256m --env-file .env.railway.local -v bigkinds-regional-data:/data bigkinds-regional:railway
```

Windows 로컬 실행은 기존처럼 `python run_web.py`로 시작합니다.

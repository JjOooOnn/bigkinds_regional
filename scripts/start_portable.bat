@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONDONTWRITEBYTECODE=1
title 빅카인즈 링크 점검
pushd "%~dp0"
if errorlevel 1 goto directory_error
if not exist "runtime\python.exe" goto missing_files
if not exist "portable_launcher.py" goto missing_files
echo.
echo 빅카인즈 링크 점검을 시작합니다.
echo 이 창을 열어 두세요. 종료하려면 Ctrl+C를 누르세요.
echo.
"runtime\python.exe" -B -X utf8 "portable_launcher.py" %*
set "result=%errorlevel%"
echo.
echo 프로그램이 종료되었습니다. 오류가 있다면 위의 안내를 확인해 주세요.
popd
pause
exit /b %result%

:missing_files
echo [오류] 필수 실행 파일이 없습니다. ZIP 전체를 새 폴더에 풀어 주세요.
popd
pause
exit /b 1

:directory_error
echo [오류] 실행 폴더에 접근할 수 없습니다. 쓰기 가능한 폴더에 압축을 풀어 주세요.
pause
exit /b 1

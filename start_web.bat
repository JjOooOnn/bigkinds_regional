@echo off
chcp 65001 >nul
set PYTHONUTF8=1

title 빅카인즈 링크 점검

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo [오류] 가상환경을 찾을 수 없습니다.
    echo 경로: %~dp0.venv
    echo.
    pause
    exit /b 1
)

echo.
echo 빅카인즈 링크 점검 서버를 시작합니다.
echo 이 창을 닫으면 서버가 종료됩니다.
echo.
echo 로컬 사용자 페이지: http://127.0.0.1:8000
echo 종료하려면 이 창에서 Ctrl+C를 누르세요.
echo.

".venv\Scripts\python.exe" run_web.py

echo.
echo 서버가 종료되었습니다.
pause
@echo off
REM Ridikc Video Toolkit - launcher Windows (GUI clipper)
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    set "PY=python"
)

%PY% -c "import flask, dotenv" >nul 2>&1
if not %errorlevel%==0 (
    echo [RCH] Dependency dasar belum ada. Jalankan: %PY% -m pip install -r requirements-dev.txt
    echo [RCH] Untuk fitur clip penuh, jalankan: %PY% -m pip install -r requirements.txt
    pause
    exit /b 1
)

%PY% -c "import moviepy.editor" >nul 2>&1
if not %errorlevel%==0 (
    echo [RCH] moviepy belum terpasang - pipeline clip tidak akan jalan.
    echo [RCH] pasang dengan: %PY% -m pip install -r requirements.txt
    echo.
)

echo [RCH] Membuka GUI clip di http://127.0.0.1:8787
echo [RCH] Tutup jendela ini untuk menghentikan server.
%PY% -m clipper.app

endlocal
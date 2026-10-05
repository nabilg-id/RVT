@echo off
REM Ridikc Video Toolkit - launcher Windows (GUI)
setlocal enabledelayedexpansion

REM The Desktop copy of this file cannot use "%~dp0" to find the repository:
REM once copied there, %~dp0 is the Desktop, so `py -3 -m clipper.app` found no
REM package and the console closed on the error. The installer passes the real
REM folder in as RCH_REPO; the probes below only run when it is absent, which is
REM the case of someone double-clicking this file inside launchers\ directly.
if not defined RCH_REPO set "RCH_REPO=%~dp0.."
if "%RCH_REPO:~-1%"=="\" set "RCH_REPO=%RCH_REPO:~0,-1%"

if not exist "%RCH_REPO%\clipper\app.py" (
    echo.
    echo [RCH] Tidak menemukan folder aplikasi di:
    echo        "%RCH_REPO%"
    echo.
    echo [RCH] Launcher ini harusnya dibuat oleh setup-gui.bat, yang menulis
    echo        folder aplikasi ke dalamnya. Jalankan setup-gui.bat sekali lagi,
    echo        atau jalankan GUI manual dari folder repository:
    echo.
    echo        cd /d "%RCH_REPO%"
    echo        py -3 -m clipper.app
    echo.
    pause
    exit /b 1
)

cd /d "%RCH_REPO%" || (
    echo [RCH] Gagal masuk ke folder: "%RCH_REPO%"
    pause
    exit /b 1
)

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    set "PY=python"
)

REM Dependency check runs the real import, so a failure means the app genuinely
REM cannot start - worth saying out loud instead of closing the window.
%PY% -c "import flask, dotenv" >nul 2>&1
if not %errorlevel%==0 (
    echo.
    echo [RCH] Dependency dasar tidak ditemukan ^(flask / dotenv^).
    echo        Jalankan:
    echo        %PY% -m pip install -r requirements-dev.txt
    echo.
    pause
    exit /b 1
)

%PY% -c "import moviepy.editor" >nul 2>&1
if not %errorlevel%==0 (
    echo [RCH] moviepy tidak ditemukan - pipeline clip tidak akan jalan.
    echo [RCH] pasang dengan: %PY% -m pip install -r requirements.txt
    echo.
)

echo [RCH] Membuka GUI di http://127.0.0.1:8787
echo [RCH]   /          Clipper
echo [RCH]   /download  Downloader
echo [RCH] Tutup jendela ini untuk menghentikan server.
echo.
%PY% -m clipper.app
if not %errorlevel%==0 (
    echo.
    echo [RCH] GUI berhenti dengan error %errorlevel%.
    pause
)

endlocal
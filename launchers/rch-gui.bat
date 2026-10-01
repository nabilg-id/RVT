@echo off
REM Ridikc Content Harvester - launcher Windows
REM Menjalankan GUI web lokal dan membukanya di browser.
setlocal

cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    set "PY=python"
)

%PY% -c "import flask" >nul 2>nul
if not %errorlevel%==0 (
    echo [RCH] Dependency belum terpasang. Jalankan setup-gui.bat terlebih dahulu.
    pause
    exit /b 1
)

echo [RCH] Menjalankan GUI di http://127.0.0.1:8787
echo [RCH] Tutup jendela ini untuk menghentikan server.
%PY% -m rch.web

endlocal

@echo off
REM Ridikc Content Harvester - installer Windows
REM Memeriksa Python, memasang dependency, yt-dlp, dan membuat shortcut desktop.
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo  Ridikc Content Harvester - Setup (Windows)
echo ============================================
echo.

REM --- 1. Python ---
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [1/4] Python tidak ditemukan. Mencoba memasangnya via winget...
  winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
  if errorlevel 1 (
    echo.
    echo [GAGAL] Instal Python 3.10+ manual dari https://www.python.org/downloads/
    echo        Pastikan centang "Add Python to PATH" saat instalasi.
    pause
    exit /b 1
  )
  set "PY=py -3"
)
for /f "tokens=2" %%v in ('%PY% --version') do set "PYVER=%%v"
echo [1/4] Python !PYVER! - OK
echo.

REM --- 2. Dependencies ---
echo [2/4] Memasang dependency dari requirements.txt...
%PY% -m pip install --upgrade pip --quiet
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
  echo [GAGAL] Instalasi dependency gagal.
  pause
  exit /b 1
)
echo.

REM --- 3. yt-dlp ---
echo [3/4] Memperbarui yt-dlp ke versi terbaru...
%PY% -m pip install --upgrade yt-dlp --quiet
%PY% -m yt_dlp --version >nul 2>nul
if errorlevel 1 (
  echo [PERINGATAN] yt-dlp tidak terverifikasi. Unduhan mungkin gagal.
) else (
  echo yt-dlp - OK
)
echo.

REM --- 4. Shortcut desktop ---
echo [4/4] Membuat shortcut desktop...
set "SHORTCUT=%USERPROFILE%\Desktop\RCH-GUI.bat"
copy /y "launchers\rch-gui.bat" "%SHORTCUT%" >nul
if exist "%SHORTCUT%" (
  echo Shortcut: %SHORTCUT%
) else (
  echo [PERINGATAN] Gagal membuat shortcut desktop.
)
echo.

echo ============================================
echo  Selesai. Buka "RCH-GUI" di Desktop.
echo  GUI terbuka di http://127.0.0.1:8787
echo ============================================
pause
endlocal

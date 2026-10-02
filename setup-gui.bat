@echo off
REM Ridikc Video Toolkit - installer Windows
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo  Ridikc Video Toolkit - Setup (Windows)
echo ============================================
echo.

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo [1/4] Python tidak ditemukan. Memasang via winget...
  winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
  if errorlevel 1 (
    echo [GAGAL] Instal Python 3.10+ manual dari https://www.python.org/downloads/
    pause & exit /b 1
  )
  set "PY=py -3"
)
for /f "tokens=2" %%v in ('%PY% --version') do echo [1/4] Python %%v - OK
echo.

echo [2/4] Memasang dependency dasar + pytest...
%PY% -m pip install --upgrade pip --quiet
%PY% -m pip install -r requirements-dev.txt
if errorlevel 1 ( echo [GAGAL] Instalasi gagal. & pause & exit /b 1 )
echo.

echo [3/4] Memasang dependensi clip (torch, moviepy, whisper, mediapipe).
echo       Ini besar - mungkin perlu 15-30 menit dan ~3 GB disk.
echo       Tekan Ctrl+C untuk melewati dan hanya memakai mode ringan.
%PY% -m pip install -r requirements.txt
if errorlevel 1 echo [PERINGATAN] Dependensi clip gagal; pipeline clip tidak akan jalan.
echo.

echo [4/4] Membuat shortcut desktop...
set "SHORTCUT=%USERPROFILE%\Desktop\VCLIP-GUI.bat"
copy /y "launchers\vclip.bat" "%SHORTCUT%" >nul
if exist "%SHORTCUT%" ( echo Shortcut: %SHORTCUT% ) else ( echo [PERINGATAN] Gagal membuat shortcut. )
echo.

echo ============================================
echo  Selesai. Buka "VCLIP-GUI" di Desktop.
echo  GUI: http://127.0.0.1:8787
echo  CLI: %PY% -m clipper.main
echo ============================================
pause
endlocal
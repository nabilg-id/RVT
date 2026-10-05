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

echo [4/4] Membuat launcher desktop...
REM Launcher DITULIS ULANG, bukan disalin. Salinan memakai "%~dp0" untuk
REM mencari folder aplikasi, dan setelah disalin ke Desktop %~dp0 menjadi
REM Desktop - sehingga import clipper gagal dan jendela langsung ketutup.
REM Yang ditulis di sini memuat folder aplikasi sebagai RCH_REPO, jadi file
REM Desktop-nya tahu harus kemana.
set "LAUNCHER=%CD%\launchers\vclip.bat"
if not exist "%LAUNCHER%" (
  set "LAUNCHER=%~dp0launchers\vclip.bat"
)
for %%F in ("%USERPROFILE%\Desktop\VCLIP-GUI.bat" "%USERPROFILE%\Desktop\RCH-GUI.bat") do (
  if exist %%F del /f /q %%F >nul 2>nul
)
for %%F in ("%USERPROFILE%\Desktop\VCLIP-GUI.bat" "%USERPROFILE%\Desktop\RCH-GUI.bat") do (
  (
    echo @echo off
    echo set "RCH_REPO=%CD:\=\%%"
    echo call "%LAUNCHER%"
  ) > %%F
  if not exist %%F echo [PERINGATAN] Gagal menulis %%F
)
if exist "%USERPROFILE%\Desktop\VCLIP-GUI.bat" echo Shortcut: %USERPROFILE%\Desktop\VCLIP-GUI.bat
if exist "%USERPROFILE%\Desktop\RCH-GUI.bat" echo Shortcut: %USERPROFILE%\Desktop\RCH-GUI.bat
echo.

echo ============================================
echo  Selesai. Buka "VCLIP-GUI" atau "RCH-GUI" di Desktop.
echo  GUI: http://127.0.0.1:8787  (tab / dan /download)
echo  CLI: %PY% -m rch --help
echo ============================================
pause
endlocal
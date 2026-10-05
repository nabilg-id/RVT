@echo off
REM Ridikc Video Toolkit - CLI launcher (Desktop)
REM Memanggil rch.exe lewat path penuh, supaya tidak bentrok dengan package
REM npm bernama "rch" yang juga ada di PATH.
setlocal

REM Temukan folder Scripts Python yang menampung rch.exe. Karena folder ini
REM sering TIDAK ada di PATH, path penuh diambil lewat interpreter `py`.
for /f "delims=" %%S in ('py -3 -c "import sysconfig; print(sysconfig.get_path('scripts'))" 2^>nul') do set "SCRIPTS=%%S"

if not defined SCRIPTS (
    echo [RVT] Tidak menemukan interpreter Python. Pastikan "py" terpasang.
    pause
    exit /b 1
)

if not exist "%SCRIPTS%\rch.exe" (
    echo [RVT] rch.exe tidak ditemukan di:
    echo        "%SCRIPTS%"
    echo.
    echo [RVT] Install dulu:  py -3 -m pip install -e .
    echo        lalu jalankan lagi.
    echo.
    pause
    exit /b 1
)

"%SCRIPTS%\rch.exe" %*
exit /b %errorlevel%
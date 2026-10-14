@echo off
REM ============================================================
REM  build_exe.bat - build audio_speed_calc.exe via PyInstaller
REM  Works on Windows 7/8/10/11 with Python 3.8+
REM ============================================================
setlocal
cd /d "%~dp0"

echo ============================================================
echo  Building audio_speed_calc.exe
echo ============================================================
echo.

REM --- Check Python ---
where python >nul 2>nul
if errorlevel 1 (
    echo [x] Python not found in PATH.
    echo     Install Python 3.8+ and add it to PATH.
    pause
    exit /b 1
)

REM --- Show Python and PyInstaller versions ---
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo [i] %%v
for /f "tokens=*" %%v in ('python -c "import PyInstaller; print(\"PyInstaller \" + PyInstaller.__version__)" 2^>^&1') do echo [i] %%v
echo.

REM --- Check PyInstaller ---
python -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo [!] PyInstaller not found. Installing...
    python -m pip install --upgrade pyinstaller
    if errorlevel 1 (
        echo [x] Cannot install PyInstaller.
        pause
        exit /b 2
    )
)

REM --- Check bin\ contents ---
echo [i] External binaries in bin\:
if exist "bin\ffmpeg.exe"   (echo     ffmpeg.exe   - OK) else (echo     ffmpeg.exe   - MISSING)
if exist "bin\ffprobe.exe"  (echo     ffprobe.exe  - OK) else (echo     ffprobe.exe  - MISSING)
if exist "bin\exiftool.exe" (echo     exiftool.exe - OK) else (echo     exiftool.exe - MISSING)
echo.

REM --- Clean previous build ---
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

REM --- Run PyInstaller ---
echo [i] Running PyInstaller...
echo.
pyinstaller audio_speed_calc.spec --clean --noconfirm
if errorlevel 1 (
    echo.
    echo [x] Build FAILED. See errors above.
    pause
    exit /b 3
)

echo.
echo ============================================================
echo  BUILD COMPLETE
echo ============================================================
if exist "dist\audio_speed_calc.exe" (
    for %%A in ("dist\audio_speed_calc.exe") do (
        echo   File: %%~fA
        echo   Size: %%~zA bytes
    )
) else (
    echo   [!] dist\audio_speed_calc.exe not found.
)

echo.
echo   Test the .exe on a machine WITHOUT Python.
echo.

if exist dist explorer dist

pause
endlocal

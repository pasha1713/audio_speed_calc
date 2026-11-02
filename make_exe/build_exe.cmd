@echo off
chcp 65001 >nul
setlocal

echo ========================================
echo  Сборка audio_speed_calc.exe
echo ========================================
echo.

pyinstaller --version >nul 2>&1
if errorlevel 1 (
    echo ОШИБКА: PyInstaller не найден.
    pause
    exit /b 1
)

echo [1/8] Сборка главного приложения...
pyinstaller audio_speed_calc.spec --clean --noconfirm --distpath ..

echo [2/8] Сборка ts_integrity_check.exe...
pyinstaller --onefile --noconfirm --distpath ..\dist\audio_speed_calc ..\ts_integrity_check.py

echo [3/8] Сборка otchet_0x300_для_ssa_26.exe...
pyinstaller --onefile --noconfirm --distpath ..\dist\audio_speed_calc ..\otchet_0x300_для_ssa_26.py

echo [4/8] Сборка ssa_shift.exe...
pyinstaller --onefile --noconfirm --distpath ..\dist\audio_speed_calc ..\ssa_shift.py

echo [5/8] Сборка ssa_add_audio_speed.exe...
pyinstaller --onefile --noconfirm --distpath ..\dist\audio_speed_calc ..\ssa_add_audio_speed.py

echo [6/8] Сборка ssa_frame_time.exe...
pyinstaller --onefile --noconfirm --distpath ..\dist\audio_speed_calc ..\ssa_frame_time.py

echo [7/8] Сборка tsp_frag_calc.exe...
pyinstaller --onefile --noconfirm --distpath ..\dist\audio_speed_calc ..\tsp_frag_calc.py

echo [8/8] Сборка patch_otchet_tolerant.exe...
pyinstaller --onefile --noconfirm --distpath ..\dist\audio_speed_calc ..\patch_otchet_tolerant.py

echo.
echo ========================================
echo  СБОРКА ЗАВЕРШЕНА!
echo  Все .exe лежат в ..\dist\audio_speed_calc\
echo ========================================
pause
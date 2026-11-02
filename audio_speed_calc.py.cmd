@echo off
goto end
rem copy audio_speed_calc_v0.49.py audio_speed_calc.py 
if exist c:audio_speed_calc_*.py move c:audio_speed_calc_*.py audio_speed_calc.py
if exist c:audio_speed_calc.py move c:audio_speed_calc.py .

rem audio_speed_calc.py 20260602050758_000229A.TS >rez_4k.txt
rem audio_speed_calc.py 20260922052548_000335.TS >rez_2k_112.txt
rem audio_speed_calc.py 20260922052448_000334.TS >rez_2k_112.txt
:end
@echo on
rem audio_speed_calc.py 20261023113149_000459.TS >rez.txt
audio_speed_calc.exe 20250826213804_000249.TS >rez_26.08.2025.txt
rem 2>rez_err.txt

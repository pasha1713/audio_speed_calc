audio_speed_calc — сборка .exe
================================

Папка exe/ содержит всё для сборки audio_speed_calc.exe:

  audio_speed_calc.spec   PyInstaller spec (пути ../ от exe/)
  build_exe.cmd           обёртка для сборки одной командой
  build/                  промежуточные файлы PyInstaller
  backup/                 старые версии spec/cmd
  requirements*.txt       зависимости (если были в корне)

Исходники (audio_speed_calc.py, make_disk_py.py, ...), gpx.fmt
и bin/*.exe остаются в родительской папке (../).

СБОРКА
------
Из папки exe/:

    build_exe.cmd

или:

    pyinstaller audio_speed_calc.spec --clean --noconfirm --distpath ..

Готовый .exe: ../dist/audio_speed_calc.exe

ТРЕБОВАНИЯ
----------
  * Python 3.8.x (последняя для Windows 7)
  * PyInstaller 5.13.2
  * pip install -r requirements.txt (если файл есть)

ОЧИСТКА
-------
build/ можно удалить в любой момент — PyInstaller создаст заново.
dist/ НЕ удалять — там готовый .exe.

  rmdir /s /q build

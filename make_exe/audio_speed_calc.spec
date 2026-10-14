# -*- mode: python ; coding: utf-8 -*-
"""
audio_speed_calc.spec - PyInstaller build spec.

Собирает ОДИН .exe со встроенными ffmpeg/ffprobe/exiftool из ./bin/.

Совместимость:
  - PyInstaller 5.x  (Python 3.8+)
  - PyInstaller 6.x  (Python 3.9+)

Сборка:
    pyinstaller audio_speed_calc.spec --clean --noconfirm

Готовый файл:
    dist/audio_speed_calc.exe
"""

import os

# ------------------------------------------------------------------
# Внешние бинарники из ./bin/
# ------------------------------------------------------------------
binaries = []
for _name in ('ffmpeg', 'ffprobe', 'exiftool'):
    _win = _name + ('.exe' if os.name == 'nt' else '')
    for _dir in ('bin', '.'):
        _src = os.path.join(_dir, _win)
        if os.path.isfile(_src):
            binaries.append((_src, 'bin'))
            break

# ------------------------------------------------------------------
# Скрытые импорты
# ------------------------------------------------------------------
hiddenimports = [
    'sys_fingerprint',
    'tkinter',
    'tkinter.filedialog',
    'tkinter.messagebox',
    'tkinter.simpledialog',
    'matplotlib.backends.backend_tkagg',
]

# ------------------------------------------------------------------
# Исключения - не тянуть лишнее
# ------------------------------------------------------------------
excludes = [
    'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
    'IPython', 'jupyter', 'notebook', 'nbformat',
    'pytest', 'sphinx', 'docutils',
]

# ------------------------------------------------------------------
# Analysis - БЕЗ optimize=0 (нет в PyInstaller 5.x)
# ------------------------------------------------------------------
a = Analysis(
    ['audio_speed_calc.py'],
    pathex=['.'],
    binaries=binaries,
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='audio_speed_calc',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
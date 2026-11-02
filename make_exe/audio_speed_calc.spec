# -*- mode: python ; coding: utf-8 -*-
"""
audio_speed_calc.spec — PyInstaller build spec (Windows 7, Python 3.8, PyInstaller 5.x)

Собирает ОДИН .exe со встроенными ffmpeg/ffprobe/exiftool из ./bin/.
Включает все вспомогательные Python-модули и gpx.fmt.

Сборка:
    pyinstaller audio_speed_calc.spec --clean --noconfirm

Готовый файл:
    dist/audio_speed_calc.exe
"""

import os

# ------------------------------------------------------------------
# Внешние бинарники из ./bin/ (ffmpeg, ffprobe, exiftool)
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
# Данные: gpx.fmt (шаблон для exiftool) + шрифты matplotlib
# ------------------------------------------------------------------
datas = []
# gpx.fmt — кладём в корень _MEIPASS, чтобы _find_exec('gpx.fmt') его нашёл
if os.path.isfile('gpx.fmt'):
    datas.append(('gpx.fmt', '.'))

# Шрифты matplotlib (если есть проблемы с русским текстом)
try:
    import matplotlib
    _mpl_data = os.path.join(os.path.dirname(matplotlib.__file__), 'mpl-data')
    if os.path.isdir(_mpl_data):
        datas.append((_mpl_data, 'matplotlib/mpl-data'))
except Exception:
    pass

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
    # Новые модули проекта
    'make_disk_py',
    'merge_ts_ssa_v2',
    'ssa_frame_time',
    'ssa_shift',
    'patch_otchet_tolerant',
    'ssa_add_audio_speed',
    'tsp_frag_calc',
]

# ------------------------------------------------------------------
# Исключения — не тянуть лишнее
# ------------------------------------------------------------------
excludes = [
    'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
    'IPython', 'jupyter', 'notebook', 'nbformat',
    'pytest', 'sphinx', 'docutils',
]

# ------------------------------------------------------------------
# Analysis
# ------------------------------------------------------------------
a = Analysis(
    ['audio_speed_calc.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
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
    console=True,                # нужен для логов Make Disk
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

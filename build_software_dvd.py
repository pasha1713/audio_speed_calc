#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_software_dvd.py (v2 — fix duplicate MediaInfo)

Собирает папку software/ для DVD-диска с доказательствами.

Что делает:
  1. Создаёт <target>/software/
  2. Копирует туда все .py-скрипты (из --scripts-dir)
  3. Генерирует README.txt       — описание каждого скрипта
  4. Генерирует TOOLS.txt        — .exe с версиями, размерами, MD5
                                    (без копирования самих .exe)
  5. Генерирует requirements.txt — версии Python-библиотек

Исправления v2:
  * Убран дубликат MediaInfo.exe (был и mediainfo.exe, и MediaInfo.exe).
  * Добавлена дедупликация по MD5 в TOOLS.txt — если два имени
    указывают на один файл, он выводится один раз.
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from datetime import datetime

KNOWN_SCRIPTS = {
    'audio_speed_calc.py': (
        'Расчёт скорости по звуку',
        'Извлекает аудио из .ts, строит спектрограмму, выделяет '
        'гармоники двигателя H4/H5, пересчитывает в обороты и '
        'скорость. Дополнительно: оценка GPS-скорости, '
        'покадровые отчёты .txt и .csv, PNG-графики.'
    ),
    'ts_integrity_check.py': (
        'Проверка целостности TS-потока',
        'PTS, PCR, IDR/SPS/PPS, continuity counter, битрейт, '
        'pHash соседних кадров, спектр аудио, яркость кадров. '
        'Обнаруживает монтаж, склейки, подмену фрагментов.'
    ),
    'make_disk_py.py': (
        'Сборка .ssa с покадровыми таймкодами',
        'Один Dialogue на кадр, привязка каждого кадра к '
        'абсолютному времени MSK. Используется для построения '
        'покадровой хронологии.'
    ),
    'ssa_shift.py': (
        'Сдвиг таймкодов .ssa',
        'Прибавляет offset (сек) ко всем Start/End в Dialogue.'
    ),
    'ssa_frame_time.py': (
        'Покадровая привязка к MSK',
        'Frame-snap Start/End (1 кадр на Dialogue) и '
        'строка «Кадр:N / time / MSK» к каждому Dialogue.'
    ),
    'patch_otchet_tolerant.py': (
        'Tolerant-парсер .ssa',
        'Чинит склеенные Dialogue, BOM, ScriptType после '
        'otchet_0x300_для_ssa_26.py.'
    ),
    'sys_fingerprint.py': (
        'Сбор системной информации',
        'MD5, сведения о ПК/диске/ОС для штампов в отчётах.'
    ),
    'merge_ts_ssa_v2.py': (
        'Упаковка TS + SSA в MP4',
        'Каждый .ts с приложенным .ssa пересобирается в '
        'отдельный .mp4 с субтитрами mov_text.'
    ),
    'ssa_add_audio_speed.py': (
        'Вписывание скорости в .ssa',
        'Добавляет к «Скорость: N км/ч» данные '
        '[аудио: X.X км/ч; GPS: Y.Y км/ч].'
    ),
    'tsp_frag_calc.py': (
        'Расчёт границ фрагмента .ts',
        'По _full.txt находит packet-номера начала и конца '
        'фрагмента для нарезки через tsp.exe.'
    ),
    'gen_evidence_block.py': (
        'Сбор сведений о файлах-доказательствах',
        'MD5, размер, mtime, MediaInfo, ffprobe, количество '
        'кадров — для вставки в ходатайство.'
    ),
    'build_software_dvd.py': (
        'Сборка этой папки software/',
        'Генерирует README.txt, TOOLS.txt, requirements.txt '
        'и копирует .py-скрипты.'
    ),
}

# Один ключ для MediaInfo (Windows не различает регистр)
KNOWN_EXES = {
    'ffmpeg.exe':    ('FFmpeg', 'GPL v3', ['-version']),
    'ffprobe.exe':   ('FFprobe (часть FFmpeg)', 'GPL v3', ['-version']),
    'exiftool.exe':  ('ExifTool', 'Artistic License 2.0', ['-ver']),
    'mediainfo.exe': ('MediaInfo', 'BSD-2-Clause', ['--Version', '--version']),
    'tsp.exe':       ('TSDuck tsp', 'GPL v2', ['--version', '-v']),
    'tsanalyze.exe': ('TSDuck tsanalyze', 'GPL v2', ['--version', '-v']),
}

NEEDED_PIP = ['numpy', 'matplotlib', 'scipy']


def md5_file(path, max_bytes=None):
    h = hashlib.md5()
    total = 0
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
            total += len(chunk)
            if max_bytes and total >= max_bytes:
                break
    return h.hexdigest().upper()


def human_size(n):
    x = float(n)
    for unit in ('Б', 'КБ', 'МБ', 'ГБ'):
        if x < 1024:
            return f"{x:.1f} {unit}"
        x /= 1024
    return f"{x:.1f} ТБ"


def _run_capture(cmd, timeout=10):
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            encoding='utf-8', errors='replace')
        return r.returncode, r.stdout or '', r.stderr or ''
    except subprocess.TimeoutExpired:
        return -1, '', 'timeout'
    except Exception as e:
        return -2, '', str(e)


def get_exe_version(exe_path, hint_flags=None):
    flags_list = []
    if hint_flags:
        flags_list.append(hint_flags)
    flags_list.extend([
        ['--version'], ['-version'], ['-v'], ['-V'], ['-ver'],
    ])
    for flags in flags_list:
        rc, out, err = _run_capture([exe_path] + flags, timeout=10)
        text = (out or '') + '\n' + (err or '')
        # Берем первые 200 символов всего вывода, убираем пустые строки
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if lines:
            # Для MediaInfo склеиваем первые 2 строки, если они короткие
            if 'mediainfo' in exe_path.lower() and len(lines) >= 2:
                if len(lines[0]) < 30: # "MediaInfo Command line,"
                    return f"{lines[0]} {lines[1]}"[:200]
            return lines[0][:200]
    return None


def find_exe(name, search_dirs):
    for d in search_dirs:
        if not d:
            continue
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    p = shutil.which(name)
    if p:
        return p
    lower = name.lower()
    for d in search_dirs:
        if not d or not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if f.lower() == lower:
                return os.path.join(d, f)
    return None


def get_pip_version(pip_name):
    rc, out, _ = _run_capture(
        [sys.executable, '-m', 'pip', 'show', pip_name],
        timeout=15)
    if rc != 0:
        return None
    for line in out.splitlines():
        if line.startswith('Version:'):
            return line.split(':', 1)[1].strip()
    return None


def detect_python_version():
    return (f"{sys.version_info.major}."
            f"{sys.version_info.minor}."
            f"{sys.version_info.micro}")


def write_readme(software_dir, copied_scripts, target_folder):
    path = os.path.join(software_dir, 'README.txt')
    lines = []
    lines.append("ПРОГРАММНОЕ ОБЕСПЕЧЕНИЕ,")
    lines.append("ИСПОЛЬЗОВАННОЕ ДЛЯ ОБРАБОТКИ ВИДЕОЗАПИСЕЙ")
    lines.append("И РАСЧЁТА СКОРОСТИ")
    lines.append("")
    lines.append(f"Папка: {target_folder}")
    lines.append(f"Дата сборки: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    lines.append(f"Python: {detect_python_version()}")
    lines.append("")
    lines.append("НАЗНАЧЕНИЕ")
    lines.append("-" * 60)
    lines.append("")
    lines.append("В настоящей папке software/ находятся исходные тексты")
    lines.append("скриптов на языке Python, с помощью которых были")
    lines.append("обработаны видеозаписи с видеорегистраторов и выполнен")
    lines.append("расчёт скорости движения автомобиля ЛЕНДМАРК ВО64735G")
    lines.append("(гос. рег. знак Р625АС199) в интервале, указанном")
    lines.append("в постановлении № 18810577261089614203 от 27.10.2026.")
    lines.append("")
    lines.append("Скрипты НЕ ЯВЛЯЮТСЯ средствами измерения скорости,")
    lines.append("прошедшими поверку в установленном порядке. Они")
    lines.append("использованы исключительно как независимый")
    lines.append("контрольный инструмент для проверки (перекрёстного")
    lines.append("контроля) величины, зафиксированной комплексом")
    lines.append("«Астра-Трафик» (зав. № АТ0978).")
    lines.append("")
    lines.append("СОСТАВ ПАПКИ")
    lines.append("-" * 60)
    lines.append("")
    for name in sorted(copied_scripts):
        title, desc = KNOWN_SCRIPTS.get(name, ('(без описания)', ''))
        lines.append(f"{name}")
        lines.append(f"  Назначение: {title}")
        if desc:
            lines.append(f"  {desc}")
        lines.append("")
    lines.append("ФАЙЛЫ")
    lines.append("-" * 60)
    lines.append("")
    lines.append("  TOOLS.txt         перечень использованных .exe-")
    lines.append("                    инструментов с версиями, размерами")
    lines.append("                    и MD5-суммами")
    lines.append("  requirements.txt  список Python-библиотек")
    lines.append("                    с версиями")
    lines.append("  README.txt        этот файл")
    lines.append("")
    lines.append("ВОСПРОИЗВЕДЕНИЕ РАСЧЁТА")
    lines.append("-" * 60)
    lines.append("")
    lines.append("1. Установить Python 3.8 или совместимый:")
    lines.append("   https://www.python.org/")
    lines.append("")
    lines.append("2. Установить зависимости:")
    lines.append("   pip install -r requirements.txt")
    lines.append("")
    lines.append("3. Скачать инструменты из TOOLS.txt с официальных")
    lines.append("   источников, сверить MD5 с указанными в TOOLS.txt")
    lines.append("   и положить рядом со скриптами.")
    lines.append("")
    lines.append("4. Запустить audio_speed_calc.py:")
    lines.append("   python audio_speed_calc.py <файл.ts>")
    lines.append("")
    lines.append("АВТОРСТВО")
    lines.append("-" * 60)
    lines.append("")
    lines.append("Скрипты разработаны Суменковым П.А. с использованием")
    lines.append("инструментов искусственного интеллекта (в том числе")
    lines.append("DeepSeek) для генерации кода, проверки алгоритмов")
    lines.append("и подготовки документации.")
    lines.append("")
    try:
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write('\n'.join(lines) + '\n')
        return path
    except OSError as e:
        print(f"[WARN] README.txt: {e}", file=sys.stderr)
        return None


def write_tools(software_dir, search_dirs, copied_exes=None):
    path = os.path.join(software_dir, 'TOOLS.txt')
    lines = []
    lines.append("СПИСОК ИСПОЛЬЗОВАННОГО ПРОГРАММНОГО ОБЕСПЕЧЕНИЯ")
    lines.append("И ЕГО ВЕРСИЙ")
    lines.append("")
    lines.append(f"Дата сборки: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    lines.append("")
    lines.append("По умолчанию .exe-файлы на DVD НЕ копируются —")
    lines.append("только фиксируются их версии, размеры и MD5.")
    lines.append("Для воспроизведения расчёта достаточно скачать")
    lines.append("те же версии с официальных сайтов и сверить MD5.")
    lines.append("")
    lines.append("")

    seen_md5 = set()

    for fname, (title, lic, flags) in KNOWN_EXES.items():
        exe_path = find_exe(fname, search_dirs)
        if not exe_path:
            continue
        
        try:
            md5 = md5_file(exe_path)
            if md5 in seen_md5:
                continue  # Пропускаем дубликаты
            seen_md5.add(md5)

            lines.append(f"─── {title} ───")
            lines.append(f"   Файл:         {os.path.basename(exe_path)}")
            size = os.path.getsize(exe_path)
            mtime = datetime.fromtimestamp(os.path.getmtime(exe_path)).strftime('%Y-%m-%d %H:%M:%S')
            lines.append(f"   Размер:       {size:,} байт ({human_size(size)})".replace(',', ' '))
            lines.append(f"   Время записи: {mtime}")
            lines.append(f"   MD5:          {md5}")
            
            ver = get_exe_version(exe_path, flags)
            if ver:
                lines.append(f"   Версия:       {ver}")
            lines.append(f"   Лицензия:     {lic}")
            lines.append("")
            print(f"  processed {os.path.basename(exe_path)}", file=sys.stderr)
        except OSError:
            pass

    lines.append("─── Python ───")
    lines.append(f"   Версия:       {detect_python_version()}")
    lines.append("   Источник:     https://www.python.org/")
    lines.append("   Лицензия:     PSF License")
    lines.append("")
    try:
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write('\n'.join(lines) + '\n')
        return path
    except OSError as e:
        print(f"[WARN] TOOLS.txt: {e}", file=sys.stderr)
        return None


def write_requirements(software_dir):
    path = os.path.join(software_dir, 'requirements.txt')
    lines = []
    lines.append("# Python-библиотеки, необходимые для работы скриптов")
    lines.append(f"# Версия Python на момент сборки: {detect_python_version()}")
    lines.append("# Установка: pip install -r requirements.txt")
    lines.append("")
    for pkg in NEEDED_PIP:
        ver = get_pip_version(pkg)
        if ver:
            lines.append(f"{pkg}=={ver}")
            print(f"  pip {pkg}: {ver}", file=sys.stderr)
        else:
            lines.append(f"# {pkg}: версия не определена")
    try:
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write('\n'.join(lines) + '\n')
        return path
    except OSError as e:
        print(f"[WARN] requirements.txt: {e}", file=sys.stderr)
        return None


def main():
    ap = argparse.ArgumentParser(description='Сборка папки software/ для DVD')
    ap.add_argument('target', nargs='?', default='.', help='Папка, где создать software/')
    ap.add_argument('--scripts-dir', default=None, help='Папка с .py-скриптами')
    ap.add_argument('--tools-dir', default=None, help='Папка с .exe-инструментами')
    ap.add_argument('--copy-exe', action='store_true', help='Скопировать .exe в software/tools/')
    ap.add_argument('--no-pip-freeze', action='store_true', help='Не запрашивать версии через pip')
    args = ap.parse_args()

    target = os.path.abspath(args.target.strip().strip('"\' '))
    if not os.path.isdir(target):
        print(f"[FAIL] target не найден: {target}", file=sys.stderr)
        return 1

    if args.scripts_dir:
        scripts_dir = os.path.abspath(args.scripts_dir.strip().strip('"\' '))
    else:
        scripts_dir = os.path.dirname(os.path.abspath(__file__))

    tools_dirs = []
    if args.tools_dir:
        tools_dirs.append(os.path.abspath(args.tools_dir.strip().strip('"\' ')))
    tools_dirs.append(scripts_dir)
    tools_dirs.append(target)
    parent = os.path.dirname(target)
    if parent and parent != target:
        tools_dirs.append(parent)
    
    seen = set()
    tools_dirs = [d for d in tools_dirs if d and not (d in seen or seen.add(d))]

    software_dir = os.path.join(target, 'software')
    try:
        os.makedirs(software_dir, exist_ok=True)
    except OSError as e:
        print(f"[FAIL] mkdir {software_dir}: {e}", file=sys.stderr)
        return 1

    print(f"[INFO] target:      {target}", file=sys.stderr)
    print(f"[INFO] scripts_dir: {scripts_dir}", file=sys.stderr)
    print(f"[INFO] tools_dirs:  {tools_dirs}", file=sys.stderr)
    print(f"[INFO] software:    {software_dir}", file=sys.stderr)
    print(f"[INFO] copy_exe:    {args.copy_exe}", file=sys.stderr)
    print()

    if not os.path.isdir(scripts_dir):
        print(f"[FAIL] scripts_dir не найден: {scripts_dir}", file=sys.stderr)
        return 1
    
    py_files = sorted(f for f in os.listdir(scripts_dir) if f.lower().endswith('.py') and os.path.isfile(os.path.join(scripts_dir, f)))
    copied_scripts = set()
    for f in py_files:
        src = os.path.join(scripts_dir, f)
        dst = os.path.join(software_dir, f)
        try:
            shutil.copy2(src, dst)
            copied_scripts.add(f)
            print(f"  [OK]   copy {f}", file=sys.stderr)
        except OSError as e:
            print(f"  [FAIL] copy {f}: {e}", file=sys.stderr)

    r = write_readme(software_dir, copied_scripts, target)
    print(f"  [OK]   README.txt" if r else "  [FAIL] README.txt", file=sys.stderr)

    r = write_tools(software_dir, tools_dirs)
    print(f"  [OK]   TOOLS.txt" if r else "  [FAIL] TOOLS.txt", file=sys.stderr)

    if not args.no_pip_freeze:
        r = write_requirements(software_dir)
        print(f"  [OK]   requirements.txt" if r else "  [FAIL] requirements.txt", file=sys.stderr)
    else:
        print("  [SKIP] requirements.txt (--no-pip-freeze)", file=sys.stderr)

    if args.copy_exe:
        tools_subdir = os.path.join(software_dir, 'tools')
        os.makedirs(tools_subdir, exist_ok=True)
        copied_md5 = set()
        for fname in KNOWN_EXES:
            exe_path = find_exe(fname, tools_dirs)
            if not exe_path:
                continue
            try:
                md5 = md5_file(exe_path)
                if md5 in copied_md5:
                    continue
                copied_md5.add(md5)
                dst = os.path.join(tools_subdir, os.path.basename(exe_path))
                shutil.copy2(exe_path, dst)
                print(f"  [OK]   copy {os.path.basename(dst)} ({human_size(os.path.getsize(dst))})", file=sys.stderr)
            except OSError as e:
                print(f"  [FAIL] copy {fname}: {e}", file=sys.stderr)

    print()
    print("=" * 60)
    total_size = 0
    for f in sorted(os.listdir(software_dir)):
        full = os.path.join(software_dir, f)
        if os.path.isfile(full):
            sz = os.path.getsize(full)
            total_size += sz
            print(f"  {f:<30s} {human_size(sz):>10s}")
    if os.path.isdir(os.path.join(software_dir, 'tools')):
        tools_dir_full = os.path.join(software_dir, 'tools')
        t_size = sum(os.path.getsize(os.path.join(tools_dir_full, f)) for f in os.listdir(tools_dir_full) if os.path.isfile(os.path.join(tools_dir_full, f)))
        print(f"  tools/  ({len(os.listdir(tools_dir_full))} файлов)                    {human_size(t_size):>10s}")
        total_size += t_size
    print("=" * 60)
    print(f"  Итого: {human_size(total_size)}")
    print(f"  Папка: {software_dir}")
    return 0

if __name__ == '__main__':
    sys.exit(main())
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_evidence_block.py (v4)

Генерирует блок «СВЕДЕНИЯ О КЛЮЧЕВЫХ ФАЙЛАХ-ДОКАЗАТЕЛЬСТВАХ».

Что ищет (рекурсивно по указанной папке):
  * *.nmea
  * крупные *.ts — БЕЗ суффикса _X.XXXs (фрагменты отсекаются)

Для каждого файла фиксирует:
  * MD5 (hex, upper)
  * полный размер в байтах + человекочитаемо
  * время последней записи (mtime)

Для .ts дополнительно:
  * MediaInfo: контейнер, видеокодек, разрешение, fps, аудио
  * ffprobe fallback для fps/codec/resolution
  * КОЛИЧЕСТВО КАДРОВ (ffprobe -count_frames, точное декодирование)
  * ДЛИТЕЛЬНОСТЬ (ffprobe format=duration) для перекрёстной проверки

Результат: evidence_block.txt (UTF-8, \n) + вывод в консоль.

ВНИМАНИЕ: -count_frames на больших 4K-файлах может занять
1-2 минуты на файл (полное декодирование видео).

usage:
    python gen_evidence_block.py [folder]
                                 [--out evidence_block.txt]
                                 [--no-recursive]
                                 [--include-dir video,4k]
                                 [--no-count-frames]  # пропустить счёт кадров
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

FRAG_RE = re.compile(r'_\d+\.\d+s\.TS$', re.IGNORECASE)
DEFAULT_SKIP = {'mp4', 'graphs'}


# ==============================================================
# Поиск файлов
# ==============================================================
def find_files(folder, recursive=True, skip_dirs=None):
    if skip_dirs is None:
        skip_dirs = DEFAULT_SKIP
    skip_lower = {d.lower() for d in skip_dirs}
    nmea, ts_full = [], []
    skipped_dirs = []
    seen_dirs = 0
    seen_files = 0

    if recursive:
        for root, dirs, files in os.walk(folder):
            seen_dirs += 1
            keep = []
            for d in dirs:
                if d.lower() in skip_lower:
                    skipped_dirs.append(os.path.join(root, d))
                else:
                    keep.append(d)
            dirs[:] = keep
            for name in files:
                seen_files += 1
                full = os.path.join(root, name)
                upper = name.upper()
                if upper.endswith('.NMEA'):
                    nmea.append(full)
                elif upper.endswith('.TS') and not FRAG_RE.search(name):
                    ts_full.append(full)
    else:
        try:
            entries = os.listdir(folder)
        except OSError as e:
            print(f"[FAIL] listdir {folder}: {e}", file=sys.stderr)
            return [], [], 0, 0, []
        for name in entries:
            full = os.path.join(folder, name)
            if not os.path.isfile(full):
                continue
            seen_files += 1
            upper = name.upper()
            if upper.endswith('.NMEA'):
                nmea.append(full)
            elif upper.endswith('.TS') and not FRAG_RE.search(name):
                ts_full.append(full)

    nmea.sort()
    ts_full.sort()
    return nmea, ts_full, seen_dirs, seen_files, skipped_dirs


# ==============================================================
# Утилиты
# ==============================================================
def md5_file(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest().upper()


def human_size(n):
    x = float(n)
    for unit in ('Б', 'КБ', 'МБ', 'ГБ', 'ТБ'):
        if x < 1024:
            return f"{x:.1f} {unit}"
        x /= 1024
    return f"{x:.2f} ТБ"


def _candidate_dirs(target_folder=None):
    dirs = []
    try:
        dirs.append(os.path.dirname(os.path.abspath(__file__)))
    except NameError:
        pass
    dirs.append(os.getcwd())
    if target_folder:
        tf = os.path.abspath(target_folder)
        dirs.append(tf)
        parent = os.path.dirname(tf)
        for _ in range(3):
            if parent and parent != os.path.dirname(parent):
                dirs.append(parent)
                parent = os.path.dirname(parent)
    seen = set()
    uniq = []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            uniq.append(d)
    return uniq


def _find_exec(name, target_folder=None):
    p = shutil.which(name)
    if p:
        return p
    for d in _candidate_dirs(target_folder):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


def find_mediainfo(target_folder=None):
    for cand in ('MediaInfo.exe', 'mediainfo.exe',
                 'MediaInfo', 'mediainfo'):
        p = _find_exec(cand, target_folder)
        if p:
            return p
    return None


def find_ffprobe(target_folder=None):
    for cand in ('ffprobe.exe', 'ffprobe'):
        p = _find_exec(cand, target_folder)
        if p:
            return p
    return None


# ==============================================================
# MediaInfo
# ==============================================================
def get_media_info(mediainfo_exe, path):
    if not mediainfo_exe:
        return None
    try:
        r = subprocess.run(
            [mediainfo_exe, '--Output=JSON', path],
            capture_output=True, text=True, timeout=120,
            encoding='utf-8', errors='replace')
        if r.returncode != 0:
            print(f"  [WARN] MediaInfo rc={r.returncode} для "
                  f"{os.path.basename(path)}: "
                  f"{(r.stderr or '').strip()[:200]}",
                  file=sys.stderr)
            return None
        data = json.loads(r.stdout)
        tracks = data.get('media', {}).get('track', [])
        out = {}
        for t in tracks:
            tt = (t.get('@type') or '').lower()
            if tt == 'general':
                out['container'] = t.get('Format', '')
            elif tt == 'video':
                out['video_codec'] = t.get('Format', '')
                out['video_codec_id'] = t.get('CodecID', '')
                out['width'] = t.get('Width', '')
                out['height'] = t.get('Height', '')
                out['fps'] = t.get('FrameRate', '')
            elif tt == 'audio':
                out['audio_codec'] = t.get('Format', '')
                out['audio_codec_id'] = t.get('CodecID', '')
                out['channels'] = t.get('Channels', '')
                out['sample_rate'] = t.get('SamplingRate', '')
        return out
    except Exception as e:
        print(f"  [WARN] MediaInfo исключение для "
              f"{os.path.basename(path)}: {e}", file=sys.stderr)
        return None


# ==============================================================
# ffprobe
# ==============================================================
def _parse_fps(s):
    if not s:
        return None
    s = str(s).strip().replace(',', '.')
    try:
        if '/' in s:
            a, b = s.split('/', 1)
            a, b = float(a), float(b)
            if b > 0 and a > 0:
                return a / b
            return None
        v = float(s)
        return v if v > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def _fps_valid(v):
    return _parse_fps(v) is not None


def get_ffprobe_info(ffprobe_exe, path):
    """fps, codec, width, height — быстрый вызов."""
    if not ffprobe_exe:
        return None
    try:
        cmd = [
            ffprobe_exe, '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries',
            'stream=avg_frame_rate,r_frame_rate,codec_name,width,height',
            '-of', 'default=noprint_wrappers=1',
            path,
        ]
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
            encoding='utf-8', errors='replace')
        if r.returncode != 0:
            return None
        data = {}
        for line in r.stdout.splitlines():
            if '=' in line:
                k, v = line.split('=', 1)
                data[k.strip()] = v.strip()
        out = {}
        for key in ('avg_frame_rate', 'r_frame_rate'):
            fps = _parse_fps(data.get(key, ''))
            if fps:
                out['fps'] = fps
                break
        codec = data.get('codec_name', '').strip()
        if codec:
            out['video_codec'] = codec
        try:
            if data.get('width'):
                out['width'] = int(data['width'])
            if data.get('height'):
                out['height'] = int(data['height'])
        except ValueError:
            pass
        return out if out else None
    except Exception as e:
        print(f"  [WARN] ffprobe исключение для "
              f"{os.path.basename(path)}: {e}", file=sys.stderr)
        return None


def get_ffprobe_frame_count(ffprobe_exe, path, timeout=900):
    """Считает количество видеокадров (-count_frames) и длительность.

    Возвращает dict {frames, duration, frames_source} или None.
    -count_frames декодирует видео полностью — медленно на 4K.
    """
    if not ffprobe_exe:
        return None
    # Длительность (быстро, из format)
    duration = None
    try:
        r = subprocess.run(
            [ffprobe_exe, '-v', 'error',
             '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1',
             path],
            capture_output=True, text=True, timeout=60,
            encoding='utf-8', errors='replace')
        if r.returncode == 0 and r.stdout.strip():
            try:
                duration = float(r.stdout.strip().splitlines()[0])
            except ValueError:
                pass
    except Exception:
        pass

    # Количество кадров
    frames = None
    frames_source = None
    try:
        r = subprocess.run(
            [ffprobe_exe, '-v', 'error',
             '-select_streams', 'v:0',
             '-count_frames',
             '-show_entries',
             'stream=nb_read_frames,nb_frames',
             '-of', 'default=noprint_wrappers=1',
             path],
            capture_output=True, text=True, timeout=timeout,
            encoding='utf-8', errors='replace')
        if r.returncode == 0:
            data = {}
            for line in r.stdout.splitlines():
                if '=' in line:
                    k, v = line.split('=', 1)
                    data[k.strip()] = v.strip()
            for key in ('nb_read_frames', 'nb_frames'):
                if data.get(key) and data[key].isdigit():
                    frames = int(data[key])
                    frames_source = key
                    break
    except subprocess.TimeoutExpired:
        print(f"  [WARN] ffprobe -count_frames timeout "
              f"(>{timeout}s) для {os.path.basename(path)}",
              file=sys.stderr)
    except Exception as e:
        print(f"  [WARN] ffprobe -count_frames для "
              f"{os.path.basename(path)}: {e}", file=sys.stderr)

    if frames is None and duration is None:
        return None
    return {
        'frames': frames,
        'duration': duration,
        'frames_source': frames_source,
    }


def merge_mi_ffprobe(mi, fp):
    result = {}
    srcs = {}
    if mi:
        for k, v in mi.items():
            if k == 'fps':
                if _fps_valid(v):
                    result['fps'] = _parse_fps(v)
                    srcs['fps'] = 'MediaInfo'
            else:
                if v:
                    result[k] = v
                    srcs[k] = 'MediaInfo'
    if fp:
        if 'fps' not in result and fp.get('fps'):
            result['fps'] = fp['fps']
            srcs['fps'] = 'ffprobe'
        if 'video_codec' not in result and fp.get('video_codec'):
            result['video_codec'] = fp['video_codec']
            srcs['video_codec'] = 'ffprobe'
        if 'width' not in result and fp.get('width'):
            result['width'] = fp['width']
            srcs['width'] = 'ffprobe'
        if 'height' not in result and fp.get('height'):
            result['height'] = fp['height']
            srcs['height'] = 'ffprobe'
    return result, srcs


def _fmt_fps(fps):
    try:
        return f"{float(fps):.3f}".replace('.', ',')
    except (ValueError, TypeError):
        return str(fps)


def _fmt_size_bytes(n):
    return f"{n:,}".replace(',', ' ') + f" байт ({human_size(n)})"


def _fmt_int(n):
    try:
        return f"{int(n):,}".replace(',', ' ')
    except (ValueError, TypeError):
        return str(n)


def _fmt_duration(sec):
    if sec is None:
        return '?'
    try:
        s = float(sec)
        m = int(s // 60)
        r = s - m * 60
        return f"{m} мин {r:.3f} с ({s:.3f} с)"
    except (ValueError, TypeError):
        return str(sec)


# ==============================================================
# main
# ==============================================================
def main():
    ap = argparse.ArgumentParser(
        description='Блок сведений о файлах-доказательствах')
    ap.add_argument('folder', nargs='?', default='.')
    ap.add_argument('--out', default='evidence_block.txt')
    ap.add_argument('--no-recursive', action='store_true')
    ap.add_argument('--include-dir', action='append', default=[])
    ap.add_argument('--no-count-frames', action='store_true',
                    help='Не считать кадры (быстро; без -count_frames)')
    args = ap.parse_args()

    folder_arg = (args.folder or '.').strip().strip('"\' ')
    folder = os.path.abspath(folder_arg)
    print(f"[INFO] folder: {folder}", file=sys.stderr)
    if not os.path.isdir(folder):
        print(f"[FAIL] папка не найдена: {folder}", file=sys.stderr)
        return 1

    skip = set(DEFAULT_SKIP)
    for inc in args.include_dir:
        skip.discard(inc.lower())

    print(f"[INFO] recursive: {not args.no_recursive}",
          file=sys.stderr)
    print(f"[INFO] skip dirs: {sorted(skip)}", file=sys.stderr)
    print(f"[INFO] count frames: {not args.no_count_frames}",
          file=sys.stderr)

    nmea, ts_full, n_dirs, n_files, skipped = find_files(
        folder,
        recursive=not args.no_recursive,
        skip_dirs=skip)

    print(f"[INFO] просмотрено папок: {n_dirs}", file=sys.stderr)
    print(f"[INFO] просмотрено файлов: {n_files}", file=sys.stderr)
    if skipped:
        print(f"[INFO] пропущено папок ({len(skipped)}):",
              file=sys.stderr)
        for d in skipped[:10]:
            print(f"       - {d}", file=sys.stderr)
        if len(skipped) > 10:
            print(f"       ... и ещё {len(skipped) - 10}",
                  file=sys.stderr)

    print(f"[INFO] найдено .nmea: {len(nmea)}", file=sys.stderr)
    print(f"[INFO] найдено крупных .ts: {len(ts_full)}",
          file=sys.stderr)

    mediainfo = find_mediainfo(target_folder=folder)
    ffprobe = find_ffprobe(target_folder=folder)
    print(f"[INFO] MediaInfo: {mediainfo or 'НЕ НАЙДЕН'}",
          file=sys.stderr)
    print(f"[INFO] ffprobe:   {ffprobe or 'НЕ НАЙДЕН'}",
          file=sys.stderr)

    lines = []
    lines.append("СВЕДЕНИЯ О КЛЮЧЕВЫХ ФАЙЛАХ-ДОКАЗАТЕЛЬСТВАХ")
    lines.append("")
    lines.append(f"Папка: {folder}")
    lines.append(
        f"Дата формирования справки: "
        f"{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    lines.append("")

    if not nmea and not ts_full:
        lines.append(
            "Файлов .nmea или .ts (исходных, не фрагментов) "
            "в указанной папке не найдено.")
        lines.append("")
        lines.append("Диагностика:")
        lines.append(f"  Просмотрено папок:  {n_dirs}")
        lines.append(f"  Просмотрено файлов: {n_files}")
        if skipped:
            lines.append(
                f"  Пропущено папок ({len(skipped)}): "
                f"{', '.join(os.path.basename(d) for d in skipped[:10])}"
                + (" ..." if len(skipped) > 10 else ""))
        lines.append(f"  MediaInfo: {mediainfo or 'не найден'}")
        lines.append(f"  ffprobe:   {ffprobe or 'не найден'}")
    else:
        idx = 0
        if nmea:
            lines.append("─── Файлы NMEA ───")
            lines.append("")
            for p in nmea:
                idx += 1
                name = os.path.basename(p)
                size = os.path.getsize(p)
                mtime = datetime.fromtimestamp(
                    os.path.getmtime(p)).strftime(
                    '%Y-%m-%d %H:%M:%S')
                print(f"  [{idx}] md5 {name} ...", file=sys.stderr)
                md5 = md5_file(p)
                lines.append(f"{idx}. {name}")
                lines.append(
                    f"   Размер:          {_fmt_size_bytes(size)}")
                lines.append(
                    f"   Время записи:    {mtime}")
                lines.append(
                    f"   MD5:             {md5}")
                lines.append("")

        if ts_full:
            lines.append(
                "─── Видеофайлы .TS (исходные, не фрагменты) ───")
            lines.append("")
            for p in ts_full:
                idx += 1
                name = os.path.basename(p)
                size = os.path.getsize(p)
                mtime = datetime.fromtimestamp(
                    os.path.getmtime(p)).strftime(
                    '%Y-%m-%d %H:%M:%S')
                print(f"  [{idx}] md5 {name} ...", file=sys.stderr)
                md5 = md5_file(p)
                mi = get_media_info(mediainfo, p)
                fp = get_ffprobe_info(ffprobe, p)
                merged, srcs = merge_mi_ffprobe(mi, fp)

                # Кадры и длительность
                fcnt = None
                if not args.no_count_frames:
                    print(f"      count_frames {name} ...",
                          file=sys.stderr)
                    fcnt = get_ffprobe_frame_count(ffprobe, p)

                lines.append(f"{idx}. {name}")
                lines.append(
                    f"   Размер:          {_fmt_size_bytes(size)}")
                lines.append(
                    f"   Время записи:    {mtime}")
                lines.append(
                    f"   MD5:             {md5}")
                if merged.get('container'):
                    lines.append(
                        f"   Контейнер:       "
                        f"{merged['container']}")
                if merged.get('video_codec'):
                    vid = merged['video_codec']
                    if merged.get('video_codec_id'):
                        vid += (f" (Codec ID: "
                                f"{merged['video_codec_id']})")
                    if merged.get('width') and merged.get('height'):
                        vid += (f", {merged['width']}×"
                                f"{merged['height']}")
                    if merged.get('fps'):
                        _fsrc = srcs.get('fps', '?')
                        vid += (f", {_fmt_fps(merged['fps'])} fps "
                                f"[{_fsrc}]")
                    lines.append(f"   Видеокодек:      {vid}")
                if fcnt:
                    if fcnt.get('frames') is not None:
                        _src_key = fcnt.get('frames_source') or '?'
                        lines.append(
                            f"   Кадров (видео):  "
                            f"{_fmt_int(fcnt['frames'])} "
                            f"[ffprobe:{_src_key}]")
                    if fcnt.get('duration') is not None:
                        lines.append(
                            f"   Длительность:    "
                            f"{_fmt_duration(fcnt['duration'])}")
                elif args.no_count_frames:
                    lines.append(
                        f"   Кадров (видео):  (не считалось, "
                        f"--no-count-frames)")
                if merged.get('audio_codec'):
                    aud = merged['audio_codec']
                    if merged.get('audio_codec_id'):
                        aud += (f" (Codec ID: "
                                f"{merged['audio_codec_id']})")
                    if merged.get('channels'):
                        aud += f", {merged['channels']} кан."
                    if merged.get('sample_rate'):
                        try:
                            sr = int(merged['sample_rate'])
                            aud += f", {_fmt_int(sr)} Гц"
                        except ValueError:
                            aud += (f", {merged['sample_rate']} "
                                    f"Гц")
                    lines.append(f"   Аудиокодек:      {aud}")
                if not merged:
                    lines.append(
                        "   Параметры:       недоступны "
                        "(MediaInfo и ffprobe не дали данных)")
                lines.append("")

    text = '\n'.join(lines)
    print()
    print(text)
    print()
    try:
        with open(args.out, 'w', encoding='utf-8',
                  newline='\n') as f:
            f.write(text + '\n')
        print(f"[OK] сохранено: {os.path.abspath(args.out)}",
              file=sys.stderr)
        return 0
    except OSError as e:
        print(f"[FAIL] запись {args.out}: {e}",
              file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
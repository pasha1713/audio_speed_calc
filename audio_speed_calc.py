#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audio_speed_calc.py v0.53
Расчёт скорости по звуку из .ts-фрагментов.

Изменения 0.53:
  - NEW: кнопка «Подп.V» — подписи пиков скорости на всех включённых
    источниках (GPS-H5, GPS-H4, реальные треки H5/H4). Каждый источник
    своим цветом.
  - NEW: GPS→H5 / GPS→H4 калибруются через V_ref выбранных H5/H4.
  - NEW: цвета кривых GPS-скорости в палитре inferno.
  - NEW: gps_lag_override читается из конфига в __init__.
  - v0.53.1: Vmax/Vmin всегда в видимом окне (даже без локальных пиков).
  - v0.53.1: в статусе V→Vср, добавлен Vgps ср, убран Rk.
  - v0.53.1: график придвинут к среднему столбцу кнопок.
  - v0.53.2: при переходах по истории (Назад/Вперёд/Удалить поз.)
    результаты пересчитываются для нового диапазона. Защита от
    пустого analyze_dur и от исключений при пересчёте.
"""

import os
import sys
import json
import subprocess
import argparse
import math
import struct
import datetime
import shutil
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.widgets import Slider, Button, SpanSelector, TextBox

from sys_fingerprint import (
    md5_file, file_mtime, get_computer_info, get_disk_info, get_script_info
)

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

VERSION = "0.53"
CONFIG_FILE = "audio_speed_config.json"
HISTORY_LOG_FILE = "history_log.txt"


CHANGELOG = {
    "0.53": [
        "",
        "v0.53",
        "-" * 64,
        "",
        "NEW",
        "",
        "* Кнопка «Подп.V» — залипающий тумблер в среднем столбце.",
        "  Включает подписи пиков скорости на всех активных источниках.",
        "  Повторное нажатие выключает.",
        "",
        "* Цвета подписей:",
        "    маджента — GPS-H5",
        "    голубой  — GPS-H4",
        "    жёлтый   — реальный трек H5",
        "    зелёный  — реальный трек H4",
        "",
        "* Кривые GPS→H5 / GPS→H4 калибруются через V_ref",
        "  выбранных H5/H4 (self.h5 / self.h4).",
        "",
        "* Цвета кривых:",
        "    GPS-H5 — маджента #ff00cc",
        "    GPS-H4 — голубой  #00ccff",
        "  Плюс чёрная обводка через path_effects.",
        "",
        "* gps_lag_override читается из конфига в __init__.",
        "",
        "ДОПОЛНЕНИЯ 0.53.1",
        "",
        "* Vmax и Vmin в видимом диапазоне времени показываются",
        "  всегда — маркеры с подписями, даже если локальных пиков",
        "  в окне нет.",
        "* В статусной строке V переименован в Vср;",
        "  добавлено Vgps ср; Rk убран.",
        "* Графическая область придвинута к среднему столбцу кнопок.",
        "",
        "ИСПРАВЛЕНО 0.53.2",
        "",
        "* При переходах по истории (Назад/Вперёд/Удалить поз.)",
        "  Vср / Vgps ср / Δgps пересчитываются для нового",
        "  восстановленного диапазона. Пустой analyze_dur (0)",
        "  больше не приводит к отсутствию расчёта — берётся",
        "  весь доступный диапазон кадров.",
        "* _draw_speed_overlay больше не падает, если V_ref <= 1.",
        "",
    ],
    "0.52": [
        "",
        "v0.52",
        "-" * 64,
        "",
        "NEW",
        "",
        "* Журнал изменений формируется скриптом автоматически.",
        "* Средний столбец кнопок GPS→H5 / GPS→H4.",
        "* per-entry хранение настроек слайдеров в view_history.",
        "* analyze_start/analyze_dur пишутся в JSON.",
        "",
        "ИСПРАВЛЕНО",
        "",
        "* _sync_fields пишет tb_step как «%.3f».",
        "* _btn_delete_pos удаляет ТЕКУЩУЮ позицию.",
        "* _push_history ищет дубликаты по всей истории.",
        "",
    ],
    "0.51": [
        "",
        "v0.51",
        "-" * 64,
        "",
        "КРИТИЧЕСКИЕ ИСПРАВЛЕНИЯ",
        "",
        "* _sync_fields и все последующие методы возвращены в класс.",
        "* Единый формат истории зума (dict) + zoom_pointer.",
        "* ALGO_NAMES вместо self.ALGO_NAMES.",
        "* Инициализация v_min/dv_min/... перед if all_v.",
        "* save_config снова вызывается при закрытии, по S и P.",
        "* _on_span корректно ставит analyze_dur.",
        "* _reset_zoom/_btn_reset_* сбрасывают zoom_pointer.",
        "* GPS: корректный UTC вместо time.mktime.",
        "* GPS exiftool: единицы через GPSSpeedRef.",
        "* ?/Z ловятся и как shift+/ и shift+z.",
        "",
    ],
}


def update_history_log(log_dir=None):
    """Дописывает запись о текущей VERSION в history_log.txt.

    Если файла нет — создаёт с заголовком.
    Если запись для текущей VERSION уже есть — ничего не делает.
    Ошибки записи не валят программу, только печатают в stderr.
    """
    if log_dir is None:
        log_dir = os.path.dirname(os.path.abspath(__file__)) or '.'
    path = os.path.join(log_dir, HISTORY_LOG_FILE)

    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            marker = "v" + VERSION
            for line in content.splitlines():
                if line.strip() == marker:
                    return
        except Exception:
            pass
    else:
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write("=" * 64 + "\n")
                f.write("audio_speed_calc.py — журнал изменений\n")
                f.write("=" * 64 + "\n")
        except Exception as e:
            print(f"Не удалось создать {path}: {e}", file=sys.stderr)
            return

    lines = CHANGELOG.get(VERSION)
    if not lines:
        return

    try:
        with open(path, 'a', encoding='utf-8') as f:
            for line in lines:
                f.write(line + "\n")
        print(f"Журнал обновлён: {path} (v{VERSION})", file=sys.stderr)
    except Exception as e:
        print(f"Не удалось обновить {path}: {e}", file=sys.stderr)


DEFAULT_CONFIG = {
    "freq_min": 20,
    "freq_max": 600,
    "harmonic_5_freq": None,
    "harmonic_4_freq": None,
    "harmonic_line_width": 3,
    "color_steps": 10,
    "time_step": 0.1,
    "report_duration": 60,
    "sample_rate": 44100,
    "fft_size": 32768,
    "target_fps": 30,
    "last_png_dir": None,
    "view_x_start": None,
    "view_x_end": None,
    "view_y_start": None,
    "view_y_end": None,
    "peak_search_hz": 5.0,
    "algorithm": 2,
    "cylinders": 4,
    "stroke": 2,
    "tire_width": 235,
    "tire_profile": 70,
    "rim_diameter": 16,
    "final_drive": 4.875,
    "gear_ratio": 0.802,
    "win_x": None,
    "win_y": None,
    "win_w": None,
    "win_h": None,
    "spec_mode": "time",
    "view_history": [],
    "overlay_mode": 0,
    "gps_lag_override": None,
    "analyze_start": 0,
    "analyze_dur": 0,
}


def add_png_footer(fig, y_pos=0.94):
    """Штамп в левый верхний угол."""
    try:
        script_path = os.path.abspath(__file__)
        script_mtime = datetime.datetime.fromtimestamp(os.path.getmtime(script_path))
        script_md5 = md5_file(script_path)[:8]
    except Exception:
        script_path = 'audio_speed_calc.py'
        script_mtime = datetime.datetime.now()
        script_md5 = '--------'
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    script_str = script_mtime.strftime('%Y-%m-%d %H:%M:%S')
    footer = '%s v%s | MD5: %s | Скрипт от: %s | PNG: %s' % (
        os.path.basename(script_path), VERSION, script_md5, script_str, now_str)
    txt = fig.text(0.02, y_pos, footer, ha='left', va='top', fontsize=6,
                   color='gray', family='monospace',
                   bbox=dict(boxstyle='round,pad=0.3', facecolor='white',
                             alpha=0.8, edgecolor='gray', linewidth=0.5))
    return txt


def load_config(path):
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            merged = dict(DEFAULT_CONFIG)
            merged.update(cfg)
            return merged
        except Exception as e:
            print(f"Предупреждение: не удалось загрузить конфиг ({e}), defaults",
                  file=sys.stderr)
    return dict(DEFAULT_CONFIG)


def save_config(cfg, path):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        print(f"Настройки сохранены: {path}", file=sys.stderr)
    except Exception as e:
        print(f"Ошибка сохранения конфига: {e}", file=sys.stderr)

def _find_exec(name):
    """Поиск ffmpeg/ffprobe/exiftool: рядом со скриптом, в bin/, в PATH."""
    suffix = '.exe' if sys.platform == 'win32' else ''
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(script_dir, name + suffix),
        os.path.join(script_dir, 'bin', name + suffix),
    ]
    path_exe = shutil.which(name)
    if path_exe:
        candidates.append(path_exe)
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


_FFMPEG_PATH = None
_FFPROBE_PATH = None


def _init_ffmpeg():
    global _FFMPEG_PATH, _FFPROBE_PATH
    _FFMPEG_PATH = _find_exec('ffmpeg')
    _FFPROBE_PATH = _find_exec('ffprobe')


def _epoch_from_utc(dt_str):
    """'2026-06-02T05:07:35Z' -> epoch (UTC), независимо от TZ машины."""
    s = dt_str.rstrip('Z')
    dt_obj = datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
    return int(dt_obj.replace(tzinfo=datetime.timezone.utc).timestamp())


def fix_time(dt):
    return "%04d-%02d-%02dT%02d:%02d:%02dZ" % (
        dt['Year'] + 2000, dt['Month'], dt['Day'],
        dt['Hour'], dt['Minute'], dt['Second'])


def fix_coordinates(hemi, raw):
    degrees = int(raw / 100)
    minutes = raw - (degrees * 100)
    result = degrees + minutes / 60.0
    if hemi in ('S', 'W'):
        result = -result
    return result


def fix_speed(speed):
    return speed * 1.852  # узлы -> км/ч


def deobfuscate_coord(latitude, longitude):
    latitude = (latitude - 187.98217) / 3.0
    longitude = (longitude - 2199.19876) * 0.5
    return latitude, longitude


def get_gps_offset(data):
    """Поиск маркера A + (N|S) + (E|W); offset на 24 байта назад."""
    pointer = len(data) - 3
    while pointer >= 24:
        try:
            active, lat_hemi, lon_hemi = struct.unpack_from('<sss', data, pointer)
            if (active == b'A'
                    and lat_hemi in (b'N', b'S')
                    and lon_hemi in (b'E', b'W')):
                return pointer - 24
        except struct.error:
            pass
        pointer -= 1
    return -1


def get_gps_data(data, deobfuscate=False):
    """Возвращает dict с GPS-точкой или None."""
    gps = {
        'Epoch': None,
        'DT': {'Year': None, 'Month': None, 'Day': None,
               'Hour': None, 'Minute': None, 'Second': None, 'DT': None},
        'Loc': {'Lat': {'Raw': None, 'Hemi': None, 'Float': None},
                'Lon': {'Raw': None, 'Hemi': None, 'Float': None},
                'Speed': None, 'Bearing': None},
    }
    if not data:
        return None
    if data[0:1] in (b'\x05', b'\xF0'):
        return None

    offset = get_gps_offset(data)
    if offset < 0:
        return None
    try:
        gps['DT']['Hour'], gps['DT']['Minute'], gps['DT']['Second'] = \
            struct.unpack_from('<III', data, offset)
        offset += 12
        gps['DT']['Year'], gps['DT']['Month'], gps['DT']['Day'] = \
            struct.unpack_from('<III', data, offset)
        offset += 12
        active, lat_hemi, lon_hemi = struct.unpack_from('<sss', data, offset)
        offset += 4
        gps['Loc']['Lat']['Raw'], gps['Loc']['Lon']['Raw'] = \
            struct.unpack_from('<ff', data, offset)
        offset += 8
        gps['Loc']['Speed'], gps['Loc']['Bearing'] = \
            struct.unpack_from('<ff', data, offset)
        gps['Loc']['Lat']['Hemi'] = lat_hemi.decode('ascii')
        gps['Loc']['Lon']['Hemi'] = lon_hemi.decode('ascii')
    except (struct.error, UnicodeDecodeError):
        return None

    if deobfuscate:
        gps['Loc']['Lat']['Raw'], gps['Loc']['Lon']['Raw'] = deobfuscate_coord(
            gps['Loc']['Lat']['Raw'], gps['Loc']['Lon']['Raw'])

    gps['Loc']['Lat']['Float'] = fix_coordinates(
        gps['Loc']['Lat']['Hemi'], gps['Loc']['Lat']['Raw'])
    gps['Loc']['Lon']['Float'] = fix_coordinates(
        gps['Loc']['Lon']['Hemi'], gps['Loc']['Lon']['Raw'])
    gps['Loc']['Speed'] = fix_speed(gps['Loc']['Speed'])
    gps['DT']['DT'] = fix_time(gps['DT'])

    try:
        gps['Epoch'] = _epoch_from_utc(gps['DT']['DT'])
    except ValueError:
        return None
    return gps


def parse_ts_gps(filepath, deobfuscate=False):
    """Прямой бинарный разбор TS (PID 0x0300).
    Возвращает [(epoch, speed_kmh, lat, lon, bearing), ...]."""
    results = []
    TS_PKT = 188
    GPS_PID = 0x0300
    PES_HDR = b'\x00\x00\x01\xBF'
    try:
        with open(filepath, 'rb') as f:
            data = f.read()
    except Exception:
        return results

    pos = 0
    partial = b''
    while pos + TS_PKT <= len(data):
        if data[pos] != 0x47:
            pos += 1
            continue
        pkt = data[pos:pos + TS_PKT]
        pos += TS_PKT
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        if pid != GPS_PID:
            continue
        payload_start = 4
        if pkt[3] & 0x20:
            af_len = pkt[4]
            payload_start = 5 + af_len
        if payload_start >= TS_PKT:
            continue
        payload = pkt[payload_start:]
        if not payload:
            continue

        pes_pos = payload.find(PES_HDR)
        if pes_pos >= 0:
            pes_data = payload[pes_pos + 4:]
            if partial:
                pes_data = partial + pes_data
                partial = b''
            gps = get_gps_data(pes_data, deobfuscate)
            if gps and gps['Loc']['Speed'] is not None:
                results.append((
                    gps['Epoch'],
                    gps['Loc']['Speed'],
                    gps['Loc']['Lat']['Float'] if gps['Loc']['Lat']['Float'] is not None else 0.0,
                    gps['Loc']['Lon']['Float'] if gps['Loc']['Lon']['Float'] is not None else 0.0,
                    gps['Loc']['Bearing'] if gps['Loc']['Bearing'] is not None else 0.0,
                ))
        else:
            if partial:
                combined = partial + payload
                gps = get_gps_data(combined, deobfuscate)
                if gps and gps['Loc']['Speed'] is not None:
                    results.append((
                        gps['Epoch'],
                        gps['Loc']['Speed'],
                        gps['Loc']['Lat']['Float'] if gps['Loc']['Lat']['Float'] is not None else 0.0,
                        gps['Loc']['Lon']['Float'] if gps['Loc']['Lon']['Float'] is not None else 0.0,
                        gps['Loc']['Bearing'] if gps['Loc']['Bearing'] is not None else 0.0,
                    ))
                    partial = b''
                else:
                    partial = combined[-14:]
            else:
                partial = payload[-14:] if len(payload) >= 14 else payload
    return results

_EXIFTOOL_PATH = None


def _init_exiftool():
    global _EXIFTOOL_PATH
    _EXIFTOOL_PATH = _find_exec('exiftool')


def _to_float(s):
    s = (s or '').strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


_SPEED_TO_KMH = {'K': 1.0, 'M': 1.609344, 'N': 1.852}


def parse_gps_exiftool(filepath):
    """Извлечение GPS через exiftool -ee3 -n с учётом GPSSpeedRef."""
    if _EXIFTOOL_PATH is None:
        return None
    fmt = ('$GPSDateTime\t$GPSSpeed\t$GPSSpeedRef\t'
           '$GPSLatitude\t$GPSLongitude\t$GPSTrack\n')
    cmd = [_EXIFTOOL_PATH, '-ee3', '-n', '-p', fmt, filepath]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                           encoding='utf-8', errors='replace')
        if r.returncode != 0:
            return None
    except Exception:
        return None

    results = []
    for line in r.stdout.strip().splitlines():
        parts = line.strip().split('\t')
        if len(parts) < 6:
            continue
        dt_str = parts[0].strip()
        speed_raw = _to_float(parts[1])
        speed_ref = (parts[2].strip() or 'K').upper()
        lat = _to_float(parts[3])
        lon = _to_float(parts[4])
        bearing = _to_float(parts[5])
        if lat is None or lon is None:
            continue

        speed_kmh = 0.0
        if speed_raw is not None:
            speed_kmh = speed_raw * _SPEED_TO_KMH.get(speed_ref, 1.0)

        dt_norm = dt_str.replace(':', '-', 2).replace(' ', 'T')
        if dt_norm.endswith('Z'):
            dt_norm = dt_norm[:-1]
        try:
            dt_obj = datetime.datetime.strptime(dt_norm, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
        epoch = int(dt_obj.replace(tzinfo=datetime.timezone.utc).timestamp())

        results.append((epoch, speed_kmh, lat, lon,
                        bearing if bearing is not None else 0.0))
    return results


def extract_gps(filepath):
    """exiftool -> прямой разбор -> пусто."""
    _init_exiftool()
    if _EXIFTOOL_PATH:
        pts = parse_gps_exiftool(filepath)
        if pts:
            return pts, 'exiftool'
    pts = parse_ts_gps(filepath)
    if pts:
        return pts, 'binary'
    return [], 'none'


def gps_to_relative_time(gps_points, first_epoch=None):
    """[(epoch, speed, lat, lon, bearing), ...]
    -> [(t_rel, speed, lat, lon, bearing), ...]."""
    if not gps_points:
        return []
    if first_epoch is None:
        first_epoch = gps_points[0][0]
    return [(p[0] - first_epoch, p[1], p[2], p[3], p[4]) for p in gps_points]


def find_gps_lag(audio_times, audio_speeds, gps_times, gps_speeds,
                 lag_range=(-5.0, 5.0), lag_step=0.1):
    """Кросс-корреляция: ищет сдвиг GPS относительно аудио."""
    if len(gps_times) < 3 or len(audio_times) < 3:
        return 0.0, 0.0, []

    gps_t = np.asarray(gps_times, dtype=float)
    gps_v = np.asarray(gps_speeds, dtype=float)
    aud_t = np.asarray(audio_times, dtype=float)
    aud_v = np.asarray(audio_speeds, dtype=float)

    ok_a = ~(np.isnan(aud_v) | np.isinf(aud_v))
    aud_t, aud_v = aud_t[ok_a], aud_v[ok_a]
    ok_g = ~(np.isnan(gps_v) | np.isinf(gps_v))
    gps_t, gps_v = gps_t[ok_g], gps_v[ok_g]

    if len(gps_t) < 3 or len(aud_t) < 3:
        return 0.0, 0.0, []

    order_g = np.argsort(gps_t)
    gps_t, gps_v = gps_t[order_g], gps_v[order_g]
    order_a = np.argsort(aud_t)
    aud_t, aud_v = aud_t[order_a], aud_v[order_a]

    best_lag = 0.0
    best_corr_abs = 0.0
    best_corr_signed = 0.0
    results = []

    for lag in np.arange(lag_range[0], lag_range[1] + lag_step, lag_step):
        shifted = gps_t + lag
        mask = (aud_t >= shifted[0]) & (aud_t <= shifted[-1])
        if mask.sum() < 5:
            continue
        g = np.interp(aud_t[mask], shifted, gps_v)
        a = aud_v[mask]
        if a.std() < 1e-3 or g.std() < 1e-3:
            continue
        c = float(np.corrcoef(a, g)[0, 1])
        if np.isnan(c):
            continue
        results.append((float(lag), float(c), int(mask.sum())))
        if abs(c) > best_corr_abs:
            best_corr_abs = abs(c)
            best_corr_signed = c
            best_lag = float(lag)

    results.sort(key=lambda x: abs(x[1]), reverse=True)
    return best_lag, best_corr_signed, results


def find_gps_window_points(gps_points, t_start, t_end):
    """GPS-точки в окне и ближайшие до/после."""
    if not gps_points:
        return [], None, None
    in_win, before, after = [], None, None
    for gt, gv in ((p[0], p[1]) for p in gps_points):
        if t_start <= gt <= t_end:
            in_win.append((gt, gv, 0.0))
        elif gt < t_start:
            if before is None or gt > before[0]:
                before = (gt, gv, t_start - gt)
        elif gt > t_end:
            if after is None or gt < after[0]:
                after = (gt, gv, gt - t_end)
    return in_win, before, after


def _find_peaks_simple(signal, min_distance=5):
    """Простые локальные максимумы в 1D-массиве."""
    n = len(signal)
    if n < 3:
        return []
    peaks = []
    for i in range(1, n - 1):
        if signal[i] > signal[i - 1] and signal[i] > signal[i + 1]:
            peaks.append(i)
    if not peaks:
        return []
    filtered = [peaks[0]]
    for p in peaks[1:]:
        if p - filtered[-1] >= min_distance:
            filtered.append(p)
        else:
            if signal[p] > signal[filtered[-1]]:
                filtered[-1] = p
    return filtered


def _find_extrema_simple(signal, min_distance=5, invert=False):
    """Локальные экстремумы: максимумы (invert=False) или минимумы
    (invert=True). Логика та же, что у _find_peaks_simple."""
    if invert:
        return _find_peaks_simple(-signal, min_distance=min_distance)
    return _find_peaks_simple(signal, min_distance=min_distance)

def get_ts_duration(filepath):
    if _FFPROBE_PATH is None:
        return None, None
    for stream_sel, label in [('v:0', 'video stream'), ('a:0', 'audio stream')]:
        cmd = [_FFPROBE_PATH, '-v', 'error',
               '-select_streams', stream_sel,
               '-show_entries', 'stream=duration',
               '-of', 'default=noprint_wrappers=1:nokey=1',
               filepath]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                               encoding='utf-8', errors='replace')
            if r.returncode == 0 and r.stdout.strip():
                for line in r.stdout.strip().splitlines():
                    line = line.strip()
                    if line:
                        try:
                            val = float(line)
                            if val > 0:
                                return val, f'ffprobe ({label})'
                        except ValueError:
                            continue
        except Exception:
            pass
    for entries in ['format=duration', 'stream=duration']:
        cmd = [_FFPROBE_PATH, '-v', 'error',
               '-show_entries', entries,
               '-of', 'default=noprint_wrappers=1:nokey=1',
               filepath]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                               encoding='utf-8', errors='replace')
            if r.returncode == 0 and r.stdout.strip():
                for line in r.stdout.strip().splitlines():
                    line = line.strip()
                    if line:
                        try:
                            val = float(line)
                            if val > 0:
                                return val, f'ffprobe ({entries})'
                        except ValueError:
                            continue
        except Exception:
            pass
    return None, None


def extract_audio(filepath, start_sec, duration_sec, sample_rate=44100):
    if _FFMPEG_PATH is None:
        print("ffmpeg не найден. Положите ffmpeg.exe рядом со скриптом или в bin/.")
        return None, sample_rate
    cmd = [
        _FFMPEG_PATH, '-nostdin',
        '-ss', f'{start_sec:.3f}',
        '-t', f'{duration_sec:.3f}',
        '-i', filepath,
        '-vn', '-ac', '1', '-ar', str(sample_rate),
        '-f', 'f32le', 'pipe:1'
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=600)
        if r.returncode != 0:
            stderr = r.stderr.decode('utf-8', errors='replace')[:300]
            print(f"ffmpeg: {stderr}")
            return None, sample_rate
        audio = np.frombuffer(r.stdout, dtype=np.float32).copy()
        if len(audio) == 0:
            print("ffmpeg: пустой аудиопоток")
            return None, sample_rate
        return audio, sample_rate
    except FileNotFoundError:
        print("ffmpeg не найден. Установите ffmpeg.")
        return None, sample_rate
    except subprocess.TimeoutExpired:
        print("Таймаут ffmpeg")
        return None, sample_rate


_window_cache = {}


def _get_window(fft_size):
    if fft_size not in _window_cache:
        _window_cache[fft_size] = np.hanning(fft_size)
    return _window_cache[fft_size]


def compute_stft(audio, sr, fft_size=32768, target_fps=30, window=None):
    hop = max(1, int(sr / target_fps))
    if window is None:
        window = _get_window(fft_size)
    n_frames = len(audio) // hop
    freqs = np.fft.rfftfreq(fft_size, 1.0 / sr)
    times = np.arange(n_frames) * hop / sr

    if n_frames == 0:
        S_db = np.zeros((len(freqs), 0), dtype=np.float32)
        return freqs, times, S_db

    pad_len = (n_frames - 1) * hop + fft_size
    if pad_len > len(audio):
        audio = np.pad(audio, (0, pad_len - len(audio)))

    frames = np.lib.stride_tricks.sliding_window_view(audio, fft_size)[::hop][:n_frames]
    windowed = frames * window[np.newaxis, :]
    S = np.abs(np.fft.rfft(windowed, axis=1)).T
    S_db = 20 * np.log10(S + 1e-10)
    return freqs, times, S_db.astype(np.float32)


def interp_crossing(freqs, spectrum, idx_above, idx_below, db_threshold):
    db_a = float(spectrum[idx_above])
    db_b = float(spectrum[idx_below])
    if abs(db_b - db_a) < 1e-10:
        return float(freqs[idx_above])
    frac = (db_threshold - db_a) / (db_b - db_a)
    return float(freqs[idx_above] + frac * (freqs[idx_below] - freqs[idx_above]))


def find_peak_near(freqs, spectrum_col, expected_freq, search_halfwidth_hz,
                   f_lo=None, f_hi=None):
    expected_idx = int(np.argmin(np.abs(freqs - expected_freq)))
    lo_freq = expected_freq - search_halfwidth_hz
    hi_freq = expected_freq + search_halfwidth_hz
    if f_lo is not None:
        lo_freq = max(lo_freq, f_lo)
    if f_hi is not None:
        hi_freq = min(hi_freq, f_hi)
    lo_idx = int(np.argmin(np.abs(freqs - lo_freq)))
    hi_idx = int(np.argmin(np.abs(freqs - hi_freq)))
    if lo_idx == hi_idx:
        return float(freqs[expected_idx])
    segment = spectrum_col[lo_idx:hi_idx + 1]
    peak_offset = int(np.argmax(segment))
    peak_idx = lo_idx + peak_offset
    if 0 < peak_idx < len(freqs) - 1:
        y_left = float(spectrum_col[peak_idx - 1])
        y_peak = float(spectrum_col[peak_idx])
        y_right = float(spectrum_col[peak_idx + 1])
        denom = y_left - 2.0 * y_peak + y_right
        if abs(denom) > 1e-10:
            delta = 0.5 * (y_left - y_right) / denom
            delta = max(-0.5, min(0.5, delta))
            df = float(freqs[peak_idx + 1] - freqs[peak_idx])
            return float(freqs[peak_idx]) + delta * df
    return float(freqs[peak_idx])


def _search_threshold(freqs, spectrum_col, target_freq, db_threshold,
                      f_lo=None, f_hi=None):
    target_idx = int(np.argmin(np.abs(freqs - target_freq)))
    if f_lo is None:
        f_lo = freqs[0]
    if f_hi is None:
        f_hi = freqs[-1]
    lo_idx = int(np.argmin(np.abs(freqs - f_lo)))
    hi_idx = int(np.argmin(np.abs(freqs - f_hi)))

    last_above = target_idx
    for i in range(target_idx, hi_idx + 1):
        if spectrum_col[i] >= db_threshold:
            last_above = i
        else:
            break
    if last_above + 1 <= hi_idx and last_above + 1 < len(freqs):
        h_max = interp_crossing(freqs, spectrum_col, last_above, last_above + 1, db_threshold)
    else:
        h_max = float(freqs[last_above])

    last_below = target_idx
    for i in range(target_idx, lo_idx - 1, -1):
        if spectrum_col[i] >= db_threshold:
            last_below = i
        else:
            break
    if last_below - 1 >= lo_idx and last_below - 1 >= 0:
        h_min = interp_crossing(freqs, spectrum_col, last_below, last_below - 1, db_threshold)
    else:
        h_min = float(freqs[last_below])

    if h_min > h_max:
        h_min, h_max = h_max, h_min
    return h_min, h_max

ALGO_NAMES = {
    1: "Абсолютный порог dB",
    2: "Индекс палитры",
    3: "Относительный от пика",
    4: "Адаптивный от шума",
    5: "Половинная мощность (-3 dB)",
    6: "RMS-интегрирование",
}

ALGO_TITLES = {
    1: "Алгоритм 1 (dB) — Абсолютный порог",
    2: "Алгоритм 2 (Пал) — Индекс палитры",
    3: "Алгоритм 3 (Отн) — Относительный от пика",
    4: "Алгоритм 4 (Шум) — Адаптивный от шума",
    5: "Алгоритм 5 (-3dB) — Половинная мощность",
    6: "Алгоритм 6 (RMS) — RMS-интегрирование",
}

ALGO_HELPS = {
    1: [
        "Цвет,инд (1-255):",
        "  Абсолютный индекс палитры.",
        "  Порог = vmin + (цвет/255)*(vmax-vmin).",
        "  Больше значение -> выше порог ->",
        "  границы СУЖАЮТСЯ.",
        "  Влияет: vmin, vmax (от палитры).",
        "",
        "Пик,Гц (1-30):",
        "  Полуширина поиска центра гармоники.",
        "  Меньше -> жёстче трекинг,",
        "  но риск потерять сместившуюся гармонику.",
    ],
    2: [
        "Цвет,инд (1-255):",
        "  Сдвиг индекса палитры от центра вниз.",
        "  Порог = индекс_центра - цвет.",
        "  Больше значение -> ниже порог ->",
        "  границы РАСШИРЯЮТСЯ.",
        "  Влияет: vmin, vmax, dB центра.",
        "",
        "Пик,Гц (1-30):",
        "  Полуширина поиска центра гармоники.",
        "  Меньше -> жёстче трекинг,",
        "  но риск потерять сместившуюся гармонику.",
    ],
    3: [
        "Цвет,инд (1-255):",
        "  Децибелы ниже пика.",
        "  Порог = dB_пика - цвет.",
        "  Больше значение -> ниже порог ->",
        "  границы РАСШИРЯЮТСЯ.",
        "  Не зависит от палитры (vmin/vmax).",
        "",
        "Пик,Гц (1-30):",
        "  Полуширина поиска центра гармоники.",
        "  Меньше -> жёстче трекинг,",
        "  но риск потерять сместившуюся гармонику.",
    ],
    4: [
        "Цвет,инд (1-255):",
        "  Множитель сигмы шума.",
        "  Порог = медиана_шума + цвет * сигма.",
        "  Больше значение -> выше порог ->",
        "  границы СУЖАЮТСЯ.",
        "  Шум оценивается по краям полосы (по 20%).",
        "  Не зависит от палитры (vmin/vmax).",
        "",
        "Пик,Гц (1-30):",
        "  Полуширина поиска центра гармоники.",
        "  Меньше -> жёстче трекинг,",
        "  но риск потерять сместившуюся гармонику.",
    ],
    5: [
        "Цвет,инд (1-255):",
        "  Доля от 3 dB (классический -3 dB).",
        "  Порог = dB_пика - (цвет/255)*3.",
        "  255 = полный спад на 3 dB.",
        "  Больше значение -> ниже порог ->",
        "  границы РАСШИРЯЮТСЯ.",
        "  Не зависит от палитры (vmin/vmax).",
        "",
        "Пик,Гц (1-30):",
        "  Полуширина поиска центра гармоники.",
        "  Меньше -> жёстче трекинг,",
        "  но риск потерять сместившуюся гармонику.",
    ],
    6: [
        "Цвет,инд (1-255):",
        "  Доля суммарной мощности (255=100%).",
        "  Накопление линейной мощности от центра",
        "  наружу до заданной доли.",
        "  Больше значение -> границы РАСШИРЯЮТСЯ.",
        "  Не зависит от палитры (vmin/vmax).",
        "  Не использует порог в dB.",
        "",
        "Пик,Гц (1-30):",
        "  Полуширина поиска центра гармоники.",
        "  Меньше -> жёстче трекинг,",
        "  но риск потерять сместившуюся гармонику.",
    ],
}

HELP_LEFT_LINES = [
    "ПОМОЩЬ — audio_speed_calc v0.53",
    "",
    "МЫШЬ:",
    "  Drag — выбор диапазона + масштаб",
    "  Клик — выбор гармоники H5/H4",
    "",
    "СОСТОЯНИЕ (статусная строка):",
    "  [N/5]  — текущий шаг",
    "  Ист: N/M — позиция в истории / всего",
    "  Шаг=X.XXXс — текущий шаг расчёта",
    "",
    "КНОПКИ СЛЕВА:",
    "  Спектр — режим time/power",
    "  5я/4я — выбор гармоник",
    "",
    "КНОПКИ СЕРЕДИНА (v0.53):",
    "  GPS→H5 — кривая H5 из GPS",
    "  GPS→H4 — кривая H4 из GPS",
    "  Подп.V — подписи пиков скорости",
    "  Повторное нажатие — выкл.",
    "",
    "ЦВЕТА ПОДПИСЕЙ:",
    "  маджента — GPS-H5",
    "  голубой  — GPS-H4",
    "  жёлтый   — реал.H5",
    "  зелёный  — реал.H4",
    "",
    "КНОПКИ АЛГОРИТМОВ:",
    "  1.dB  2.Пал  3.Отн",
    "  4.Шум 5.-3dB 6.RMS",
    "  ? — справка по алгоритму",
    "",
    "СЛАЙДЕРЫ И ПОЛЯ:",
    "  Fmin/Fmax — диапазон частот",
    "  Ширина — полосы гармоник",
    "  Шаг,с — шаг расчёта (0.01-5.0)",
    "  Цвет — градаций палитры (1-255)",
    "  Пик,Гц — поиск пика (1-30)",
]

HELP_RIGHT_LINES = [
    "ОВЕРЛЕЙ:",
    "  Прям. — линии H5/H4 min/max",
    "  Факт. — кривые по шагам",
    "  Оба   — и линии, и кривые",
    "  Выкл  — ничего",
    "",
    "НАВИГАЦИЯ ПО ИСТОРИИ:",
    "  Назад / Вперед — по позициям",
    "  Сохр.поз. — записать текущую",
    "  Удалить поз. — стереть текущую",
    "  Сб.время / Сб.част / Сброс",
    "",
    "КЛАВИШИ:",
    "  ? — помощь (это окно)",
    "  Q — выход   S — сохранить конфиг",
    "  P — PNG-скриншот со штампом",
    "  Z — шаг назад по истории",
    "  Shift+Z — общий сброс",
    "  Esc — отмена / фаза TIME",
    "  1..6 — запуск алгоритма",
    "  Tab / Shift+Tab — поля ввода",
    "",
    "ЧТО ХРАНИТ ИСТОРИЯ:",
    "  Оси X/Y, диапазон анализа,",
    "  H5/H4, алгоритм, Шаг, Цвет,",
    "  Пик, Ширину, режим оверлея.",
    "",
    "ЛОГИКА РАСЧЁТА:",
    "  f0 = (H5/5 + H4/4) / 2",
    "  V = 0.377·Rk·n / (Io·Ik)",
    "  Rk = 0.85·P·H/1e5 + 0.0127·R",
    "  dn = 60·df·stroke / cyl",
    "",
    "ФАЙЛЫ:",
    "  <file>_config.json — настройки",
    "  <file>_speed_report_algoN.txt — отчёт",
    "  <file>_spec_YYYYMMDD_HHMMSS.png — PNG",
    "  history_log.txt — журнал версий",
]


def find_range_1(freqs, spectrum_col, target_freq, color_steps,
                 vmin, vmax, cmap_n=256, f_lo=None, f_hi=None):
    norm = matplotlib.colors.Normalize(vmin=vmin, vmax=vmax)
    idx_threshold = max(0, min(cmap_n - 1, int(color_steps)))
    db_threshold = float(norm.inverse(idx_threshold / (cmap_n - 1)))
    return _search_threshold(freqs, spectrum_col, target_freq, db_threshold, f_lo, f_hi)


def find_range_2(freqs, spectrum_col, target_freq, color_steps,
                 vmin, vmax, cmap_n=256, f_lo=None, f_hi=None):
    target_idx = int(np.argmin(np.abs(freqs - target_freq)))
    db_center = spectrum_col[target_idx]
    norm = matplotlib.colors.Normalize(vmin=vmin, vmax=vmax)
    idx_center = int(norm(db_center) * (cmap_n - 1))
    idx_threshold = max(0, idx_center - int(color_steps))
    db_threshold = float(norm.inverse(idx_threshold / (cmap_n - 1)))
    return _search_threshold(freqs, spectrum_col, target_freq, db_threshold, f_lo, f_hi)


def find_range_3(freqs, spectrum_col, target_freq, color_steps,
                 vmin, vmax, cmap_n=256, f_lo=None, f_hi=None):
    target_idx = int(np.argmin(np.abs(freqs - target_freq)))
    db_center = float(spectrum_col[target_idx])
    db_threshold = db_center - float(color_steps)
    return _search_threshold(freqs, spectrum_col, target_freq, db_threshold, f_lo, f_hi)


def find_range_4(freqs, spectrum_col, target_freq, color_steps,
                 vmin, vmax, cmap_n=256, f_lo=None, f_hi=None):
    if f_lo is None:
        f_lo = freqs[0]
    if f_hi is None:
        f_hi = freqs[-1]
    lo_idx = int(np.argmin(np.abs(freqs - f_lo)))
    hi_idx = int(np.argmin(np.abs(freqs - f_hi)))
    target_idx = int(np.argmin(np.abs(freqs - target_freq)))
    band = hi_idx - lo_idx
    noise_n = max(3, band // 5)
    noise_lo = spectrum_col[lo_idx:lo_idx + noise_n]
    noise_hi = spectrum_col[hi_idx - noise_n:hi_idx + 1]
    noise = np.concatenate([noise_lo, noise_hi])
    noise_median = float(np.median(noise))
    noise_std = float(np.std(noise))
    if noise_std < 1e-6:
        noise_std = 1.0
    db_threshold = noise_median + float(color_steps) * noise_std
    db_center = float(spectrum_col[target_idx])
    if db_threshold >= db_center:
        db_threshold = db_center - 1.0
    return _search_threshold(freqs, spectrum_col, target_freq, db_threshold, f_lo, f_hi)


def find_range_5(freqs, spectrum_col, target_freq, color_steps,
                 vmin, vmax, cmap_n=256, f_lo=None, f_hi=None):
    target_idx = int(np.argmin(np.abs(freqs - target_freq)))
    db_center = float(spectrum_col[target_idx])
    db_drop = float(color_steps) / 255.0 * 3.0
    db_threshold = db_center - db_drop
    return _search_threshold(freqs, spectrum_col, target_freq, db_threshold, f_lo, f_hi)


def find_range_6(freqs, spectrum_col, target_freq, color_steps,
                 vmin, vmax, cmap_n=256, f_lo=None, f_hi=None):
    target_idx = int(np.argmin(np.abs(freqs - target_freq)))
    if f_lo is None:
        f_lo = freqs[0]
    if f_hi is None:
        f_hi = freqs[-1]
    lo_idx = int(np.argmin(np.abs(freqs - f_lo)))
    hi_idx = int(np.argmin(np.abs(freqs - f_hi)))

    power = np.power(10.0, spectrum_col / 20.0)
    fraction = max(0.01, min(1.0, float(color_steps) / 255.0))
    total_power = float(np.sum(power[lo_idx:hi_idx + 1]))
    if total_power < 1e-10:
        return float(freqs[target_idx]), float(freqs[target_idx])

    cumsum = 0.0
    h_max_idx = target_idx
    for i in range(target_idx, hi_idx + 1):
        cumsum += power[i]
        if cumsum / total_power >= fraction:
            h_max_idx = i
            break
        h_max_idx = i

    cumsum = 0.0
    h_min_idx = target_idx
    for i in range(target_idx, lo_idx - 1, -1):
        cumsum += power[i]
        if cumsum / total_power >= fraction:
            h_min_idx = i
            break
        h_min_idx = i

    if h_max_idx > target_idx and h_max_idx - 1 >= 0:
        cumsum_before = float(np.sum(power[target_idx:h_max_idx]))
        cumsum_after = cumsum_before + float(power[h_max_idx])
        target_cum = fraction * total_power
        if cumsum_after - cumsum_before > 1e-10:
            frac = (target_cum - cumsum_before) / (cumsum_after - cumsum_before)
            frac = max(0.0, min(1.0, frac))
            h_max = float(freqs[h_max_idx - 1] + frac *
                          (freqs[h_max_idx] - freqs[h_max_idx - 1]))
        else:
            h_max = float(freqs[h_max_idx])
    else:
        h_max = float(freqs[h_max_idx])

    if h_min_idx < target_idx and h_min_idx + 1 < len(freqs):
        cumsum_before = float(np.sum(power[h_min_idx + 1:target_idx + 1]))
        cumsum_after = cumsum_before + float(power[h_min_idx])
        target_cum = fraction * total_power
        if cumsum_after - cumsum_before > 1e-10:
            frac = (target_cum - cumsum_before) / (cumsum_after - cumsum_before)
            frac = max(0.0, min(1.0, frac))
            h_min = float(freqs[h_min_idx + 1] + frac *
                          (freqs[h_min_idx] - freqs[h_min_idx + 1]))
        else:
            h_min = float(freqs[h_min_idx])
    else:
        h_min = float(freqs[h_min_idx])

    return h_min, h_max


_FIND_RANGE_FUNCS = {
    1: find_range_1, 2: find_range_2, 3: find_range_3,
    4: find_range_4, 5: find_range_5, 6: find_range_6,
}


def find_range_dispatch(algo, freqs, spectrum_col, target_freq, color_steps,
                        vmin, vmax, cmap_n=256, f_lo=None, f_hi=None):
    func = _FIND_RANGE_FUNCS.get(algo, find_range_2)
    return func(freqs, spectrum_col, target_freq, color_steps,
                vmin, vmax, cmap_n, f_lo, f_hi)


def get_search_bounds(harm_freqs, n_harm, target_idx):
    n = len(harm_freqs)
    if n <= 1:
        return None, None
    target_idx = max(0, min(target_idx, n - 1))
    if target_idx > 0:
        f_lo = (harm_freqs[target_idx - 1] + harm_freqs[target_idx]) / 2
    else:
        f_lo = harm_freqs[target_idx] / 2 if harm_freqs[target_idx] > 0 else None
    if target_idx < n - 1:
        f_hi = (harm_freqs[target_idx] + harm_freqs[target_idx + 1]) / 2
    else:
        f_hi = harm_freqs[target_idx] * 1.5
    return f_lo, f_hi

class AudioSpeedAnalyzer:

    PHASE_TIME = 'time'
    PHASE_H5 = 'h5'
    PHASE_H4 = 'h4'
    PHASE_DONE = 'done'

    def __init__(self, filepath, config, config_path):
        self.filepath = filepath
        self.cfg = config
        self.config_path = config_path
        self.audio = None
        self.sr = config['sample_rate']
        self.duration = 0
        self.freqs = None
        self.times = None
        self.S_db = None
        self.n_frames = 0
        self.frame_idx = None
        self.analyze_start = 0
        self.analyze_dur = 0
        self.h5 = config.get('harmonic_5_freq')
        self.h4 = config.get('harmonic_4_freq')
        self.f0 = None
        self.n_harm = 0
        self.harm_freqs = []
        self.phase = self.PHASE_TIME
        self.line_width = int(config.get('harmonic_line_width', 3))
        self.color_steps = int(config.get('color_steps', 10))
        self.help_visible = False
        self.time_marker = None
        self.time_span = None
        self.span_selector = None
        self.harm_lines = []
        self.harm_bands = []
        self.harm_labels = []
        self.spec_img = None
        self._cbar = None
        self._spec_vmin = None
        self._spec_vmax = None
        self._xaxis_fps = config['target_fps']
        self.h5_min = None
        self.h5_max = None
        self.h4_min = None
        self.h4_max = None
        self.range_lines = []
        self.range_labels = []
        self.cursor_text = None
        self.drag_text = None
        self._drag_start_frame = None
        self.zoom_history = []
        self.zoom_pointer = -1
        self._last_reset_action = None
        self.tb_x_start = None
        self.tb_x_end = None
        self.tb_dx = None
        self.tb_y_min = None
        self.tb_y_max = None
        self.tb_width = None
        self.tb_step = None
        self.tb_color = None
        self.tb_peak = None
        self._syncing_fields = False
        self._tb_list = []
        self._updating_axes = False
        self._tb_focused_idx = -1
        self.tube_t = []
        self.tube_h5_mins = []
        self.tube_h5_maxs = []
        self.tube_h4_mins = []
        self.tube_h4_maxs = []
        self.tube_collections = []
        self.tube_center_lines = []
        self.tube_h5_centers = []
        self.tube_h4_centers = []
        self.overlay_objects = []
        self._algo_help_visible_num = None
        self._algo_help_obj = None
        self.peak_search_hz = float(config.get('peak_search_hz', 5.0))
        self.algorithm = int(config.get('algorithm', 2))
        self.last_algo = self.algorithm
        self._algo_run_done = False
        self.overlay_mode = int(config.get('overlay_mode', 0))
        self._algo_results = {}
        self.gps_points = []
        self.gps_method = 'none'
        self.gps_lag = 0.0
        self.gps_corr = 0.0
        self._png_list = []
        self._footer_text = None
        self._sys_info_printed = False
        self._closing = False

        # v0.53: оверлей GPS-скорости и подписи пиков
        self.speed_overlay_h5 = False
        self.speed_overlay_h4 = False
        self._speed_lines = []
        self.speed_labels_on = False
        self._speed_label_objs = []

        # v0.53.1: Vmax/Vmin в окне + GPS-средняя для статуса
        self.gps_v_avg = None
        self._extreme_objs = []

        # v0.53.2: защита от повторного входа в пересчёт
        self._recalc_in_progress = False

        _override_init = config.get('gps_lag_override')
        if _override_init is not None:
            try:
                self.gps_lag = float(_override_init)
            except (ValueError, TypeError):
                pass

        sm = config.get('spec_mode', 'time')
        if sm == 'freq':
            sm = 'power'
        self.spec_mode = sm

    # ---------- расчёт скорости ----------

    def _calc_speed(self, h5_c, h4_c):
        cyl = self.cfg['cylinders']
        stroke = self.cfg['stroke']
        P = self.cfg['tire_width']
        H = self.cfg['tire_profile']
        R = self.cfg['rim_diameter']
        Io = self.cfg['final_drive']
        Ik = self.cfg['gear_ratio']

        f5 = h5_c / 5.0
        f4 = h4_c / 4.0
        f = (f5 + f4) / 2.0
        n = 60.0 * f * stroke / cyl
        Rk = 0.85 * P * H / 100000.0 + 0.0127 * R
        self.cfg['_rk'] = Rk
        V = 0.377 * Rk * n / (Io * Ik)
        return f, n, Rk, V

    def _calc_speed_error(self, h5_min, h5_max, h4_min, h4_max, f, V):
        df = (abs(h5_max - h5_min) / 5.0 + abs(h4_max - h4_min) / 4.0) / 2.0 / 2.0
        cyl = self.cfg['cylinders']
        stroke = self.cfg['stroke']
        Rk = self.cfg.get('_rk', 0.0)
        Io = self.cfg['final_drive']
        Ik = self.cfg['gear_ratio']
        dn = 60.0 * df * stroke / cyl
        dV = abs(0.377 * Rk * dn / (Io * Ik)) if (Io * Ik) > 0 else 0.0
        return df, dV

    def _write_speed_report(self, filepath, algo, f_start, f_end, step_sec,
                            color_steps, peak_hz, vmin, vmax,
                            f_lo5, f_hi5, f_lo4, f_hi4,
                            step_data, h5_glob_min, h5_glob_max,
                            h4_glob_min, h4_glob_max,
                            v_min, v_max, v_avg, dv_min, dv_max, dv_avg,
                            f0_val, n_min, n_max, Rk_val):
        cyl = self.cfg['cylinders']
        stroke = self.cfg['stroke']
        P = self.cfg['tire_width']
        H = self.cfg['tire_profile']
        R = self.cfg['rim_diameter']
        Io = self.cfg['final_drive']
        Ik = self.cfg['gear_ratio']
        fps = self.cfg['target_fps']

        sep = "=" * 60
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"Отчёт расчёта скорости — audio_speed_calc v{VERSION}\n")
            f.write(f"Файл: {os.path.basename(self.filepath)}\n")
            f.write(f"Длительность: {self.duration:.3f} сек "
                    f"(кадров: {self.n_frames})\n")
            f.write(f"Алгоритм: {algo} ({ALGO_NAMES.get(algo, '?')})\n\n")

            # --- Диапазон анализа ---
            f.write(sep + "\n")
            f.write("ДИАПАЗОН АНАЛИЗА\n")
            f.write(sep + "\n")
            f.write(f"  Кадры:     {f_start}-{f_end}  "
                    f"({f_end - f_start} кадров)\n")
            f.write(f"  Время:     {f_start/fps:.3f}-{f_end/fps:.3f}с  "
                    f"({(f_end - f_start)/fps:.3f}с)\n")
            f.write(f"  FPS:       {fps}\n")
            step_actual_frames_r0 = int(round(step_sec * fps))
            step_actual_sec_r0 = step_actual_frames_r0 / fps
            f.write(f"  Шаг:       {step_sec:.3f} сек "
                    f"(~{step_sec * fps:.1f} кадров, "
                    f"факт {step_actual_frames_r0} кадр = "
                    f"{step_actual_sec_r0:.3f}с)\n")
            f.write(f"  Шагов:     {len(step_data)}\n")
            f.write(sep + "\n\n")

            now_dt = datetime.datetime.now().strftime('%d.%m.%Y %H:%M:%S')
            f.write(f"Дата и время анализа: {now_dt}\n")
            _, sections = self._collect_sysinfo_lines()
            for title, slines in sections:
                f.write(sep + "\n")
                f.write(title + "\n")
                f.write(sep + "\n")
                for sl in slines:
                    f.write(sl + "\n")
            f.write(sep + "\n\n")
            f.write(f"Кадры: {f_start}-{f_end}  "
                    f"Время: {f_start/fps:.3f}-{f_end/fps:.3f}с\n")
            step_actual_frames_r = int(round(step_sec * fps))
            step_actual_sec_r = step_actual_frames_r / fps
            f.write(f"Шаг: {step_sec:.3f} сек (~{step_sec * fps:.1f} кадров, "
                    f"факт {step_actual_frames_r} кадр = "
                    f"{step_actual_sec_r:.3f}с)\n\n")

            f.write("Параметры автомобиля:\n")
            f.write(f"  Цилиндров: {cyl}\n")
            f.write(f"  Тактность: {stroke} "
                    f"({'4-такт' if stroke == 2 else '2-такт'})\n")
            f.write(f"  Шина: {P}/{H} R{R}\n")
            f.write(f"  Главная пара: {Io}\n")
            f.write(f"  Передача: {Ik}\n\n")

            f.write("Формулы:\n")
            f.write("  f5 = H5ц / 5\n")
            f.write("  f4 = H4ц / 4\n")
            f.write("  f  = (f5 + f4) / 2\n")
            f.write("  n  = 60 * f * stroke / cylinders\n")
            f.write("  Rk = 0.85 * P * H / 100000 + 0.0127 * R\n")
            f.write("  V  = 0.377 * Rk * n / (Io * Ik)\n")
            f.write("  df = ((H5max-H5min)/5 + (H4max-H4min)/4) / 2 / 2\n")
            f.write("  dn = 60 * df * stroke / cyl\n")
            f.write("  dV = 0.377 * Rk * dn / (Io * Ik)\n\n")

            f.write(f"Rk = 0.85 * {P} * {H} / 100000 + 0.0127 * {R} = "
                    f"{Rk_val:.4f} м\n\n")

            f.write("Пошаговый расчёт:\n")
            f.write(f"{'N':>3s}  {'Время,с':>8s}  {'H5 min':>10s}  "
                    f"{'H5 max':>10s}  {'H4 min':>10s}  {'H4 max':>10s}  "
                    f"{'H5ц':>8s}  {'H4ц':>8s}  {'f,Гц':>8s}  "
                    f"{'n,об/мин':>10s}  {'V,км/ч':>8s}  {'±V,км/ч':>8s}\n")
            f.write("-" * 120 + "\n")

            for i, sd in enumerate(step_data):
                t_sec = sd['t_sec']
                h5mn, h5mx = sd['h5_min'], sd['h5_max']
                h4mn, h4mx = sd['h4_min'], sd['h4_max']
                h5c, h4c = sd['h5_c'], sd['h4_c']
                f_val, n_val = sd['f'], sd['n']
                v_val, dv_val = sd['V'], sd['dV']

                f.write(f"{i+1:3d}  {t_sec:8.3f}  {h5mn:10.3f}  "
                        f"{h5mx:10.3f}  {h4mn:10.3f}  {h4mx:10.3f}  "
                        f"{h5c:8.3f}  {h4c:8.3f}  {f_val:8.3f}  "
                        f"{n_val:10.0f}  {v_val:8.2f}  {dv_val:8.2f}\n")

                f5 = h5c / 5.0
                f4 = h4c / 4.0
                f0 = (f5 + f4) / 2.0
                n0 = 60.0 * f0 * stroke / cyl
                df_val = (abs(h5mx - h5mn) / 5.0
                          + abs(h4mx - h4mn) / 4.0) / 2.0 / 2.0
                f.write("     Подстановка:\n")
                f.write(f"       f5 = {h5c:.3f}/5 = {f5:.3f} Гц\n")
                f.write(f"       f4 = {h4c:.3f}/4 = {f4:.3f} Гц\n")
                f.write(f"       f  = ({f5:.3f}+{f4:.3f})/2 = {f0:.3f} Гц\n")
                f.write(f"       n  = 60*{f0:.3f}*{stroke}/{cyl} = "
                        f"{n0:.0f} об/мин\n")
                f.write(f"       V  = 0.377*{Rk_val:.4f}*{n0:.0f}/"
                        f"({Io}*{Ik}) = {v_val:.2f} км/ч\n")
                f.write(f"       df = (({h5mx:.3f}-{h5mn:.3f})/5 + "
                        f"({h4mx:.3f}-{h4mn:.3f})/4) / 2 / 2 = "
                        f"{df_val:.3f} Гц\n")
                dn0 = 60.0 * df_val * stroke / cyl
                dV0 = abs(0.377 * Rk_val * dn0 / (Io * Ik)) \
                    if (Io * Ik) > 0 else 0.0
                f.write(f"       dn = 60*{df_val:.3f}*{stroke}/{cyl} = "
                        f"{dn0:.0f} об/мин\n")
                f.write(f"       dV = 0.377*{Rk_val:.4f}*{dn0:.0f}/"
                        f"({Io}*{Ik}) = {dV0:.2f} км/ч\n")
                f.write(f"       ИТОГ ШАГА: V = {v_val:.2f} ± "
                        f"{dV0:.2f} км/ч\n\n")

            f.write("\nИТОГ (за весь отрезок):\n")
            f.write(f"  H5: {h5_glob_min:.3f} - {h5_glob_max:.3f} Гц\n")
            f.write(f"  H4: {h4_glob_min:.3f} - {h4_glob_max:.3f} Гц\n")
            f.write(f"  V min  = {v_min:.2f} ± {dv_min:.2f} км/ч\n")
            f.write(f"  V max  = {v_max:.2f} ± {dv_max:.2f} км/ч\n")
            f.write(f"  V ср   = {v_avg:.2f} ± {dv_avg:.2f} км/ч\n")
            f.write(f"  n      = {n_min:.0f} - {n_max:.0f} об/мин\n")
            f.write(f"  f0     = {f0_val:.3f} Гц\n")
            f.write(f"  Rk     = {Rk_val:.4f} м\n")

            if self.gps_points:
                gps_t_arr_r = [p[0] for p in self.gps_points]
                gps_v_arr_r = [p[1] for p in self.gps_points]
                t_start_gps_r = f_start / fps + self.gps_lag
                t_end_gps_r = f_end / fps + self.gps_lag
                gps_in_win_r, gps_before_r, gps_after_r = find_gps_window_points(
                    self.gps_points, t_start_gps_r, t_end_gps_r)
                if not gps_in_win_r and len(gps_t_arr_r) >= 2:
                    t_mid_r = (t_start_gps_r + t_end_gps_r) / 2
                    if gps_t_arr_r[0] <= t_mid_r <= gps_t_arr_r[-1]:
                        gps_in_win_r = [(t_mid_r, float(np.interp(
                            t_mid_r, gps_t_arr_r, gps_v_arr_r)), 0.0)]
                f.write("\nGPS-СРАВНЕНИЕ:\n")
                f.write(f"  Метод: {self.gps_method}\n")
                f.write(f"  GPS-меток в файле: {len(self.gps_points)}\n")
                f.write(f"  Запаздывание GPS: {self.gps_lag:+.3f} сек "
                        f"(корреляция: {self.gps_corr:.3f})\n")
                f.write(f"  Окно аудио: {f_start/fps:.3f}-{f_end/fps:.3f}с\n")
                f.write(f"  Окно GPS (с поправкой): "
                        f"{t_start_gps_r:.3f}-{t_end_gps_r:.3f}с\n")
                f.write("  Соседние GPS-точки:\n")
                if gps_before_r:
                    f.write(f"    [{gps_before_r[0]:.3f}с] "
                            f"{gps_before_r[1]:.1f} км/ч  "
                            f"({gps_before_r[2]:.3f}с до окна)\n")
                for pt in gps_in_win_r:
                    f.write(f"    [{pt[0]:.3f}с] {pt[1]:.1f} км/ч  "
                            f"(в окне)\n")
                if gps_after_r:
                    f.write(f"    [{gps_after_r[0]:.3f}с] "
                            f"{gps_after_r[1]:.1f} км/ч  "
                            f"({gps_after_r[2]:.3f}с после окна)\n")
                if gps_in_win_r:
                    gps_avg_r = sum(p[1] for p in gps_in_win_r) \
                        / len(gps_in_win_r)
                    f.write(f"  V аудио (ср): {v_avg:.2f} км/ч\n")
                    f.write(f"  V GPS  (ср): {gps_avg_r:.2f} км/ч "
                            f"(интерполяция по "
                            f"{len(gps_in_win_r)} точкам)\n")
                    f.write(f"  dV: {v_avg - gps_avg_r:+.2f} км/ч\n")
                else:
                    f.write("  GPS-точек в окне не найдено\n")

            png_list = getattr(self, '_png_list', [])
            if png_list:
                f.write("\n" + sep + "\n")
                f.write(f"  СКРИНШОТЫ ({len(png_list)} шт.)\n")
                f.write(sep + "\n")
                for i, p in enumerate(png_list):
                    f.write(f"  {i+1}. {p['name']}\n")
                    f.write(f"     Размер: {p['size']} байт   "
                            f"MD5: {p['md5']}   Дата: {p['date']}\n")

    def _save_window_geometry(self):
        try:
            mgr = self.fig.canvas.manager
            win = mgr.window
            import re
            if hasattr(win, 'geometry'):
                geo = win.geometry()
                m = re.match(r'(\d+)x(\d+)\+(-?\d+)\+(-?\d+)', geo)
                if m:
                    self.cfg['win_w'] = int(m.group(1))
                    self.cfg['win_h'] = int(m.group(2))
                    self.cfg['win_x'] = int(m.group(3))
                    self.cfg['win_y'] = int(m.group(4))
                    return
            if hasattr(win, 'geometry'):
                g = win.geometry()
                self.cfg['win_w'] = int(g.width())
                self.cfg['win_h'] = int(g.height())
                self.cfg['win_x'] = int(g.x())
                self.cfg['win_y'] = int(g.y())
        except Exception:
            pass

    def _restore_window_geometry(self):
        w = self.cfg.get('win_w')
        h = self.cfg.get('win_h')
        x = self.cfg.get('win_x')
        y = self.cfg.get('win_y')
        if w is None or h is None:
            return
        try:
            mgr = self.fig.canvas.manager
            win = mgr.window
            if hasattr(win, 'geometry') and not hasattr(win, 'setGeometry'):
                win.geometry(f"{w}x{h}+{x}+{y}")
            elif hasattr(win, 'setGeometry'):
                win.setGeometry(x, y, w, h)
        except Exception:
            pass

    def run(self):
        _init_ffmpeg()
        if _FFMPEG_PATH:
            print(f"ffmpeg: {_FFMPEG_PATH}")
        else:
            print("ffmpeg не найден. Положите ffprobe.exe и ffmpeg.exe "
                  "рядом со скриптом или в папку bin/.")
            return
        if _FFPROBE_PATH:
            print(f"ffprobe: {_FFPROBE_PATH}")
        else:
            print("Предупреждение: ffprobe не найден — длительность "
                  "будет определена по факту.")

        dur, dur_source = get_ts_duration(self.filepath)
        if dur:
            self.duration = dur
            print(f"Файл: {os.path.basename(self.filepath)}  "
                  f"длительность: {dur:.3f} сек ({dur_source})")
            extract_dur = self.duration
        else:
            print("Длительность неизвестна (ffprobe), извлекаем полностью...")
            extract_dur = 3600.0

        print(f"Извлечение аудио (до {extract_dur:.0f} сек)...")
        self.audio, self.sr = extract_audio(self.filepath, 0, extract_dur,
                                            self.sr)
        if self.audio is None or len(self.audio) == 0:
            print("Не удалось извлечь аудио.")
            return

        actual_dur = len(self.audio) / self.sr
        self.duration = actual_dur
        if dur is None:
            print(f"  {len(self.audio)} сэмплов, {actual_dur:.3f} сек, "
                  f"SR={self.sr} (фактический замер аудио)")
        else:
            diff_ms = abs(dur - actual_dur) * 1000
            print(f"  {len(self.audio)} сэмплов, {actual_dur:.3f} сек, "
                  f"SR={self.sr}")
            if diff_ms > 1:
                print(f"  (ffprobe: {dur:.3f} сек, факт: {actual_dur:.3f} "
                      f"сек — расхождение {diff_ms:.0f} мс)")

        print("Вычисление спектрограммы...")
        self.freqs, self.times, self.S_db = compute_stft(
            self.audio, self.sr,
            self.cfg['fft_size'], self.cfg['target_fps']
        )
        self.n_frames = len(self.times)
        self.frame_idx = np.arange(self.n_frames)
        print(f"  Кадров: {self.n_frames}")

        self.gps_points = []
        self.gps_method = 'none'
        self.gps_lag = 0.0
        self.gps_corr = 0.0

        _override_before = self.cfg.get('gps_lag_override')
        if _override_before is not None:
            try:
                self.gps_lag = float(_override_before)
            except (ValueError, TypeError):
                pass

        try:
            gps_raw, self.gps_method = extract_gps(self.filepath)
            if gps_raw:
                self.gps_points = gps_to_relative_time(gps_raw)
                print(f"GPS: {len(self.gps_points)} меток "
                      f"({self.gps_method})")
                if self.gps_points:
                    first = self.gps_points[0]
                    last = self.gps_points[-1]
                    print(f"  Первая точка: t={first[0]:.1f}с, "
                          f"V={first[1]:.1f} км/ч")
                    print(f"  Последняя точка: t={last[0]:.1f}с, "
                          f"V={last[1]:.1f} км/ч")
            else:
                print("GPS: меток не найдено")
        except Exception as e:
            print(f"GPS: ошибка извлечения ({e})")
            self.gps_points = []

        hop = max(1, int(self.sr / self.cfg['target_fps']))
        frame_dur = hop / self.sr
        self.analyze_dur = int(self.cfg['report_duration'] / frame_dur)
        if self.analyze_dur < 1:
            self.analyze_dur = self.n_frames

        self._png_list = []
        self._footer_text = None
        self._sys_info_printed = False
        self.build_gui()
        self._restore_window_geometry()

        if self.h5 and self.h4:
            self.calc_harmonics()
            self.draw_harmonics()
            self.phase = self.PHASE_DONE
            self.update_status()

        self._restore_view()
        self._update_overlay_buttons()
        self._update_algo_buttons()
        if self.harm_freqs:
            self.draw_harmonics()
        self.draw_ranges()
        self.draw_tubes()

        self.update_status()
        self.fig.canvas.draw_idle()
        plt.show()

    def build_gui(self):
        saved_w = self.cfg.get('win_w')
        saved_h = self.cfg.get('win_h')
        dpi = 100
        if saved_w and saved_h and saved_w > 100 and saved_h > 100:
            fw = saved_w / dpi
            fh = max(3, (saved_h - 28) / dpi)
        else:
            try:
                import tkinter as tk
                root = tk.Tk()
                sw = root.winfo_screenwidth()
                sh = root.winfo_screenheight()
                root.destroy()
                fw = min(15, sw / 100)
                fh = min(9, sh / 100)
            except Exception:
                fw, fh = 15, 9

        self.fig, self.ax_spec = plt.subplots(1, 1, figsize=(fw, fh))
        self.fig.suptitle(
            f'audio_speed_calc v{VERSION} — '
            f'{os.path.basename(self.filepath)}',
            fontsize=10, y=0.98
        )
        # v0.53.1: график придвинут к среднему столбцу кнопок.
        self.fig.subplots_adjust(left=0.08, right=0.69, top=0.91, bottom=0.22)

        self._draw_spectrogram()

        self.ax_spec.set_autoscalex_on(False)
        self.ax_spec.set_autoscaley_on(False)

        self.span_selector = SpanSelector(
            self.ax_spec, self._on_span, 'horizontal',
            useblit=True,
            props=dict(alpha=0.3, facecolor='red'),
            interactive=True, drag_from_anywhere=False
        )

        sx, sw = 0.10, 0.15
        self.sl_fmin = Slider(
            self.fig.add_axes([sx, 0.04, sw, 0.018]),
            '     Fmin', 20, 600, valinit=self.cfg['freq_min'], valfmt='%d'
        )
        self.sl_fmax = Slider(
            self.fig.add_axes([sx, 0.085, sw, 0.018]),
            '     Fmax', 20, 600, valinit=self.cfg['freq_max'], valfmt='%d'
        )
        wx = sx + sw + 0.05
        self.sl_width = Slider(
            self.fig.add_axes([wx, 0.04, 0.07, 0.018]),
            '     Ширина', 1, 20, valinit=self.line_width, valfmt='%d'
        )
        self.sl_step = Slider(
            self.fig.add_axes([wx, 0.085, 0.07, 0.018]),
            '     Шаг,с', 0.01, 5.0,
            valinit=float(self.cfg['time_step']), valfmt='%.3f'
        )
        cx = wx + 0.07 + 0.05
        self.sl_color = Slider(
            self.fig.add_axes([cx, 0.04, 0.07, 0.018]),
            '     Цвет,инд', 1, 255, valinit=self.color_steps, valfmt='%d'
        )
        px = cx + 0.07 + 0.05
        self.sl_peak = Slider(
            self.fig.add_axes([px, 0.04, 0.06, 0.018]),
            '     Пик,Гц', 1, 30, valinit=self.peak_search_hz, valfmt='%d'
        )

        self.sl_fmin.on_changed(self._on_freq)
        self.sl_fmax.on_changed(self._on_freq)
        self.sl_width.on_changed(self._on_width)
        self.sl_step.on_changed(self._on_step)
        self.sl_color.on_changed(self._on_color)
        self.sl_peak.on_changed(self._on_peak)

        btn_x = 0.84
        btn_w = 0.14
        btn_h = 0.035
        btn_gap = 0.003
        btn_help_w = 0.025
        btn_main_w = btn_w - btn_help_w - 0.002
        btn_labels = [
            ('Спектр', '_on_toggle_spec', False, None),
            ('5я гармоника', '_btn_h5', False, None),
            ('4я гармоника', '_btn_h4', False, None),
            ('1.dB', lambda _, n=1: self._on_run(None, n), True, None),
            ('2.Пал', lambda _, n=2: self._on_run(None, n), True, None),
            ('3.Отн', lambda _, n=3: self._on_run(None, n), True, None),
            ('4.Шум', lambda _, n=4: self._on_run(None, n), True, None),
            ('5.-3dB', lambda _, n=5: self._on_run(None, n), True, None),
            ('6.RMS', lambda _, n=6: self._on_run(None, n), True, None),
            ('Прям.', lambda _, n=0: self._set_overlay_mode(n), False, 0),
            ('Факт.', lambda _, n=1: self._set_overlay_mode(n), False, 1),
            ('Оба', lambda _, n=2: self._set_overlay_mode(n), False, 2),
            ('Выкл', lambda _, n=3: self._set_overlay_mode(n), False, 3),
            ('Сб.время', '_btn_reset_time', False, None),
            ('Сб.част', '_btn_reset_freq', False, None),
            ('Сброс', '_btn_reset_all', False, None),
            ('Назад', '_btn_back', False, None),
            ('Вперед', '_step_forward', False, None),
            ('Удалить поз.', '_btn_delete_pos', False, None),
            ('Сохр.поз.', '_btn_save_pos', False, None),
            ('Выход', '_btn_exit', False, None),
        ]
        n_btns = len(btn_labels)
        y_top = 0.876
        for i, (label, handler, is_algo, overlay_num) in enumerate(btn_labels):
            y = y_top - i * (btn_h + btn_gap)
            cb = handler if callable(handler) else getattr(self, handler)
            if is_algo:
                ax_btn = self.fig.add_axes([btn_x, y, btn_main_w, btn_h])
                btn = Button(ax_btn, label)
                btn.label.set_fontsize(8)
                btn.on_clicked(cb)
                setattr(self, f'_btn_widget_{i}', btn)
                algo_num = int(label.split('.')[0])
                setattr(self, f'_btn_widget_algo_{algo_num}', btn)
                ax_help = self.fig.add_axes(
                    [btn_x + btn_main_w + 0.002, y, btn_help_w, btn_h])
                btn_help = Button(ax_help, '?')
                btn_help.label.set_fontsize(7)
                btn_help.on_clicked(
                    lambda _, n=algo_num: self._show_algo_help(n))
                setattr(self, f'_btn_help_widget_{algo_num}', btn_help)
            else:
                ax_btn = self.fig.add_axes([btn_x, y, btn_w, btn_h])
                btn = Button(ax_btn, label)
                btn.label.set_fontsize(8)
                btn.on_clicked(cb)
                setattr(self, f'_btn_widget_{i}', btn)
                if overlay_num is not None:
                    setattr(self, f'_btn_widget_overlay_{overlay_num}', btn)

        btn_mid_x = 0.70
        btn_mid_w = 0.12
        btn_mid_h = 0.035
        btn_mid_gap = 0.003
        btn_mid_defs = [
            ('GPS→H5', 'h5'),
            ('GPS→H4', 'h4'),
            ('Подп.V', 'labels'),
        ]
        for i, (label, which) in enumerate(btn_mid_defs):
            y = y_top - i * (btn_mid_h + btn_mid_gap)
            ax_btn = self.fig.add_axes([btn_mid_x, y, btn_mid_w, btn_mid_h])
            b = Button(ax_btn, label)
            b.label.set_fontsize(8)
            if which == 'labels':
                b.on_clicked(lambda _: self._on_toggle_speed_labels())
            else:
                b.on_clicked(lambda _, w=which: self._on_speed_overlay(w))
            setattr(self, f'_btn_mid_{which}', b)
        self._update_speed_overlay_buttons()

        self.status = self.fig.text(
            0.02, 0.135, '', transform=self.fig.transFigure,
            fontsize=9, va='top', ha='left',
            bbox=dict(boxstyle='round', fc='white', alpha=0.85,
                      edgecolor='gray')
        )
        self.update_status()
        self._update_algo_buttons()
        self._update_overlay_buttons()

        self.cursor_text = self.ax_spec.text(
            0.99, 0.98, '', transform=self.ax_spec.transAxes,
            fontsize=8, va='top', ha='right',
            bbox=dict(boxstyle='round,pad=0.3', fc='lightyellow',
                      alpha=0.85, edgecolor='orange'),
            zorder=50
        )
        self.cursor_text.set_visible(False)

        self.drag_text = self.ax_spec.text(
            0.5, 0.98, '', transform=self.ax_spec.transAxes,
            fontsize=9, va='top', ha='center',
            bbox=dict(boxstyle='round,pad=0.4', fc='#fff8dc',
                      alpha=0.92, edgecolor='red', lw=1.2),
            zorder=51
        )
        self.drag_text.set_visible(False)

        tb_y = 0.005
        tb_h = 0.022
        tb_w = 0.04
        tb_step = 0.09
        tb_x = 0.10
        _tb_defs = [
            ('x_start', 'Xнач,с'), ('x_end', 'Xкон,с'), ('dx', 'dX,с'),
            ('y_min', 'Fmin'), ('y_max', 'Fmax'),
            ('width', 'Шир'), ('step', 'Шаг'), ('color', 'Цвет'),
            ('peak', 'Пик'),
        ]
        for i, (name, label) in enumerate(_tb_defs):
            tb = TextBox(
                self.fig.add_axes([tb_x + i * tb_step, tb_y, tb_w, tb_h]),
                label, initial=''
            )
            setattr(self, f'tb_{name}', tb)
            tb.on_submit(getattr(self, f'_on_field_{name}'))

        self._tb_list = [
            self.tb_x_start, self.tb_x_end, self.tb_dx,
            self.tb_y_min, self.tb_y_max,
            self.tb_width, self.tb_step, self.tb_color, self.tb_peak,
        ]
        self._tb_focused_idx = -1
        for idx, tb in enumerate(self._tb_list):
            self._patch_textbox(tb, idx)

        plt.rcParams['keymap.save'] = []
        plt.rcParams['keymap.pan'] = []

        self.fig.canvas.mpl_connect('key_press_event', self._on_key)
        self.fig.canvas.mpl_connect('button_press_event', self._on_click)
        self.fig.canvas.mpl_connect('motion_notify_event', self._on_motion)
        self.fig.canvas.mpl_connect('close_event', self._on_close)

    def _patch_textbox(self, tb, idx):
        orig_click = tb._click
        orig_keypress = tb._keypress

        def _click_handler(event):
            orig_click(event)
            try:
                tb.text_select_all()
            except Exception:
                pass

        def _keypress_handler(event):
            if event.key == 'tab':
                next_idx = (idx + 1) % len(self._tb_list)
                self._focus_textbox(next_idx, select_all=True)
                return
            elif event.key == 'shift+tab':
                prev_idx = (idx - 1) % len(self._tb_list)
                self._focus_textbox(prev_idx, select_all=True)
                return
            orig_keypress(event)

        tb._click = _click_handler
        tb._keypress = _keypress_handler

    def _focus_textbox(self, idx, select_all=True):
        if idx < 0 or idx >= len(self._tb_list):
            return
        tb = self._tb_list[idx]
        self._tb_focused_idx = idx
        try:
            canvas = self.fig.canvas
            if hasattr(canvas, 'widgetlock'):
                for other_tb in self._tb_list:
                    if other_tb is not tb:
                        try:
                            canvas.widgetlock.release(other_tb)
                        except Exception:
                            pass
                canvas.widgetlock(tb)
            if hasattr(tb, 'ax'):
                tb.ax.set_navigate(False)
            if select_all:
                try:
                    tb.text_select_all()
                except Exception:
                    pass
            self.fig.canvas.draw_idle()
        except Exception:
            pass

    def _move_focus(self, direction):
        canvas = self.fig.canvas
        current_idx = -1
        for i, tb in enumerate(self._tb_list):
            if hasattr(canvas, 'widgetlock') and not canvas.widgetlock.available(tb):
                current_idx = i
                break
        if current_idx < 0:
            next_idx = 0 if direction > 0 else len(self._tb_list) - 1
        else:
            try:
                canvas.widgetlock.release(self._tb_list[current_idx])
            except Exception:
                pass
            next_idx = (current_idx + direction) % len(self._tb_list)
        self._focus_textbox(next_idx)

    def _draw_spectrogram(self):
        if self.spec_img is not None:
            try:
                self.spec_img.remove()
            except Exception:
                pass
            self.spec_img = None
        if self._cbar is not None:
            try:
                self._cbar.remove()
            except Exception:
                pass
            self._cbar = None

        fmin = self.cfg['freq_min']
        fmax = self.cfg['freq_max']
        fmask = self.freqs <= fmax * 1.5
        S_subset = self.S_db[fmask, :]
        vmin = float(np.min(S_subset))
        vmax = float(np.max(S_subset))
        self._spec_vmin = vmin
        self._spec_vmax = vmax

        if self.spec_mode == 'time':
            self.spec_img = self.ax_spec.pcolormesh(
                self.frame_idx, self.freqs[fmask], self.S_db[fmask, :],
                shading='auto', cmap='inferno'
            )
            spec_title = 'Спектрограмма'
        else:
            n_levels = max(10, min(50, self.color_steps * 5))
            levels = np.linspace(vmin, vmax, n_levels)
            self.spec_img = self.ax_spec.contourf(
                self.frame_idx, self.freqs[fmask], self.S_db[fmask, :],
                levels=levels, cmap='inferno'
            )
            spec_title = 'Power Spectrum'

        self._cbar = self.fig.colorbar(self.spec_img, ax=self.ax_spec,
                                       label='дБ', pad=0.01)
        self.ax_spec.set_xlabel('Кадр / Время, с', labelpad=5)
        self.ax_spec.set_ylabel('Частота, Гц')
        self.ax_spec.set_ylim(fmin, fmax)
        self.ax_spec.set_xlim(0, self.n_frames)
        self.ax_spec.set_title(
            f'{spec_title} ({int(fmin)}-{int(fmax)} Гц)  |  '
            'drag = выбор/масштаб  |  клик = гармоники  |  '
            'Z = сброс  |  P = PNG  |  Q = выход  |  ? = помощь',
            fontsize=10
        )
        self.ax_spec.yaxis.set_major_formatter(
            plt.FuncFormatter(lambda x, _: f'{int(x)}')
        )
        self._xaxis_fps = self.cfg['target_fps']
        self.ax_spec.xaxis.set_major_formatter(
            plt.FuncFormatter(lambda x, _: f'{int(x)}\n{x/self._xaxis_fps:.3f}с')
        )

    def _on_toggle_spec(self, _=None):
        self.spec_mode = 'power' if self.spec_mode == 'time' else 'time'
        self.cfg['spec_mode'] = self.spec_mode
        self._draw_spectrogram()
        if self.harm_freqs:
            self.draw_harmonics()
        self.draw_ranges()
        self.draw_tubes()
        self._sync_fields()
        self.update_status()
        self.fig.canvas.draw_idle()

    def _btn_h5(self, _=None):
        self._set_phase(self.PHASE_H5)

    def _btn_h4(self, _=None):
        self._set_phase(self.PHASE_H4)

    def _set_overlay_mode(self, mode):
        self.overlay_mode = mode
        self.cfg['overlay_mode'] = mode
        self._update_overlay_buttons()
        self.draw_ranges()
        self.draw_tubes()
        self.fig.canvas.draw_idle()

    def _highlight_buttons(self, prefix, count, active_val, start=0):
        for n in range(start, start + count):
            w = getattr(self, f'{prefix}{n}', None)
            if w is None and prefix == '_btn_widget_algo_':
                idx = 2 + n
                w = getattr(self, f'_btn_widget_{idx}', None)
            if w is not None:
                if n == active_val:
                    w.color = '#ccffcc'
                    w.hovercolor = '#aaddaa'
                else:
                    w.color = '0.85'
                    w.hovercolor = '0.95'
                w.ax.set_facecolor(w.color)
                w.ax.figure.canvas.draw_idle()

    def _update_overlay_buttons(self):
        self._highlight_buttons('_btn_widget_overlay_', 4, self.overlay_mode)

    def _update_algo_buttons(self):
        active = self.last_algo if self._algo_run_done else None
        self._highlight_buttons('_btn_widget_algo_', 6, active, start=1)

    def _show_algo_help(self, algo_num):
        if self._algo_help_visible_num == algo_num:
            self._hide_algo_help()
            return
        lines_text = ALGO_HELPS.get(algo_num, [])
        title = ALGO_TITLES.get(algo_num, f"Алгоритм {algo_num}")
        all_lines = [title, "=" * 50, ""] + lines_text
        txt = '\n'.join(all_lines)
        if self._algo_help_obj is not None:
            try:
                self._algo_help_obj.remove()
            except Exception:
                pass
        self._algo_help_obj = self.fig.text(
            0.25, 0.50, txt,
            fontsize=9, va='center', ha='left', family='monospace',
            bbox=dict(boxstyle='round,pad=0.5', fc='lightyellow',
                      alpha=0.95, edgecolor='orange', lw=1.5),
            zorder=100
        )
        self._algo_help_visible_num = algo_num
        self.fig.canvas.draw_idle()

    def _hide_algo_help(self):
        if self._algo_help_obj is not None:
            try:
                self._algo_help_obj.remove()
            except Exception:
                pass
            self._algo_help_obj = None
        self._algo_help_visible_num = None
        self.fig.canvas.draw_idle()

    # ---------- оверлей GPS-скорости (v0.53) ----------

    def _on_speed_overlay(self, which):
        if which == 'h5':
            self.speed_overlay_h5 = not self.speed_overlay_h5
        elif which == 'h4':
            self.speed_overlay_h4 = not self.speed_overlay_h4
        else:
            return

        self._update_speed_overlay_buttons()
        self._draw_speed_overlay()
        self._draw_speed_labels()
        self._draw_speed_extremes()

    def _on_toggle_speed_labels(self):
        self.speed_labels_on = not self.speed_labels_on
        self._update_speed_overlay_buttons()
        self._draw_speed_labels()
        self._draw_speed_extremes()

    def _update_speed_overlay_buttons(self):
        states = [
            ('h5',     self.speed_overlay_h5, '#ffddaa', '#ffcc88'),
            ('h4',     self.speed_overlay_h4, '#ffccee', '#ffaadd'),
            ('labels', self.speed_labels_on,  '#ffffcc', '#ffff88'),
        ]
        for which, active, on_color, on_hover in states:
            w = getattr(self, f'_btn_mid_{which}', None)
            if w is None:
                continue
            if active:
                w.color = on_color
                w.hovercolor = on_hover
            else:
                w.color = '0.85'
                w.hovercolor = '0.95'
            w.ax.set_facecolor(w.color)
            w.ax.figure.canvas.draw_idle()

    def _draw_speed_overlay(self):
        for ln in self._speed_lines:
            try:
                ln.remove()
            except Exception:
                pass
        self._speed_lines = []

        if not (self.speed_overlay_h5 or self.speed_overlay_h4):
            self.fig.canvas.draw_idle()
            return

        if not self.gps_points or len(self.gps_points) < 2:
            self.fig.canvas.draw_idle()
            return

        if self.h5 is None or self.h4 is None:
            self.fig.canvas.draw_idle()
            return

        try:
            V_ref = self._calc_speed(self.h5, self.h4)[3]
        except Exception:
            V_ref = 0.0

        if V_ref <= 1.0:
            self.fig.canvas.draw_idle()
            return

        k5 = self.h5 / V_ref
        k4 = self.h4 / V_ref

        fps = self.cfg['target_fps']
        gps_t = np.array([p[0] for p in self.gps_points], dtype=float)
        gps_v = np.array([p[1] for p in self.gps_points], dtype=float)

        if len(gps_t) >= 2:
            dt_med = float(np.median(np.diff(gps_t)))
            if dt_med <= 0:
                dt_med = 1.0
        else:
            dt_med = 1.0
        win_n = max(1, int(round(2.0 / dt_med)))
        if win_n > 1 and win_n < len(gps_v):
            kernel = np.ones(win_n, dtype=float) / win_n
            gps_v_sm = np.convolve(gps_v, kernel, mode='same')
        else:
            gps_v_sm = gps_v

        lag_used = self.gps_lag
        _override = self.cfg.get('gps_lag_override')
        if _override is not None:
            try:
                lag_used = float(_override)
            except (ValueError, TypeError):
                pass
        gps_frames = (gps_t - lag_used) * fps

        if self.speed_overlay_h5:
            h5_curve = gps_v_sm * k5
            ln, = self.ax_spec.plot(
                gps_frames, h5_curve,
                color='#ff00cc', lw=2.2, alpha=0.95,
                zorder=10, solid_capstyle='round'
            )
            ln.set_path_effects([
                pe.Stroke(linewidth=4.5, foreground='black'),
                pe.Normal()
            ])
            self._speed_lines.append(ln)

        if self.speed_overlay_h4:
            h4_curve = gps_v_sm * k4
            ln, = self.ax_spec.plot(
                gps_frames, h4_curve,
                color='#00ccff', lw=2.2, alpha=0.95,
                zorder=10, solid_capstyle='round'
            )
            ln.set_path_effects([
                pe.Stroke(linewidth=4.5, foreground='black'),
                pe.Normal()
            ])
            self._speed_lines.append(ln)

        self.fig.canvas.draw_idle()

    def _draw_speed_labels(self):
        for obj in self._speed_label_objs:
            try:
                obj.remove()
            except Exception:
                pass
        self._speed_label_objs = []

        if not self.speed_labels_on:
            self.fig.canvas.draw_idle()
            return

        x0_view, x1_view = self.ax_spec.get_xlim()
        if x1_view < x0_view:
            x0_view, x1_view = x1_view, x0_view

        def _add_label(f_x, f_y, v_val, color, is_max):
            txt_str = f'{v_val:.1f}'
            if is_max:
                txt = self.ax_spec.text(
                    f_x, f_y, txt_str,
                    fontsize=8, color=color,
                    ha='center', va='bottom',
                    fontweight='bold', zorder=15
                )
            else:
                txt = self.ax_spec.text(
                    f_x, f_y, txt_str,
                    fontsize=8, color=color,
                    ha='center', va='top',
                    fontweight='bold', zorder=15
                )
            txt.set_path_effects([
                pe.Stroke(linewidth=2.5, foreground='black'),
                pe.Normal()
            ])
            self._speed_label_objs.append(txt)

        if (self.speed_overlay_h5 or self.speed_overlay_h4) \
                and self.gps_points and len(self.gps_points) >= 3 \
                and self.h5 is not None and self.h4 is not None:
            gps_t = np.array([p[0] for p in self.gps_points], dtype=float)
            gps_v = np.array([p[1] for p in self.gps_points], dtype=float)

            if len(gps_t) >= 2:
                dt_med = float(np.median(np.diff(gps_t)))
                if dt_med <= 0:
                    dt_med = 1.0
            else:
                dt_med = 1.0

            win_n = max(1, int(round(2.0 / dt_med)))
            if win_n > 1 and win_n < len(gps_v):
                kernel = np.ones(win_n, dtype=float) / win_n
                gps_v_sm = np.convolve(gps_v, kernel, mode='same')
            else:
                gps_v_sm = gps_v

            try:
                V_ref = self._calc_speed(self.h5, self.h4)[3]
            except Exception:
                V_ref = 0.0

            if V_ref > 1.0:
                k5 = self.h5 / V_ref
                k4 = self.h4 / V_ref

                fps = self.cfg['target_fps']

                lag_used = self.gps_lag
                _override = self.cfg.get('gps_lag_override')
                if _override is not None:
                    try:
                        lag_used = float(_override)
                    except (ValueError, TypeError):
                        pass
                gps_frames = (gps_t - lag_used) * fps

                vis = (gps_frames >= x0_view) & (gps_frames <= x1_view)
                if vis.sum() >= 3:
                    gf_vis = gps_frames[vis]
                    gv_vis = gps_v_sm[vis]

                    min_dist = max(2, int(round(3.0 / dt_med)))
                    peaks_idx = _find_peaks_simple(gv_vis,
                                                   min_distance=min_dist)
                    mins_idx = _find_extrema_simple(gv_vis,
                                                    min_distance=min_dist,
                                                    invert=True)

                    if len(peaks_idx) > 20:
                        peaks_idx = sorted(peaks_idx,
                                           key=lambda i: -gv_vis[i])[:20]
                        peaks_idx.sort()
                    if len(mins_idx) > 20:
                        mins_idx = sorted(mins_idx,
                                          key=lambda i: gv_vis[i])[:20]
                        mins_idx.sort()

                    if self.speed_overlay_h5:
                        for idx in peaks_idx:
                            v_val = float(gv_vis[idx])
                            if v_val < 5.0:
                                continue
                            _add_label(float(gf_vis[idx]),
                                       v_val * k5,
                                       v_val, '#ff00cc', True)
                        for idx in mins_idx:
                            v_val = float(gv_vis[idx])
                            if v_val < 5.0:
                                continue
                            _add_label(float(gf_vis[idx]),
                                       v_val * k5,
                                       v_val, '#ff00cc', False)

                    if self.speed_overlay_h4:
                        for idx in peaks_idx:
                            v_val = float(gv_vis[idx])
                            if v_val < 5.0:
                                continue
                            _add_label(float(gf_vis[idx]),
                                       v_val * k4,
                                       v_val, '#00ccff', True)
                        for idx in mins_idx:
                            v_val = float(gv_vis[idx])
                            if v_val < 5.0:
                                continue
                            _add_label(float(gf_vis[idx]),
                                       v_val * k4,
                                       v_val, '#00ccff', False)

        if self.tube_t and self.tube_h5_centers and self.tube_h4_centers:
            t_arr = np.array(self.tube_t, dtype=float)
            h5_c_arr = np.array(self.tube_h5_centers, dtype=float)
            h4_c_arr = np.array(self.tube_h4_centers, dtype=float)

            v_audio = []
            for h5c, h4c in zip(h5_c_arr, h4_c_arr):
                try:
                    _, _, _, v = self._calc_speed(h5c, h4c)
                except Exception:
                    v = 0.0
                v_audio.append(v)
            v_audio = np.array(v_audio, dtype=float)

            if len(t_arr) >= 2:
                dt_step = float(np.median(np.diff(t_arr)))
            else:
                dt_step = 0.1
            if dt_step <= 0:
                dt_step = 0.1

            vis2 = (t_arr >= x0_view) & (t_arr <= x1_view)
            if vis2.sum() >= 3:
                t_vis = t_arr[vis2]
                h5_vis = h5_c_arr[vis2]
                h4_vis = h4_c_arr[vis2]
                v_vis = v_audio[vis2]

                min_dist2 = max(1, int(round(3.0 / dt_step)))
                peaks_idx2 = _find_peaks_simple(v_vis,
                                                min_distance=min_dist2)
                mins_idx2 = _find_extrema_simple(v_vis,
                                                 min_distance=min_dist2,
                                                 invert=True)

                if len(peaks_idx2) > 20:
                    peaks_idx2 = sorted(peaks_idx2,
                                        key=lambda i: -v_vis[i])[:20]
                    peaks_idx2.sort()
                if len(mins_idx2) > 20:
                    mins_idx2 = sorted(mins_idx2,
                                       key=lambda i: v_vis[i])[:20]
                    mins_idx2.sort()

                for idx in peaks_idx2:
                    v_val = float(v_vis[idx])
                    if v_val < 5.0:
                        continue
                    _add_label(float(t_vis[idx]), float(h5_vis[idx]),
                               v_val, '#ffff00', True)
                    _add_label(float(t_vis[idx]), float(h4_vis[idx]),
                               v_val, '#00ff00', True)

                for idx in mins_idx2:
                    v_val = float(v_vis[idx])
                    if v_val < 5.0:
                        continue
                    _add_label(float(t_vis[idx]), float(h5_vis[idx]),
                               v_val, '#ffff00', False)
                    _add_label(float(t_vis[idx]), float(h4_vis[idx]),
                               v_val, '#00ff00', False)

        self.fig.canvas.draw_idle()

    def _draw_speed_extremes(self):
        """Отмечает Vmax и Vmin в видимом диапазоне времени для каждого
        активного источника.

        Показываются ТОЛЬКО когда подписи пиков (speed_labels_on)
        выключены. Если включён режим «Подп.V», локальные максимумы и
        минимумы уже подписаны в _draw_speed_labels — и глобальные
        Vmax/Vmin дублировать не нужно. Подпись — только число (%.1f).
        """
        for obj in getattr(self, '_extreme_objs', []):
            try:
                obj.remove()
            except Exception:
                pass
        self._extreme_objs = []

        # Если включён режим подписей локальных пиков — глобальные
        # Vmax/Vmin не рисуем: они уже покрыты подписями.
        if self.speed_labels_on:
            self.fig.canvas.draw_idle()
            return

        has_audio = bool(self.tube_t and self.tube_h5_centers
                         and self.tube_h4_centers)
        has_gps = bool((self.speed_overlay_h5 or self.speed_overlay_h4)
                       and self.gps_points and len(self.gps_points) >= 3
                       and self.h5 is not None and self.h4 is not None)
        if not has_audio and not has_gps:
            self.fig.canvas.draw_idle()
            return

        x0_view, x1_view = self.ax_spec.get_xlim()
        if x1_view < x0_view:
            x0_view, x1_view = x1_view, x0_view

        def _add_marker(f_x, f_y, v_val, color, is_max):
            marker, = self.ax_spec.plot(
                [f_x], [f_y],
                marker='D' if is_max else 'v',
                color=color, markersize=7,
                markeredgecolor='black', markeredgewidth=1.0,
                linestyle='None', zorder=18
            )
            self._extreme_objs.append(marker)
            txt = self.ax_spec.text(
                f_x, f_y, f'{v_val:.1f}',
                fontsize=8, color=color,
                ha='center',
                va='bottom' if is_max else 'top',
                fontweight='bold', zorder=19
            )
            txt.set_path_effects([
                pe.Stroke(linewidth=2.5, foreground='black'),
                pe.Normal()
            ])
            self._extreme_objs.append(txt)

        if has_audio:
            t_arr = np.array(self.tube_t, dtype=float)
            h5_c_arr = np.array(self.tube_h5_centers, dtype=float)
            h4_c_arr = np.array(self.tube_h4_centers, dtype=float)

            v_audio = []
            for h5c, h4c in zip(h5_c_arr, h4_c_arr):
                try:
                    _, _, _, v = self._calc_speed(h5c, h4c)
                except Exception:
                    v = 0.0
                v_audio.append(v)
            v_audio = np.array(v_audio, dtype=float)

            vis = (t_arr >= x0_view) & (t_arr <= x1_view)
            if vis.sum() >= 2:
                t_vis = t_arr[vis]
                h5_vis = h5_c_arr[vis]
                h4_vis = h4_c_arr[vis]
                v_vis = v_audio[vis]

                if len(v_vis) >= 2:
                    v_max_g = float(v_vis.max())
                    v_min_g = float(v_vis.min())

                    if v_max_g >= 5.0:
                        i_max = int(np.argmax(v_vis))
                        _add_marker(float(t_vis[i_max]),
                                    float(h5_vis[i_max]),
                                    v_max_g, '#ffff00', True)
                        _add_marker(float(t_vis[i_max]),
                                    float(h4_vis[i_max]),
                                    v_max_g, '#00ff00', True)

                    if v_min_g >= 5.0:
                        i_min = int(np.argmin(v_vis))
                        _add_marker(float(t_vis[i_min]),
                                    float(h5_vis[i_min]),
                                    v_min_g, '#ffff00', False)
                        _add_marker(float(t_vis[i_min]),
                                    float(h4_vis[i_min]),
                                    v_min_g, '#00ff00', False)

        if has_gps:
            gps_t = np.array([p[0] for p in self.gps_points], dtype=float)
            gps_v = np.array([p[1] for p in self.gps_points], dtype=float)

            try:
                V_ref = self._calc_speed(self.h5, self.h4)[3]
            except Exception:
                V_ref = 0.0

            if V_ref > 1.0:
                k5 = self.h5 / V_ref
                k4 = self.h4 / V_ref

                fps = self.cfg['target_fps']
                lag_used = self.gps_lag
                _override = self.cfg.get('gps_lag_override')
                if _override is not None:
                    try:
                        lag_used = float(_override)
                    except (ValueError, TypeError):
                        pass
                gps_frames = (gps_t - lag_used) * fps

                vis = (gps_frames >= x0_view) & (gps_frames <= x1_view)
                if vis.sum() >= 2:
                    gf_vis = gps_frames[vis]
                    gv_vis = gps_v[vis]

                    v_max_g = float(gv_vis.max())
                    v_min_g = float(gv_vis.min())

                    if v_max_g >= 5.0:
                        i_max = int(np.argmax(gv_vis))
                        if self.speed_overlay_h5:
                            _add_marker(float(gf_vis[i_max]),
                                        v_max_g * k5,
                                        v_max_g, '#ff00cc', True)
                        if self.speed_overlay_h4:
                            _add_marker(float(gf_vis[i_max]),
                                        v_max_g * k4,
                                        v_max_g, '#00ccff', True)

                    if v_min_g >= 5.0:
                        i_min = int(np.argmin(gv_vis))
                        if self.speed_overlay_h5:
                            _add_marker(float(gf_vis[i_min]),
                                        v_min_g * k5,
                                        v_min_g, '#ff00cc', False)
                        if self.speed_overlay_h4:
                            _add_marker(float(gf_vis[i_min]),
                                        v_min_g * k4,
                                        v_min_g, '#00ccff', False)

        self.fig.canvas.draw_idle()

    def _btn_reset_time(self, _=None):
        if self._last_reset_action == 'time':
            self._last_reset_action = None
            self._step_back()
            return
        self._last_reset_action = 'time'
        self._push_history()
        self.ax_spec.set_xlim(0, self.n_frames)
        self.analyze_start = 0
        self.analyze_dur = self.n_frames
        self._update_time_span()
        self._sync_fields()
        self._redraw_all()

    def _btn_reset_freq(self, _=None):
        if self._last_reset_action == 'freq':
            self._last_reset_action = None
            self._step_back()
            return
        self._last_reset_action = 'freq'
        self._push_history()
        self.ax_spec.set_ylim(20, 600)
        self._sync_fields()
        self._redraw_all()

    def _btn_reset_all(self, _=None):
        if self._last_reset_action == 'all':
            self._last_reset_action = None
            self._step_back()
            return
        self._last_reset_action = 'all'
        self._push_history()
        self._reset_zoom()

    def _btn_exit(self, _=None):
        self._on_exit()

    def _set_phase(self, phase):
        self.phase = phase
        if self.span_selector:
            self.span_selector.set_active(phase == self.PHASE_TIME)
        self.update_status()
        self.fig.canvas.draw_idle()

    def _redraw_all(self):
        if self.harm_freqs:
            self.draw_harmonics()
        self.draw_ranges()
        self.draw_tubes()
        self._draw_speed_overlay()
        self._draw_speed_labels()
        self._draw_speed_extremes()
        self.update_status()
        self.fig.canvas.draw_idle()

    def _frame_to_sec(self, frame):
        return frame / self.cfg['target_fps']

    def _sec_to_frame(self, sec):
        return int(round(sec * self.cfg['target_fps']))

    def _on_motion(self, event):
        if event.xdata is None or event.ydata is None \
                or event.inaxes != self.ax_spec:
            if self.cursor_text:
                self.cursor_text.set_visible(False)
            self.fig.canvas.draw_idle()
            return
        frame = int(event.xdata)
        fps = self.cfg['target_fps']
        t_sec = frame / fps
        freq = event.ydata
        self.cursor_text.set_text(
            f'кадр={frame} | t={t_sec:.3f}с | {freq:.0f} Гц')
        self.cursor_text.set_visible(True)
        if event.button == 1 and self.phase == self.PHASE_TIME \
                and self._drag_start_frame is not None:
            xmin = min(self._drag_start_frame, frame)
            xmax = max(self._drag_start_frame, frame)
            dur_f = xmax - xmin
            t0 = xmin / fps
            t1 = xmax / fps
            dur_s = t1 - t0
            self.drag_text.set_text(
                f'кадры: {xmin}-{xmax} ({dur_f})  |  '
                f'время: {t0:.3f}-{t1:.3f}с ({dur_s:.3f}с)'
            )
            self.drag_text.set_visible(True)
        self.fig.canvas.draw_idle()

    def _set_tb_silent(self, tb_name, value):
        old = self._syncing_fields
        self._syncing_fields = True
        try:
            getattr(self, f'tb_{tb_name}').set_val(value)
        finally:
            self._syncing_fields = old

    def _sync_fields(self):
        if self._syncing_fields:
            return
        self._syncing_fields = True
        try:
            xlim = self.ax_spec.get_xlim()
            ylim = self.ax_spec.get_ylim()
            fps = self.cfg['target_fps']

            self.tb_x_start.set_val(f"{xlim[0] / fps:.3f}")
            self.tb_x_end.set_val(f"{xlim[1] / fps:.3f}")
            self.tb_dx.set_val(f"{(xlim[1] - xlim[0]) / fps:.3f}")
            self.tb_y_min.set_val(f"{int(ylim[0])}")
            self.tb_y_max.set_val(f"{int(ylim[1])}")

            self.sl_fmin.set_val(ylim[0])
            self.sl_fmax.set_val(ylim[1])

            self.tb_width.set_val(int(self.sl_width.val))
            self.tb_step.set_val(f"{float(self.sl_step.val):.3f}")
            self.tb_color.set_val(int(self.sl_color.val))
            self.tb_peak.set_val(float(self.sl_peak.val))
        finally:
            self._syncing_fields = False

    def _on_field_x_start(self, text):
        if self._syncing_fields:
            return
        try:
            val = float(text)
            fps = self.cfg['target_fps']
            frame = self._sec_to_frame(val)
            xlim = self.ax_spec.get_xlim()
            dur = xlim[1] - xlim[0]
            self._push_history()
            self.ax_spec.set_xlim(frame, frame + dur)
            self.analyze_start = max(0, frame)
            self.analyze_dur = int(round(dur))
            self._update_time_span()
            self._sync_fields()
            self._redraw_all()
        except ValueError:
            self._sync_fields()

    def _on_field_x_end(self, text):
        if self._syncing_fields:
            return
        try:
            val = float(text)
            fps = self.cfg['target_fps']
            frame_end = self._sec_to_frame(val)
            xlim = self.ax_spec.get_xlim()
            self._push_history()
            self.ax_spec.set_xlim(xlim[0], frame_end)
            self.analyze_start = max(0, int(xlim[0]))
            self.analyze_dur = max(1, int(round(frame_end - xlim[0])))
            self._update_time_span()
            self._sync_fields()
            self._redraw_all()
        except ValueError:
            self._sync_fields()

    def _on_field_dx(self, text):
        if self._syncing_fields:
            return
        try:
            dx_sec = float(text)
            if dx_sec <= 0:
                self._sync_fields()
                return
            fps = self.cfg['target_fps']
            xlim = self.ax_spec.get_xlim()
            x_start = xlim[0]
            x_end = x_start + dx_sec * fps
            self._push_history()
            self.ax_spec.set_xlim(x_start, x_end)
            self.analyze_start = max(0, int(round(x_start)))
            self.analyze_dur = max(1, int(round(x_end - x_start)))
            self._update_time_span()
            self._sync_fields()
            self._redraw_all()
        except ValueError:
            self._sync_fields()

    def _on_field_y_min(self, text):
        if self._syncing_fields:
            return
        try:
            val = float(text)
            ylim = self.ax_spec.get_ylim()
            if val < ylim[1]:
                self.cfg['freq_min'] = val
                self.sl_fmin.set_val(val)
                self.ax_spec.set_ylim(val, ylim[1])
                self._sync_fields()
                if self.harm_freqs:
                    self.draw_harmonics()
                self.draw_ranges()
                self.draw_tubes()
                self.fig.canvas.draw_idle()
        except ValueError:
            self._sync_fields()

    def _on_field_y_max(self, text):
        if self._syncing_fields:
            return
        try:
            val = float(text)
            ylim = self.ax_spec.get_ylim()
            if val > ylim[0]:
                self.cfg['freq_max'] = val
                self.sl_fmax.set_val(val)
                self.ax_spec.set_ylim(ylim[0], val)
                self._sync_fields()
                if self.harm_freqs:
                    self.draw_harmonics()
                self.draw_ranges()
                self.draw_tubes()
                self.fig.canvas.draw_idle()
        except ValueError:
            self._sync_fields()

    def _on_field_width(self, text):
        if self._syncing_fields:
            return
        try:
            val = int(float(text))
            if 1 <= val <= 20:
                self.line_width = val
                self.cfg['harmonic_line_width'] = val
                self.sl_width.set_val(val)
                self._set_tb_silent('width', f'{val}')
                if self.harm_freqs:
                    self.draw_harmonics()
                    self.fig.canvas.draw_idle()
        except ValueError:
            self._sync_fields()

    def _on_field_step(self, text):
        if self._syncing_fields:
            return
        try:
            val = float(text)
            if 0.01 <= val <= 5.0:
                self.cfg['time_step'] = val
                self.sl_step.set_val(val)
                self._set_tb_silent('step', f'{val:.3f}')
        except ValueError:
            self._sync_fields()

    def _on_field_color(self, text):
        if self._syncing_fields:
            return
        try:
            val = int(float(text))
            if 1 <= val <= 255:
                self.color_steps = val
                self.cfg['color_steps'] = val
                self.sl_color.set_val(val)
                self._set_tb_silent('color', f'{val}')
        except ValueError:
            self._sync_fields()

    def _on_field_peak(self, text):
        if self._syncing_fields:
            return
        try:
            val = float(text)
            if 1 <= val <= 30:
                self.peak_search_hz = val
                self.cfg['peak_search_hz'] = val
                self.sl_peak.set_val(val)
                self._set_tb_silent('peak', f'{int(val)}')
        except ValueError:
            self._sync_fields()

    def _update_time_span(self):
        if self.time_span:
            try:
                self.time_span.remove()
            except Exception:
                pass
        self.time_span = self.ax_spec.axvspan(
            self.analyze_start, self.analyze_start + self.analyze_dur,
            color='red', alpha=0.15, zorder=5
        )

    def _push_history(self, xlim=None, ylim=None,
                      analyze_start=None, analyze_dur=None):
        if xlim is None:
            xlim = self.ax_spec.get_xlim()
        if ylim is None:
            ylim = self.ax_spec.get_ylim()
        if analyze_start is None:
            analyze_start = self.analyze_start
        if analyze_dur is None:
            analyze_dur = self.analyze_dur

        entry = {
            'view_x_start': float(xlim[0]),
            'view_x_end':   float(xlim[1]),
            'view_y_start': float(ylim[0]),
            'view_y_end':   float(ylim[1]),
            'analyze_start': int(analyze_start),
            'analyze_dur':   int(analyze_dur),
            'h5': float(self.h5) if self.h5 is not None else None,
            'h4': float(self.h4) if self.h4 is not None else None,
            'algorithm': int(self.last_algo) if self.last_algo is not None else 0,
            'time_step':      float(self.cfg['time_step']),
            'color_steps':    int(self.color_steps),
            'peak_search_hz': float(self.peak_search_hz),
            'line_width':     int(self.line_width),
            'overlay_mode':   int(self.overlay_mode),
        }
        if self.zoom_history and self.zoom_history[-1] == entry:
            return
        if self.zoom_pointer < len(self.zoom_history) - 1:
            self.zoom_history = self.zoom_history[:self.zoom_pointer + 1]
        self.zoom_history.append(entry)
        while len(self.zoom_history) > 50:
            self.zoom_history.pop(0)
        self.zoom_pointer = len(self.zoom_history) - 1

    def _apply_history_entry(self, entry):
        """Восстанавливает состояние из записи истории и пересчитывает
        результаты для нового диапазона.

        Параметры (Шаг, Цвет, Пик, Ширина, H5, H4, алгоритм, overlay)
        восстанавливаются из entry. Если есть H5, H4 и гармоники —
        _on_run запускается заново, чтобы Vср/Vgps ср/Δgps и подписи
        соответствовали новому диапазону.
        """
        # --- 1. Оси ---
        self.ax_spec.set_xlim(entry['view_x_start'], entry['view_x_end'])
        self.ax_spec.set_ylim(entry['view_y_start'], entry['view_y_end'])

        # --- 2. Диапазон анализа ---
        self.analyze_start = int(entry.get('analyze_start', 0))
        self.analyze_dur = int(entry.get('analyze_dur', self.n_frames))
        if self.analyze_dur <= 0:
            self.analyze_dur = max(1, self.n_frames - self.analyze_start)

        # --- 3. Слайдеры и поля ---
        ts = entry.get('time_step')
        if ts is not None:
            self.cfg['time_step'] = float(ts)
            self.sl_step.set_val(float(ts))

        cs = entry.get('color_steps')
        if cs is not None:
            self.color_steps = int(cs)
            self.cfg['color_steps'] = int(cs)
            self.sl_color.set_val(int(cs))

        pk = entry.get('peak_search_hz')
        if pk is not None:
            self.peak_search_hz = float(pk)
            self.cfg['peak_search_hz'] = float(pk)
            self.sl_peak.set_val(float(pk))

        lw = entry.get('line_width')
        if lw is not None:
            self.line_width = int(lw)
            self.cfg['harmonic_line_width'] = int(lw)
            self.sl_width.set_val(int(lw))

        om = entry.get('overlay_mode')
        if om is not None:
            self.overlay_mode = int(om)
            self.cfg['overlay_mode'] = self.overlay_mode
            self._update_overlay_buttons()

        # --- 4. H5 / H4 ---
        new_h5 = entry.get('h5')
        new_h4 = entry.get('h4')
        h_changed = False

        if new_h5 is not None:
            if abs(new_h5 - (self.h5 if self.h5 is not None else 0.0)) > 1e-9:
                h_changed = True
            self.h5 = float(new_h5)
            self.cfg['harmonic_5_freq'] = float(new_h5)

        if new_h4 is not None:
            if abs(new_h4 - (self.h4 if self.h4 is not None else 0.0)) > 1e-9:
                h_changed = True
            self.h4 = float(new_h4)
            self.cfg['harmonic_4_freq'] = float(new_h4)

        # --- 5. Алгоритм ---
        new_algo = entry.get('algorithm')
        if new_algo is not None:
            self.last_algo = int(new_algo)
            self.cfg['algorithm'] = int(new_algo)

        if h_changed:
            self.calc_harmonics()

        # --- 6. Обновление визуальных слоёв ---
        self._update_time_span()
        self._sync_fields()

        # --- 7. Пересчёт для нового диапазона ---
        # Запускаем всегда, когда есть всё необходимое: гармоники,
        # H5/H4, валидный диапазон и корректный номер алгоритма.
        # Раньше здесь проверялся флаг was_run = self._algo_run_done —
        # из-за него пересчёт мог молча пропускаться.
        can_recalc = (
            not self._recalc_in_progress
            and bool(self.harm_freqs)
            and self.h5 is not None and self.h4 is not None
            and self.analyze_dur > 0
            and self.last_algo in (1, 2, 3, 4, 5, 6)
        )

        if can_recalc:
            self._recalc_in_progress = True
            try:
                self._on_run(None, self.last_algo)
                return
            except Exception as e:
                print(f"Не удалось пересчитать для нового диапазона: {e}",
                      file=sys.stderr)
                self._algo_run_done = False
                self.gps_v_avg = None
            finally:
                self._recalc_in_progress = False

        # Если пересчёт не удался или не требуется — очищаем старые
        # данные, чтобы в статусе не остались числа от прошлого окна,
        # и перерисовываем.
        self._algo_run_done = False
        self.gps_v_avg = None
        self._redraw_all()

    def _step_back(self, event=None):
        if self.zoom_pointer <= 0:
            self._reset_zoom()
            return
        self.zoom_pointer -= 1
        self._apply_history_entry(self.zoom_history[self.zoom_pointer])

    def _step_forward(self, event=None):
        if self.zoom_pointer >= len(self.zoom_history) - 1:
            return
        self.zoom_pointer += 1
        self._apply_history_entry(self.zoom_history[self.zoom_pointer])

    def _btn_back(self, event=None):
        self._step_back(event)

    def _btn_save_pos(self, _=None):
        self._push_history()
        print("Текущее положение сохранено в историю.", file=sys.stderr)
        self.update_status()

    def _btn_delete_pos(self, _=None):
        if not self.zoom_history:
            return

        pos = self.zoom_pointer
        if pos < 0 or pos >= len(self.zoom_history):
            pos = len(self.zoom_history) - 1

        self.zoom_history.pop(pos)

        if not self.zoom_history:
            self.zoom_pointer = -1
            self._reset_zoom()
            self.update_status()
            return

        self.zoom_pointer = max(0, pos - 1)

        self._apply_history_entry(self.zoom_history[self.zoom_pointer])
        self.update_status()

    def _on_close(self, event):
        if getattr(self, '_closing', False):
            return
        self._closing = True
        self._save_view()
        save_config(self.cfg, self.config_path)

    def _on_exit(self, _=None):
        if getattr(self, '_closing', False):
            return
        self._closing = True
        self._save_view()
        save_config(self.cfg, self.config_path)
        plt.close(self.fig)

    def _on_span(self, xmin, xmax):
        self.drag_text.set_visible(False)
        self._push_history()
        x0 = max(0, int(round(min(xmin, xmax))))
        x1 = max(0, int(round(max(xmin, xmax))))
        self.analyze_start = x0
        self.analyze_dur = max(1, x1 - x0)
        self._update_time_span()
        self.ax_spec.set_xlim(x0, x0 + self.analyze_dur)
        fps = self.cfg['target_fps']
        t0 = x0 / fps
        t1 = x1 / fps
        print(f"Диапазон: кадры {x0}-{x1} ({self.analyze_dur} кадров) | "
              f"время: {t0:.1f}-{t1:.1f}с ({(x1 - x0)/fps:.1f}с)")
        self._sync_fields()
        self._redraw_all()

    def _reset_zoom(self, _=None):
        self.drag_text.set_visible(False)
        self._updating_axes = True
        try:
            self.zoom_history.clear()
            self.zoom_pointer = -1
            self.ax_spec.set_xlim(0, self.n_frames)
            self.ax_spec.set_ylim(self.cfg['freq_min'], self.cfg['freq_max'])
            self.analyze_start = 0
            self.analyze_dur = self.n_frames
        finally:
            self._updating_axes = False
        self._update_time_span()
        self._sync_fields()
        self._redraw_all()

    def _on_freq(self, _=None):
        if self._syncing_fields or self._updating_axes:
            return
        fmin = self.sl_fmin.val
        fmax = self.sl_fmax.val
        if fmax <= fmin + 1:
            fmax = fmin + 1
            self.sl_fmax.set_val(fmax)

        self._updating_axes = True
        try:
            self.cfg['freq_min'] = fmin
            self.cfg['freq_max'] = fmax
            self.ax_spec.set_ylim(fmin, fmax)

            spec_title = ('Спектрограмма' if self.spec_mode == 'time'
                          else 'Power Spectrum')
            self.ax_spec.set_title(
                f'{spec_title} ({int(fmin)}-{int(fmax)} Гц)  |  '
                'drag = выбор/масштаб  |  клик = гармоники  |  '
                'Z = сброс  |  P = PNG  |  Q = выход  |  ? = помощь',
                fontsize=10
            )
            if self.harm_freqs:
                self.draw_harmonics()
            self.draw_ranges()
            self.draw_tubes()
        finally:
            self._updating_axes = False
        self._sync_fields()
        self.fig.canvas.draw_idle()

    def _on_width(self, _=None):
        self.line_width = int(self.sl_width.val)
        self.cfg['harmonic_line_width'] = self.line_width
        if not self._syncing_fields:
            self._set_tb_silent('width', f'{self.line_width}')
        if self.harm_freqs:
            self.draw_harmonics()

    def _on_step(self, _=None):
        self.cfg['time_step'] = float(self.sl_step.val)
        if not self._syncing_fields:
            self._set_tb_silent('step', f"{self.cfg['time_step']:.3f}")

    def _on_color(self, _=None):
        self.color_steps = int(self.sl_color.val)
        self.cfg['color_steps'] = self.color_steps
        if not self._syncing_fields:
            self._set_tb_silent('color', f'{self.color_steps}')

    def _on_peak(self, _=None):
        self.peak_search_hz = float(self.sl_peak.val)
        self.cfg['peak_search_hz'] = self.peak_search_hz
        if not self._syncing_fields:
            self._set_tb_silent('peak', f'{int(self.peak_search_hz)}')

    def _on_key(self, event):
        key = event.key
        if key == 'tab':
            self._move_focus(1)
        elif key == 'shift+tab':
            self._move_focus(-1)
        elif key in ('?', 'shift+/'):
            self._toggle_help()
        elif key == 'escape':
            self._hide_help()
            self._hide_algo_help()
            self._set_phase(self.PHASE_TIME)
        elif key in ('q', 'Q'):
            self._on_exit()
        elif key in ('s', 'S'):
            self._save_view()
            save_config(self.cfg, self.config_path)
            self._save_png()
        elif key == 'z':
            self._step_back()
        elif key in ('Z', 'shift+z'):
            self._btn_reset_all()
        elif key in ('p', 'P'):
            self._toolbar_save_png()
        elif key in ('1', '2', '3', '4', '5', '6'):
            tb_focused = False
            for tb in self._tb_list:
                if hasattr(self.fig.canvas, 'widgetlock') and \
                        not self.fig.canvas.widgetlock.available(tb):
                    tb_focused = True
                    break
            if not tb_focused:
                self._on_run(None, int(key))

    def _collect_sysinfo_lines(self):
        sep = "=" * 70
        sections = []
        try:
            comp = get_computer_info()
            sections.append(("  СВЕДЕНИЯ О КОМПЬЮТЕРЕ", [
                f"  Имя:             {comp['name']}",
                f"  ОС:              {comp['os']}",
                f"  Версия ОС:       {comp['os_version']}",
                f"  Архитектура:     {comp['arch']}",
                f"  Процессор:       {comp['processor']}",
                f"  Пользователь:    {comp['user']}",
                f"  Python:          {comp['python']}",
                f"  CPU S/N:         {comp['cpu_serial']}",
            ]))
        except Exception as e:
            sections.append(("  СВЕДЕНИЯ О КОМПЬЮТЕРЕ", [f"  Ошибка: {e}"]))
        try:
            disk = get_disk_info(self.filepath)
            total_gb = disk['total_bytes'] / 1e9 if disk['total_bytes'] else 0
            free_gb = disk['free_bytes'] / 1e9 if disk['free_bytes'] else 0
            sections.append(("  СВЕДЕНИЯ О ДИСКЕ", [
                f"  Диск:            {disk['drive']}",
                f"  ФС:              {disk['fs_type']}",
                f"  Метка тома:      {disk['volume_label']}",
                f"  S/N тома:        {disk['volume_serial']}",
                f"  Модель диска:    {disk['disk_model']}",
                f"  S/N диска:       {disk['disk_serial']}",
                f"  Всего:           {total_gb:.1f} ГБ",
                f"  Свободно:        {free_gb:.1f} ГБ",
            ]))
        except Exception as e:
            sections.append(("  СВЕДЕНИЯ О ДИСКЕ", [f"  Ошибка: {e}"]))
        try:
            scr = get_script_info(VERSION, os.path.abspath(__file__))
            sections.append(("  СВЕДЕНИЯ О СКРИПТЕ", [
                f"  Имя:             {scr['name']}",
                f"  Путь:            {scr['path']}",
                f"  Размер:          {scr['size']} байт",
                f"  mtime:           {scr['mtime']}",
                f"  MD5:             {scr['md5']}",
                f"  Версия:          {scr['version']}",
            ]))
        except Exception as e:
            sections.append(("  СВЕДЕНИЯ О СКРИПТЕ", [f"  Ошибка: {e}"]))
        return sep, sections

    def _save_view(self):
        xlim = self.ax_spec.get_xlim()
        ylim = self.ax_spec.get_ylim()
        self.cfg['view_x_start'] = float(xlim[0])
        self.cfg['view_x_end']   = float(xlim[1])
        self.cfg['view_y_start'] = float(ylim[0])
        self.cfg['view_y_end']   = float(ylim[1])
        self.cfg['spec_mode']    = self.spec_mode
        self.cfg['peak_search_hz'] = self.peak_search_hz
        self.cfg['overlay_mode'] = self.overlay_mode
        self.cfg['analyze_start'] = int(self.analyze_start)
        self.cfg['analyze_dur']   = int(self.analyze_dur)

        self.cfg['view_history'] = [
            {
                'view_x_start': float(e['view_x_start']),
                'view_x_end':   float(e['view_x_end']),
                'view_y_start': float(e['view_y_start']),
                'view_y_end':   float(e['view_y_end']),
                'analyze_start': int(e.get('analyze_start', 0)),
                'analyze_dur':   int(e.get('analyze_dur', self.n_frames)),
                'h5': e.get('h5'),
                'h4': e.get('h4'),
                'algorithm': int(e.get('algorithm', 0)),
                'time_step':      float(e.get(
                    'time_step', self.cfg['time_step'])),
                'color_steps':    int(e.get(
                    'color_steps', self.color_steps)),
                'peak_search_hz': float(e.get(
                    'peak_search_hz', self.peak_search_hz)),
                'line_width':     int(e.get(
                    'line_width', self.line_width)),
                'overlay_mode':   int(e.get(
                    'overlay_mode', self.overlay_mode)),
            }
            for e in self.zoom_history[-50:]
            if isinstance(e, dict) and 'view_x_start' in e
        ]
        self._save_window_geometry()

    def _restore_view(self):
        cfg = self.cfg
        try:
            if (cfg.get('view_x_start') is not None
                    and cfg.get('view_x_end') is not None
                    and cfg.get('view_y_start') is not None
                    and cfg.get('view_y_end') is not None):
                self.ax_spec.set_xlim(cfg['view_x_start'], cfg['view_x_end'])
                self.ax_spec.set_ylim(cfg['view_y_start'], cfg['view_y_end'])
        except Exception:
            pass

        try:
            if cfg.get('analyze_dur'):
                self.analyze_start = int(cfg.get('analyze_start', 0))
                self.analyze_dur   = int(cfg['analyze_dur'])
        except (ValueError, TypeError):
            pass

        self.zoom_history.clear()
        for entry in cfg.get('view_history', []):
            if isinstance(entry, dict) and 'view_x_start' in entry \
                    and 'view_y_start' in entry:
                try:
                    self.zoom_history.append({
                        'view_x_start': float(entry['view_x_start']),
                        'view_x_end':   float(entry['view_x_end']),
                        'view_y_start': float(entry['view_y_start']),
                        'view_y_end':   float(entry['view_y_end']),
                        'analyze_start': int(entry.get('analyze_start', 0)),
                        'analyze_dur':   int(entry.get(
                            'analyze_dur', self.n_frames)),
                        'h5': entry.get('h5'),
                        'h4': entry.get('h4'),
                        'algorithm': int(entry.get('algorithm', 0)),
                        'time_step':      float(entry.get(
                            'time_step', self.cfg['time_step'])),
                        'color_steps':    int(entry.get(
                            'color_steps', self.color_steps)),
                        'peak_search_hz': float(entry.get(
                            'peak_search_hz', self.peak_search_hz)),
                        'line_width':     int(entry.get(
                            'line_width', self.line_width)),
                        'overlay_mode':   int(entry.get(
                            'overlay_mode', self.overlay_mode)),
                    })
                except (ValueError, TypeError, KeyError):
                    continue
        while len(self.zoom_history) > 50:
            self.zoom_history.pop(0)
        self.zoom_pointer = len(self.zoom_history) - 1

        self._sync_fields()
        self._update_time_span()
        self.draw_tubes()
        self.fig.canvas.draw_idle()

    def _print_system_info(self):
        if getattr(self, '_sys_info_printed', False):
            return
        self._sys_info_printed = True
        sep, sections = self._collect_sysinfo_lines()
        for title, slines in sections:
            print(sep)
            print(title)
            print(sep)
            for sl in slines:
                print(sl)
        print(sep)

    def _toolbar_save_png(self):
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            base = os.path.splitext(os.path.basename(self.filepath))[0]
            now = datetime.datetime.now()
            default_name = (f"{base}_spec_"
                            f"{now.strftime('%Y%m%d')}_"
                            f"{now.strftime('%H%M%S')}.png")
            png_dir = (self.cfg.get('last_png_dir', '')
                       or os.path.dirname(self.filepath) or '.')
            if not os.path.isdir(png_dir):
                png_dir = os.path.dirname(self.filepath) or '.'
            filepath = filedialog.asksaveasfilename(
                title='Сохранить PNG',
                defaultextension='.png',
                filetypes=[('PNG', '*.png')],
                initialdir=png_dir,
                initialfile=default_name
            )
            root.destroy()
            if filepath:
                self._save_png(custom_path=filepath)
        except Exception:
            self._save_png()

    def _save_png(self, custom_path=None):
        if not hasattr(self, '_png_list'):
            self._png_list = []

        if custom_path:
            png_path = custom_path
            png_dir = os.path.dirname(png_path) or '.'
            png_name = os.path.basename(png_path)
        else:
            png_dir = self.cfg.get('last_png_dir', '')
            if not png_dir or not os.path.isdir(png_dir):
                png_dir = os.path.dirname(self.filepath) or '.'
            base = os.path.splitext(os.path.basename(self.filepath))[0]
            now = datetime.datetime.now()
            png_name = (f"{base}_spec_"
                        f"{now.strftime('%Y%m%d')}_"
                        f"{now.strftime('%H%M%S')}.png")
            png_path = os.path.join(png_dir, png_name)

        if self._footer_text is not None:
            try:
                self._footer_text.remove()
            except Exception:
                pass
            self._footer_text = None

        self._footer_text = add_png_footer(self.fig)

        try:
            self.fig.savefig(png_path, dpi=150, pad_inches=0.3)
        except Exception as e:
            print(f"Ошибка сохранения PNG: {e}")
            if self._footer_text is not None:
                try:
                    self._footer_text.remove()
                except Exception:
                    pass
                self._footer_text = None
            self.fig.canvas.draw_idle()
            return

        if self._footer_text is not None:
            try:
                self._footer_text.remove()
            except Exception:
                pass
            self._footer_text = None
        self.fig.canvas.draw_idle()

        try:
            png_size = os.path.getsize(png_path)
            png_md5 = md5_file(png_path)
            png_mtime = datetime.datetime.fromtimestamp(
                os.path.getmtime(png_path))
            png_date_str = png_mtime.strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            png_size = 0
            png_md5 = '---'
            png_date_str = '---'

        print(f"\nСкриншот сохранён: {png_name}")
        print(f"  Размер:  {png_size} байт")
        print(f"  MD5:     {png_md5}")
        print(f"  Дата:    {png_date_str}")
        print(f"  Папка:   {png_dir}")

        self._png_list.append({
            'name': png_name,
            'path': png_path,
            'size': png_size,
            'md5': png_md5,
            'date': png_date_str,
        })

        self.cfg['last_png_dir'] = png_dir
        save_config(self.cfg, self.config_path)

    def _on_click(self, event):
        if event.xdata is None or event.ydata is None:
            return
        if event.inaxes != self.ax_spec:
            return

        self._drag_start_frame = int(event.xdata)
        frame = int(event.xdata)
        fps = self.cfg['target_fps']
        t_sec = frame / fps

        if self.phase == self.PHASE_TIME:
            self.drag_text.set_text(
                f'кадр={frame} | t={t_sec:.3f}с | {event.ydata:.0f} Гц'
            )
            self.drag_text.set_visible(True)
            self.fig.canvas.draw_idle()
            return

        click_f = event.ydata
        click_frame = event.xdata

        if self.phase == self.PHASE_H5:
            self.h5 = click_f
            self.cfg['harmonic_5_freq'] = click_f
            print(f"5-я гармоника: {click_f:.3f} Гц "
                  f"(кадр={int(click_frame)}, "
                  f"t={click_frame/fps:.1f}с)")
            self._set_phase(self.PHASE_H4)
        elif self.phase == self.PHASE_H4:
            self.h4 = click_f
            self.cfg['harmonic_4_freq'] = click_f
            print(f"4-я гармоника: {click_f:.3f} Гц "
                  f"(кадр={int(click_frame)}, "
                  f"t={click_frame/fps:.1f}с)")
            self.calc_harmonics()
            self.draw_harmonics()
            self._set_phase(self.PHASE_DONE)

    def calc_harmonics(self):
        if not (self.h5 and self.h4):
            return
        self.f0 = (self.h5 / 5 + self.h4 / 4) / 2
        if self.f0 <= 0:
            print("Ошибка: некорректная фундаментальная частота")
            return
        fmax = self.cfg['freq_max']
        self.n_harm = max(5, int(fmax / self.f0))
        self.harm_freqs = [self.f0 * (i + 1) for i in range(self.n_harm)]
        print(f"\nФундаментальная частота: {self.f0:.3f} Гц")
        print(f"Гармоник в диапазоне: {self.n_harm}")
        for i, f in enumerate(self.harm_freqs):
            print(f"  H{i+1}: {f:.3f} Гц")

    def draw_harmonics(self):
        for obj in self.harm_lines + self.harm_bands + self.harm_labels:
            try:
                obj.remove()
            except Exception:
                pass
        self.harm_lines.clear()
        self.harm_bands.clear()
        self.harm_labels.clear()

        if not self.harm_freqs:
            return

        cmap = plt.cm.tab10
        colors = cmap(np.linspace(0, 1, min(self.n_harm, 10)))

        xlim = self.ax_spec.get_xlim()
        label_x = xlim[1] - (xlim[1] - xlim[0]) * 0.02

        for i, freq in enumerate(self.harm_freqs):
            c = colors[i % len(colors)]
            ln = self.ax_spec.axhline(y=freq, color=c, lw=1.5,
                                      ls='--', alpha=0.85)
            self.harm_lines.append(ln)
            w = self.line_width
            for j in range(w, 0, -1):
                alpha = 0.06 * (w - j + 1) / w
                band = self.ax_spec.axhspan(freq - j, freq + j, color=c,
                                            alpha=alpha, zorder=0)
                self.harm_bands.append(band)
            lbl = self.ax_spec.text(
                label_x, freq + 0.5,
                f'H{i+1}={freq:.0f}', fontsize=7, color=c, alpha=0.8,
                fontweight='bold'
            )
            self.harm_labels.append(lbl)

        self.fig.canvas.draw_idle()

    def draw_ranges(self):
        for obj in self.range_lines + self.range_labels:
            try:
                obj.remove()
            except Exception:
                pass
        self.range_lines.clear()
        self.range_labels.clear()

        if self.overlay_mode not in (0, 2):
            self.fig.canvas.draw_idle()
            return

        if (self.h5_min is None and self.h5_max is None
                and self.h4_min is None and self.h4_max is None):
            return

        xlim = self.ax_spec.get_xlim()
        label_x = xlim[1] - (xlim[1] - xlim[0]) * 0.02

        _range_defs = [
            (self.h5_min, 'cyan',   'H5 min'),
            (self.h5_max, 'cyan',   'H5 max'),
            (self.h4_min, 'yellow', 'H4 min'),
            (self.h4_max, 'yellow', 'H4 max'),
        ]
        for val, color, tag in _range_defs:
            if val is None:
                continue
            ln = self.ax_spec.axhline(y=val, color=color, lw=2.0, ls='-',
                                      alpha=0.9, zorder=6)
            self.range_lines.append(ln)
            lbl = self.ax_spec.text(
                label_x, val + 0.5,
                f'{tag}={val:.0f}', fontsize=7, color=color,
                alpha=0.9, fontweight='bold'
            )
            self.range_labels.append(lbl)

        self.fig.canvas.draw_idle()

    def draw_tubes(self):
        for obj in self.tube_collections + self.tube_center_lines:
            try:
                obj.remove()
            except Exception:
                pass
        self.tube_collections.clear()
        self.tube_center_lines.clear()
        for obj in getattr(self, 'overlay_objects', []):
            try:
                obj.remove()
            except Exception:
                pass
        self.overlay_objects = []

        if not self.tube_t or not self.tube_h5_mins:
            return
        if self.overlay_mode == 3:
            return

        t = np.array(self.tube_t, dtype=float)
        h5_lo = np.array(self.tube_h5_mins)
        h5_hi = np.array(self.tube_h5_maxs)
        h4_lo = np.array(self.tube_h4_mins)
        h4_hi = np.array(self.tube_h4_maxs)

        draw_straight = self.overlay_mode in (0, 2)
        draw_actual = self.overlay_mode in (1, 2)

        if draw_straight:
            xlim = self.ax_spec.get_xlim()
            x = [xlim[0], xlim[1]]
            self._draw_tube_band(x, x, x, 'cyan',
                                 self.h5_min, self.h5_max, None, True)
            self._draw_tube_band(x, x, x, 'yellow',
                                 self.h4_min, self.h4_max, None, True)

        if draw_actual:
            self._draw_tube_band(t, h5_lo, h5_hi, 'cyan', None, None,
                                 self.tube_h5_centers, False)
            self._draw_tube_band(t, h4_lo, h4_hi, 'yellow', None, None,
                                 self.tube_h4_centers, False)

        self.fig.canvas.draw_idle()

    def _draw_tube_band(self, x, lo, hi, color, flat_lo, flat_hi,
                        centers, is_flat):
        if is_flat:
            if flat_lo is None or flat_hi is None:
                return
            llo, = self.ax_spec.plot(x, [flat_lo, flat_lo], color=color,
                                     lw=1.5, ls='-', alpha=0.8, zorder=5)
            lhi, = self.ax_spec.plot(x, [flat_hi, flat_hi], color=color,
                                     lw=1.5, ls='-', alpha=0.8, zorder=5)
            self.tube_center_lines.extend([llo, lhi])
            coll = self.ax_spec.fill_between(
                x, [flat_lo, flat_lo], [flat_hi, flat_hi],
                color=color, alpha=0.12, zorder=3)
            self.tube_collections.append(coll)
        else:
            llo, = self.ax_spec.plot(x, lo, color=color, lw=1.2, ls='-',
                                     alpha=0.9, zorder=5)
            lhi, = self.ax_spec.plot(x, hi, color=color, lw=1.2, ls='-',
                                     alpha=0.9, zorder=5)
            self.overlay_objects.extend([llo, lhi])
            coll = self.ax_spec.fill_between(x, lo, hi, color=color,
                                             alpha=0.15, zorder=4)
            self.tube_collections.append(coll)
            if centers:
                c_arr = np.array(centers)
                if len(c_arr) == len(x):
                    lc, = self.ax_spec.plot(x, c_arr, color=color, lw=1.0,
                                            ls='--', alpha=0.7, zorder=5)
                    self.overlay_objects.append(lc)

    def _print_algo_summary(self):
        if not self._algo_results:
            return
        n = len(self._algo_results)
        sep = '-' * 70
        print(f"\n{'='*70}")
        print("СВОДКА АЛГОРИТМОВ:")
        print(f"  {'Алг':>4s}  {'V min':>8s}  {'V max':>8s}  {'V ср':>8s}  "
              f"{'±V min':>8s}  {'±V max':>8s}  {'±V ср':>8s}")
        print(f"  {sep}")
        for algo_num in sorted(self._algo_results.keys()):
            r = self._algo_results[algo_num]
            name = ALGO_NAMES.get(algo_num, '?')[:24]
            print(f"  {algo_num:>4d}  {r['v_min']:8.2f}  {r['v_max']:8.2f}  "
                  f"{r['v_avg']:8.2f}  {r['dv_min']:8.2f}  "
                  f"{r['dv_max']:8.2f}  {r['dv_avg']:8.2f}  | {name}")
        if n >= 2:
            v_mins = [r['v_min'] for r in self._algo_results.values()]
            v_maxs = [r['v_max'] for r in self._algo_results.values()]
            v_avgs = [r['v_avg'] for r in self._algo_results.values()]
            dv_mins = [r['dv_min'] for r in self._algo_results.values()]
            dv_maxs = [r['dv_max'] for r in self._algo_results.values()]
            dv_avgs = [r['dv_avg'] for r in self._algo_results.values()]
            print(f"  {sep}")
            print(f"  {'СР':>4s}  {sum(v_mins)/n:8.2f}  "
                  f"{sum(v_maxs)/n:8.2f}  {sum(v_avgs)/n:8.2f}  "
                  f"{sum(dv_mins)/n:8.2f}  {sum(dv_maxs)/n:8.2f}  "
                  f"{sum(dv_avgs)/n:8.2f}  | Усреднение по {n} алг.")
            spread = max(v_avgs) - min(v_avgs)
            print(f"  Разброс V ср между алгоритмами: {spread:.2f} км/ч")
        print(f"{'='*70}")

    def _on_run(self, _, algo=None):
        if not self.harm_freqs:
            print("Сначала выберите гармоники (5-ю и 4-ю)")
            return

        if algo is None:
            algo = self.last_algo
        self.last_algo = algo
        self._algo_run_done = True
        self.cfg['algorithm'] = algo

        # Сброс GPS-средней (v0.53.1)
        self.gps_v_avg = None

        step_sec = float(self.cfg['time_step'])
        step_frames_float = step_sec * self.cfg['target_fps']

        color_steps = self.color_steps
        peak_hz = self.peak_search_hz

        # --- Диапазон анализа ---
        # Никогда не сбрасываем молча на весь трек. Если analyze_dur
        # округлился до 0 (например, dX < 1/fps), берём ровно 1 кадр
        # от analyze_start. Если analyze_start вне границ — прижимаем.
        f_start = int(round(self.analyze_start))
        if f_start < 0:
            f_start = 0
        if self.n_frames > 0 and f_start > self.n_frames - 1:
            f_start = self.n_frames - 1

        f_dur_frames = int(round(self.analyze_dur))
        if f_dur_frames < 1:
            f_dur_frames = 1

        f_end = f_start + f_dur_frames
        if f_end > self.n_frames:
            f_end = self.n_frames
        if f_end <= f_start:
            f_end = min(f_start + 1, self.n_frames)

        # Нормализуем: отчёт и статус показывают ровно тот диапазон,
        # по которому считали.
        self.analyze_start = f_start
        self.analyze_dur = f_end - f_start
        self._update_time_span()

        if self._spec_vmin is not None:
            vmin, vmax = self._spec_vmin, self._spec_vmax
        elif hasattr(self.spec_img, 'norm') and self.spec_img.norm is not None:
            vmin = self.spec_img.norm.vmin
            vmax = self.spec_img.norm.vmax
        else:
            vmin = float(np.min(self.S_db))
            vmax = float(np.max(self.S_db))

        n_hf = len(self.harm_freqs)
        if n_hf < 2:
            print("Недостаточно гармоник для расчёта (нужно минимум 2)")
            return
        h5_idx = int(np.argmin(np.abs(np.array(self.harm_freqs) - self.h5)))
        h4_idx = int(np.argmin(np.abs(np.array(self.harm_freqs) - self.h4)))
        f_lo5, f_hi5 = get_search_bounds(self.harm_freqs, self.n_harm, h5_idx)
        f_lo4, f_hi4 = get_search_bounds(self.harm_freqs, self.n_harm, h4_idx)

        self._print_system_info()
        print(f"\n{'='*70}")
        print(f"Расчёт min/max по гармоникам — алгоритм {algo}: "
              f"{ALGO_NAMES.get(algo, '?')}")
        fps = self.cfg['target_fps']
        t_start_sec = f_start / fps
        t_end_sec = f_end / fps
        print(f"  Кадры: {f_start}-{f_end}")
        print(f"  Время: {t_start_sec:.3f}-{t_end_sec:.3f}с "
              f"({t_end_sec - t_start_sec:.3f}с)")
        step_actual_frames = int(round(step_frames_float))
        step_actual_sec = step_actual_frames / fps
        print(f"  Шаг: {step_sec:.3f} сек (~{step_frames_float:.1f} кадров, "
              f"факт {step_actual_frames} кадр = {step_actual_sec:.3f}с)")
        print(f"  Порог палитры: -{color_steps} индекс (из 255)")
        print(f"  Поиск пика: +/-{peak_hz:.0f} Гц")
        print(f"  Палитра: vmin={vmin:.1f} dB, vmax={vmax:.1f} dB")
        print(f"  H5: {f_lo5:.1f}-{f_hi5:.1f} Гц  |  "
              f"H4: {f_lo4:.1f}-{f_hi4:.1f} Гц")
        print(f"  Гармоник: {self.n_harm}  |  f0 = {self.f0:.3f} Гц")

        if self.gps_points and len(self.gps_points) >= 3:
            gps_times = [p[0] for p in self.gps_points]
            gps_speeds = [p[1] for p in self.gps_points]
            override = self.cfg.get('gps_lag_override')
            if override is not None:
                self.gps_lag = float(override)
                self.gps_corr = 0.0
                print(f"  GPS запаздывание: {self.gps_lag:.3f} сек "
                      f"(ручной ввод)")
            else:
                audio_times_full = []
                audio_speeds_full = []
                try:
                    step_check = 3
                    for step_i_check in range(0, self.n_frames, step_check):
                        if step_i_check >= self.n_frames:
                            break
                        col = self.S_db[:, step_i_check]
                        h5_peak = find_peak_near(self.freqs, col, self.h5,
                                                 peak_hz, f_lo5, f_hi5)
                        h4_peak = find_peak_near(self.freqs, col, self.h4,
                                                 peak_hz, f_lo4, f_hi4)
                        _, _, _, v_tmp = self._calc_speed(h5_peak, h4_peak)
                        audio_times_full.append(step_i_check / fps)
                        audio_speeds_full.append(v_tmp)
                    if len(audio_times_full) >= 10:
                        smooth_win = max(3, int(1.0 / (step_check / fps)))
                        arr_v = np.array(audio_speeds_full, dtype=float)
                        arr_t = np.array(audio_times_full, dtype=float)
                        if len(arr_v) > smooth_win:
                            kernel = np.ones(smooth_win) / smooth_win
                            arr_v_smooth = np.convolve(arr_v, kernel,
                                                       mode='same')
                        else:
                            arr_v_smooth = arr_v
                        ds = max(1, len(arr_v_smooth)
                                 // max(1, len(gps_times)))
                        arr_t_ds = arr_t[::ds]
                        arr_v_ds = arr_v_smooth[::ds]
                        print(f"  GPS-корреляция: аудио {len(arr_v)} "
                              f"-> сгл. {len(arr_v_smooth)} "
                              f"-> даунс. {len(arr_v_ds)} точек")
                        print(f"  V аудио разброс: "
                              f"{arr_v.min():.2f}..{arr_v.max():.2f} км/ч "
                              f"(std={arr_v.std():.3f})")
                        print(f"  V GPS  разброс: "
                              f"{min(gps_speeds):.2f}.."
                              f"{max(gps_speeds):.2f} км/ч "
                              f"(std={np.std(gps_speeds):.3f})")
                        self.gps_lag, self.gps_corr, lag_results = \
                            find_gps_lag(arr_t_ds.tolist(),
                                         arr_v_ds.tolist(),
                                         gps_times, gps_speeds)
                        if lag_results:
                            print("  Топ-5 лагов по корреляции:")
                            for l, c, nn in lag_results[:5]:
                                marker = (" <==" if abs(l - self.gps_lag)
                                          < 1e-9 else "")
                                print(f"    {l:+.1f}с  corr={c:+.3f}  "
                                      f"n={nn}{marker}")
                        print(f"  GPS запаздывание: {self.gps_lag:+.3f} сек "
                              f"(корреляция: {self.gps_corr:+.3f})")
                        if abs(self.gps_corr) < 0.5:
                            print("  ВНИМАНИЕ: |корреляция| < 0.5 — "
                                  "результат недостоверен!")
                            print("    Задайте gps_lag_override в конфиге "
                                  "(например 1.5)")
                    else:
                        print(f"  GPS: недостаточно точек аудио "
                              f"({len(audio_times_full)})")
                except Exception as e:
                    print(f"  GPS: не удалось вычислить запаздывание ({e})")

        print("  Интерполяция границ: включена (суббинная точность)")
        print(f"  Параметры авто: цил={self.cfg['cylinders']} "
              f"такт={self.cfg['stroke']} "
              f"шина={self.cfg['tire_width']}/"
              f"{self.cfg['tire_profile']} "
              f"R{self.cfg['rim_diameter']} "
              f"ГП={self.cfg['final_drive']} "
              f"КПП={self.cfg['gear_ratio']}")
        print(f"{'='*70}")

        hdr = (f"{'Время,с':>8s}  {'H5 min':>10s}  {'H5 max':>10s}  "
               f"{'H4 min':>10s}  {'H4 max':>10s}  {'H5ц':>8s}  {'H4ц':>8s}  "
               f"{'f,Гц':>8s}  {'n,об/мин':>10s}  {'V,км/ч':>8s}  "
               f"{'±V,км/ч':>8s}  {'V GPS':>8s}")
        print(hdr)
        print("-" * len(hdr))

        all_h5_min, all_h5_max = [], []
        all_h4_min, all_h4_max = [], []
        step_data = []
        all_v, all_dv = [], []
        all_f, all_n = [], []

        self.tube_t = []
        self.tube_h5_mins = []
        self.tube_h5_maxs = []
        self.tube_h4_mins = []
        self.tube_h4_maxs = []
        self.tube_h5_centers = []
        self.tube_h4_centers = []

        expected_h5 = self.h5
        expected_h4 = self.h4

        n_steps = max(1, math.floor((f_end - f_start)
                                    / step_frames_float) + 1)
        for step_i in range(n_steps):
            fs = f_start + int(round(step_i * step_frames_float))
            if fs > f_end:
                break
            fe = min(f_start + int(round((step_i + 1) * step_frames_float)),
                     self.n_frames)
            t_sec = fs / fps

            h5_mins, h5_maxs = [], []
            h4_mins, h4_maxs = [], []
            h5_centers, h4_centers = [], []

            cur_expected_h5 = expected_h5
            cur_expected_h4 = expected_h4

            for frame_i in range(fs, fe):
                if frame_i >= self.n_frames:
                    break
                col_data = self.S_db[:, frame_i]
                cur_expected_h5 = find_peak_near(self.freqs, col_data,
                                                 cur_expected_h5, peak_hz,
                                                 f_lo5, f_hi5)
                cur_expected_h4 = find_peak_near(self.freqs, col_data,
                                                 cur_expected_h4, peak_hz,
                                                 f_lo4, f_hi4)
                h5_centers.append(cur_expected_h5)
                h4_centers.append(cur_expected_h4)

                h5_lo, h5_hi = find_range_dispatch(
                    algo, self.freqs, col_data, cur_expected_h5, color_steps,
                    vmin, vmax, 256, f_lo5, f_hi5)
                h5_mins.append(h5_lo)
                h5_maxs.append(h5_hi)

                h4_lo, h4_hi = find_range_dispatch(
                    algo, self.freqs, col_data, cur_expected_h4, color_steps,
                    vmin, vmax, 256, f_lo4, f_hi4)
                h4_mins.append(h4_lo)
                h4_maxs.append(h4_hi)

            if h5_centers:
                expected_h5 = h5_centers[-1]
            if h4_centers:
                expected_h4 = h4_centers[-1]

            h5_min = min(h5_mins)
            h5_max = max(h5_maxs)
            h4_min = min(h4_mins)
            h4_max = max(h4_maxs)
            h5_c = sum(h5_centers) / len(h5_centers) if h5_centers else 0.0
            h4_c = sum(h4_centers) / len(h4_centers) if h4_centers else 0.0

            all_h5_min.append(h5_min)
            all_h5_max.append(h5_max)
            all_h4_min.append(h4_min)
            all_h4_max.append(h4_max)

            self.tube_t.append(float(fs))
            self.tube_h5_mins.append(h5_min)
            self.tube_h5_maxs.append(h5_max)
            self.tube_h4_mins.append(h4_min)
            self.tube_h4_maxs.append(h4_max)
            self.tube_h5_centers.append(h5_c)
            self.tube_h4_centers.append(h4_c)

            f_val, n_val, Rk_val, V_val = self._calc_speed(h5_c, h4_c)
            df_val, dV_val = self._calc_speed_error(h5_min, h5_max,
                                                    h4_min, h4_max,
                                                    f_val, V_val)

            all_v.append(V_val)
            all_dv.append(dV_val)
            all_f.append(f_val)
            all_n.append(n_val)

            step_data.append({
                't_sec': t_sec,
                'h5_min': h5_min, 'h5_max': h5_max,
                'h4_min': h4_min, 'h4_max': h4_max,
                'h5_c': h5_c, 'h4_c': h4_c,
                'f': f_val, 'n': n_val, 'V': V_val, 'dV': dV_val,
            })

            gps_v_str = ""
            if self.gps_points:
                t_gps = t_sec + self.gps_lag
                gps_t_arr = [p[0] for p in self.gps_points]
                gps_v_arr = [p[1] for p in self.gps_points]
                if gps_t_arr[0] <= t_gps <= gps_t_arr[-1]:
                    gps_v_val = float(np.interp(t_gps, gps_t_arr, gps_v_arr))
                    gps_v_str = f"  {gps_v_val:8.1f}"

            row = (f"{t_sec:8.3f}  {h5_min:10.3f}  {h5_max:10.3f}  "
                   f"{h4_min:10.3f}  {h4_max:10.3f}  {h5_c:8.3f}  "
                   f"{h4_c:8.3f}  {f_val:8.3f}  {n_val:10.0f}  "
                   f"{V_val:8.2f}  {dV_val:8.2f}{gps_v_str}")
            print(row)
            sys.stdout.flush()

        self.h5_min = min(all_h5_min) if all_h5_min else None
        self.h5_max = max(all_h5_max) if all_h5_max else None
        self.h4_min = min(all_h4_min) if all_h4_min else None
        self.h4_max = max(all_h4_max) if all_h4_max else None

        v_min = v_max = v_avg = 0.0
        dv_min = dv_max = dv_avg = 0.0
        n_min_v = n_max_v = 0
        f0_avg = 0.0
        Rk_v = (0.85 * self.cfg['tire_width'] * self.cfg['tire_profile']
                / 100000.0 + 0.0127 * self.cfg['rim_diameter'])

        print(f"\n{'='*70}")
        print("ИТОГ (за весь отрезок):")
        if self.h5_min is not None:
            print(f"  H5: {self.h5_min:.3f} - {self.h5_max:.3f} Гц")
            print(f"  H4: {self.h4_min:.3f} - {self.h4_max:.3f} Гц")

        if all_v:
            v_min = min(all_v)
            v_max = max(all_v)
            v_avg = sum(all_v) / len(all_v)
            dv_min = min(all_dv)
            dv_max = max(all_dv)
            dv_avg = sum(all_dv) / len(all_dv)
            n_min_v = min(all_n)
            n_max_v = max(all_n)
            f0_avg = sum(all_f) / len(all_f)

            print(f"  V min  = {v_min:.2f} ± {dv_min:.2f} км/ч")
            print(f"  V max  = {v_max:.2f} ± {dv_max:.2f} км/ч")
            print(f"  V ср   = {v_avg:.2f} ± {dv_avg:.2f} км/ч")
            print(f"  n      = {n_min_v:.0f} - {n_max_v:.0f} об/мин")
            print(f"  f0     = {f0_avg:.3f} Гц")
            print(f"  Rk     = {Rk_v:.4f} м")

            if self.gps_points:
                gps_t_arr = [p[0] for p in self.gps_points]
                gps_v_arr = [p[1] for p in self.gps_points]
                t_start_gps = t_start_sec + self.gps_lag
                t_end_gps = t_end_sec + self.gps_lag
                gps_in_win, gps_before, gps_after = find_gps_window_points(
                    self.gps_points, t_start_gps, t_end_gps)
                if not gps_in_win and len(gps_t_arr) >= 2:
                    t_mid = (t_start_gps + t_end_gps) / 2
                    if gps_t_arr[0] <= t_mid <= gps_t_arr[-1]:
                        gps_in_win = [(t_mid, float(np.interp(
                            t_mid, gps_t_arr, gps_v_arr)), 0.0)]
                if gps_in_win:
                    gps_avg = sum(p[1] for p in gps_in_win) / len(gps_in_win)
                    self.gps_v_avg = gps_avg  # v0.53.1
                    dv_gps = v_avg - gps_avg
                    print("  GPS-сравнение:")
                    print(f"    Метод: {self.gps_method}")
                    print(f"    GPS-меток в файле: {len(self.gps_points)}")
                    print(f"    Запаздывание GPS: {self.gps_lag:+.3f} сек "
                          f"(корреляция: {self.gps_corr:+.3f})")
                    print(f"    Окно: {t_start_sec:.3f}-{t_end_sec:.3f}с -> "
                          f"GPS: {t_start_gps:.3f}-{t_end_gps:.3f}с")
                    if gps_before:
                        print(f"    [{gps_before[0]:.3f}с] "
                              f"{gps_before[1]:.1f} км/ч  "
                              f"({gps_before[2]:.3f}с до окна)")
                    for pt in gps_in_win:
                        print(f"    [{pt[0]:.3f}с] {pt[1]:.1f} км/ч  "
                              f"(в окне)")
                    if gps_after:
                        print(f"    [{gps_after[0]:.3f}с] "
                              f"{gps_after[1]:.1f} км/ч  "
                              f"({gps_after[2]:.3f}с после окна)")
                    print(f"    V аудио (ср): {v_avg:.2f} км/ч")
                    print(f"    V GPS  (ср): {gps_avg:.2f} км/ч "
                          f"(интерполяция по {len(gps_in_win)} точкам)")
                    print(f"    dV: {dv_gps:+.2f} км/ч")
                else:
                    print(f"  GPS: нет точек в окне "
                          f"(с поправкой t={self.gps_lag:+.3f}с)")

            base = os.path.splitext(os.path.basename(self.filepath))[0]
            report_path = f"{base}_speed_report_algo{algo}.txt"
            try:
                self._write_speed_report(
                    report_path, algo, f_start, f_end, step_sec,
                    color_steps, peak_hz, vmin, vmax,
                    f_lo5, f_hi5, f_lo4, f_hi4,
                    step_data, self.h5_min, self.h5_max,
                    self.h4_min, self.h4_max,
                    v_min, v_max, v_avg, dv_min, dv_max, dv_avg,
                    f0_avg, n_min_v, n_max_v, Rk_v)
                print(f"Отчёт сохранён: {report_path}")
            except Exception as e:
                print(f"Не удалось сохранить отчёт: {e}")
        print(f"{'='*70}")

        self._algo_results[algo] = {
            'v_min': v_min, 'v_max': v_max, 'v_avg': v_avg,
            'dv_min': dv_min, 'dv_max': dv_max, 'dv_avg': dv_avg,
            'n_min': n_min_v, 'n_max': n_max_v, 'f0': f0_avg,
        }
        self._print_algo_summary()

        self._update_overlay_buttons()
        self._update_algo_buttons()
        self.draw_tubes()
        self.draw_ranges()
        self._draw_speed_overlay()
        self._draw_speed_labels()
        self._draw_speed_extremes()
        self.update_status()
        self.fig.canvas.draw_idle()

    def update_status(self):
        parts = []

        if self.phase == self.PHASE_TIME:
            step = 1
        elif self.phase == self.PHASE_H5:
            step = 2
        elif self.phase == self.PHASE_H4:
            step = 3
        else:
            step = 5

        parts.append(f"[{step}/5]")

        hist_total = len(self.zoom_history)
        if hist_total == 0:
            parts.append("Ист: 0/0")
        else:
            hist_pos = self.zoom_pointer + 1
            if hist_pos < 1:
                hist_pos = 1
            if hist_pos > hist_total:
                hist_pos = hist_total
            parts.append(f"Ист: {hist_pos}/{hist_total}")

        try:
            step_sec = float(self.cfg['time_step'])
        except (ValueError, TypeError):
            step_sec = 0.0
        parts.append(f"Шаг={step_sec:.3f}с")

        if self.phase == self.PHASE_TIME:
            parts.append("Drag — выбрать диапазон, "
                         "клик — выбрать 5-ю гармонику")
            parts.append(f"Файл: {os.path.basename(self.filepath)}")
        elif self.phase == self.PHASE_H5:
            parts.append("Кликните мышью по 5-й гармонике")
        elif self.phase == self.PHASE_H4:
            parts.append("Кликните мышью по 4-й гармонике")
        elif self.phase == self.PHASE_DONE:
            # --- Диапазон анализа (кадры и секунды) ---
            try:
                fps_status = float(self.cfg['target_fps'])
                f_s = int(round(self.analyze_start))
                f_d = int(round(self.analyze_dur))
                if f_d < 1:
                    f_d = 1
                f_e = min(f_s + f_d, self.n_frames)
                if f_e <= f_s:
                    f_e = min(f_s + 1, self.n_frames)
                if fps_status > 0:
                    t0 = f_s / fps_status
                    t1 = f_e / fps_status
                    parts.append(
                        f"Диап: кадры {f_s}-{f_e} "
                        f"({t0:.3f}-{t1:.3f}с)"
                    )
                else:
                    parts.append(f"Диап: кадры {f_s}-{f_e}")
            except Exception:
                pass

            if not self._algo_run_done:
                parts.append("Выберите алгоритм: 1.dB ... 6.RMS")
                if self.h5 is not None:
                    parts.append(f"H5={self.h5:.1f} Гц")
                if self.h4 is not None:
                    parts.append(f"H4={self.h4:.1f} Гц")
                if self.f0 is not None:
                    parts.append(f"f0={self.f0:.3f} Гц")
            else:
                algo_name = ALGO_NAMES.get(self.last_algo, "?")
                parts.append(f"Готово | Алго {self.last_algo}: {algo_name}")
                if (self.h5 is not None and self.h4 is not None
                        and self.f0 is not None):
                    parts.append(f"H5={self.h5:.1f} Гц | "
                                 f"H4={self.h4:.1f} Гц")
                    parts.append(f"f0={self.f0:.1f} Гц")

                    # Vср — из результатов последнего запуска алгоритма,
                    # ровно то же значение, что записано в отчёте.
                    v_avg_status = None
                    if self._algo_results and \
                            self.last_algo in self._algo_results:
                        v_avg_status = self._algo_results[
                            self.last_algo].get('v_avg')
                    if v_avg_status is not None:
                        parts.append(f"Vср={v_avg_status:.1f} км/ч")

                    gps_avg = getattr(self, 'gps_v_avg', None)
                    if gps_avg is not None:
                        parts.append(f"Vgps ср={gps_avg:.1f} км/ч")

                    # Δgps — сдвиг между GPS-временем и временем аудио
                    # (как в отчёте). Если активен gps_lag_override из
                    # JSON — помечаем «(OVR)».
                    gps_dt_used = self.gps_lag
                    _override = self.cfg.get('gps_lag_override')
                    ovr_flag = False
                    if _override is not None:
                        try:
                            gps_dt_used = float(_override)
                            ovr_flag = True
                        except (ValueError, TypeError):
                            pass
                    parts.append(f"Δgps={gps_dt_used:+.3f}с"
                                 + (" (OVR)" if ovr_flag else ""))

        self.status.set_text(' | '.join(parts) if parts else '')
        self.fig.canvas.draw_idle()

    def _toggle_help(self):
        if self.help_visible:
            self._hide_help()
        else:
            self._show_help()

    def _show_help(self):
        left_lines = list(HELP_LEFT_LINES)
        right_lines = list(HELP_RIGHT_LINES)
        maxw_l = max(len(l) for l in left_lines)
        maxw_r = max(len(l) for l in right_lines)
        while len(right_lines) < len(left_lines):
            right_lines.append("")
        while len(left_lines) < len(right_lines):
            left_lines.append("")
        combined = []
        for l, r in zip(left_lines, right_lines):
            combined.append(l.ljust(maxw_l) + "  |  " + r.ljust(maxw_r))
        txt = '\n'.join(combined)
        self.help_obj = self.fig.text(
            0.5, 0.5, txt,
            transform=self.fig.transFigure,
            fontsize=8, va='center', ha='center',
            family='monospace', color='white',
            bbox=dict(boxstyle='round,pad=0.8', facecolor='#1a1a2e',
                      alpha=0.95, edgecolor='#e0e0e0', lw=1.5),
            zorder=100
        )
        self.help_visible = True
        self.fig.canvas.draw_idle()

    def _hide_help(self):
        if self.help_visible and hasattr(self, 'help_obj'):
            try:
                self.help_obj.remove()
            except Exception:
                pass
            self.help_obj = None
            self.help_visible = False
            self.fig.canvas.draw_idle()


def prompt_car_params(config, config_path):
    """Запрос параметров автомобиля. Промпты в stderr."""
    keys = [
        ('cylinders',     'Цилиндров',     int),
        ('stroke',        'Тактность (1=2-такт, 2=4-такт)', int),
        ('tire_width',    'Ширина шины',   int),
        ('tire_profile',  'Профиль шины',  int),
        ('rim_diameter',  'Диаметр диска', int),
        ('final_drive',   'Главная пара',  float),
        ('gear_ratio',    'Передача КПП',  float),
    ]

    print("\nПАРАМЕТРЫ АВТОМОБИЛЯ (из config):", file=sys.stderr)
    print(f"  Цилиндры:      {config['cylinders']}", file=sys.stderr)
    print(f"  Тактность:     {config['stroke']} "
          f"({'4-такт' if config['stroke']==2 else '2-такт'})",
          file=sys.stderr)
    print(f"  Шина:          {config['tire_width']}/"
          f"{config['tire_profile']} "
          f"R{config['rim_diameter']}", file=sys.stderr)
    print(f"  ГП:            {config['final_drive']}", file=sys.stderr)
    print(f"  КПП:           {config['gear_ratio']}", file=sys.stderr)
    print("Согласны? (Enter=да / n=ввести новые):",
          file=sys.stderr, flush=True)

    try:
        resp = input().strip().lower()
    except (EOFError, OSError):
        resp = ''

    if resp == 'n':
        for key, label, typ in keys:
            cur = config[key]
            print(f"  {label} [{cur}]: ", file=sys.stderr,
                  end='', flush=True)
            try:
                val = input().strip()
            except (EOFError, OSError):
                val = ''
            if val:
                try:
                    config[key] = typ(val)
                except ValueError:
                    print(f"    Ошибка: оставлено {cur}",
                          file=sys.stderr)
        save_config(config, config_path)


def main():
    parser = argparse.ArgumentParser(
        description='Расчёт скорости по звуку из .ts-фрагментов'
    )
    parser.add_argument('filepath', help='Путь к .ts файлу')
    parser.add_argument('--config', default=None,
                        help='Файл конфигурации JSON '
                             '(по умолчанию: <base_ts>_config.json)')
    args = parser.parse_args()

    if not os.path.exists(args.filepath):
        print(f"Файл не найден: {args.filepath}")
        sys.exit(1)

    base_ts = os.path.splitext(os.path.basename(args.filepath))[0]
    per_file_config = f"{base_ts}_config.json"

    if args.config:
        config_path = args.config
    elif os.path.exists(per_file_config):
        config_path = per_file_config
    else:
        if os.path.exists(CONFIG_FILE):
            print(f"Перенос настроек из {CONFIG_FILE} -> "
                  f"{per_file_config}", file=sys.stderr)
            old_cfg = load_config(CONFIG_FILE)
            save_config(old_cfg, per_file_config)
            config_path = per_file_config
        else:
            config_path = per_file_config

    update_history_log()

    config = load_config(config_path)

    if not config.get('harmonic_5_freq') or not config.get('harmonic_4_freq'):
        print("Гармоники не заданы в конфиге.")
        print("Запустите программу, выберите 5-ю и 4-ю гармоники кликом мыши.")
        print("Настройки сохранятся автоматически (S — сохранить вручную).")

    prompt_car_params(config, config_path)

    analyzer = AudioSpeedAnalyzer(args.filepath, config, config_path)
    analyzer.run()


if __name__ == '__main__':
    main()
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gps_parser.py v0.46
Извлечение GPS-данных из .ts-файлов видеорегистраторов (Novatek/Viofo/70mai).

Основной метод: exiftool -ee3 (если найден)
Fallback: прямой бинарный разбор потока 0x0300

Основано на Sergei Franco's Novatek GPS extractor (GPL3)
https://gist.github.com/UndeRus/01ef8b436353f579760a60b0dea96253
и ts_integrity_check.py v3.37
"""

import os
import sys
import struct
import time
import datetime
import subprocess
import shutil
import numpy as np

VERSION = "0.46"

# ============================================================
# Поиск exiftool (аналог _find_exec из audio_speed_calc.py)
# ============================================================

def _find_exec(name):
    """Ищет исполняемый файл: рядом со скриптом, в bin/, в PATH."""
    # Рядом со скриптом
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(script_dir, name),
        os.path.join(script_dir, name + '.exe'),
        os.path.join(script_dir, 'bin', name),
        os.path.join(script_dir, 'bin', name + '.exe'),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    # В PATH
    found = shutil.which(name)
    if found:
        return found
    return None


# ============================================================
# GPS-парсинг (из ts_integrity_check.py v3.37 — отлажено)
# ============================================================

def fix_time(dt):
    """Форматирует GPS-дату в ISO-строку UTC."""
    return "%04d-%02d-%02dT%02d:%02d:%02dZ" % (
        dt['Year'] + 2000, dt['Month'], dt['Day'],
        dt['Hour'], dt['Minute'], dt['Second'])

def fix_coordinates(hemi, raw):
    """Конвертирует DDMM.MMMM -> десятичные градусы."""
    degrees = int(raw / 100)
    minutes = raw - (degrees * 100)
    result = degrees + minutes / 60.0
    if hemi in ['S', 'W']:
        result = -result
    return result

def fix_speed(speed):
    """Узлы -> км/ч."""
    return speed * 1.852

def deobfuscate_coord(latitude, longitude):
    """Расшифровка координат для обфусцированных регистраторов."""
    latitude = (latitude - 187.98217) / 3.0
    longitude = (longitude - 2199.19876) * 0.5
    return latitude, longitude

def get_gps_offset(data):
    """Поиск маркера A + N/S + E/W с конца payload."""
    pointer = len(data) - 20
    beginning = 0
    while pointer > beginning:
        try:
            active, lon_hemi, lat_hemi = struct.unpack_from('<sss', data, pointer)
            active = active.decode()
            lon_hemi = lon_hemi.decode()
            lat_hemi = lat_hemi.decode()
            if active == 'A' and lon_hemi in ['N', 'S'] and lat_hemi in ['E', 'W']:
                offset = pointer - 24
                return offset
        except (UnicodeDecodeError, struct.error):
            pass
        pointer -= 1
    return -1

def get_gps_data(data, deobfuscate):
    """Полный разбор одной GPS-точки: время, координаты, скорость, курс."""
    gps = {
        'Epoch': None,
        'DT': {'Year': None, 'Month': None, 'Day': None,
               'Hour': None, 'Minute': None, 'Second': None, 'DT': None},
        'Loc': {'Lat': {'Raw': None, 'Hemi': None, 'Float': None},
                'Lon': {'Raw': None, 'Hemi': None, 'Float': None},
                'Speed': None, 'Bearing': None},
    }
    if struct.unpack_from('>c', data)[0] in [b'\x05', b'\xF0']:
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
        active = active.decode()
        gps['Loc']['Lat']['Hemi'] = lat_hemi.decode()
        gps['Loc']['Lon']['Hemi'] = lon_hemi.decode()
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
        gps['Epoch'] = int(time.mktime(time.strptime(
            gps['DT']['DT'][:-1] + 'UTC', "%Y-%m-%dT%H:%M:%S%Z")))
    except ValueError:
        return None
    return gps


# ============================================================
# Упрощённый парсинг TS (только GPS, без PCR/PTS/NAL)
# ============================================================

def parse_ts_gps(filepath, deobfuscate=False):
    """
    Прямой бинарный разбор .ts-файла — только GPS (PID 0x0300).
    Возвращает список словарей gps_data (как get_gps_data).
    """
    gps_points = []

    with open(filepath, 'rb') as f:
        data = f.read()

    if not data:
        return gps_points

    # Поиск синхробайта 0x47
    is_ts = False
    if len(data) >= 377:
        if data[0] == 0x47 and data[188] == 0x47 and data[376] == 0x47:
            is_ts = True
    if not is_ts:
        for start in range(min(204, len(data) - 376)):
            if (data[start] == 0x47 and
                start + 188 < len(data) and data[start + 188] == 0x47 and
                start + 376 < len(data) and data[start + 376] == 0x47):
                data = data[start:]
                is_ts = True
                break
    if not is_ts:
        return gps_points

    offset = 0
    partial = b''

    while offset + 188 <= len(data):
        packet = data[offset:offset + 188]
        offset += 188

        if packet[0] != 0x47:
            # Resync
            resync = -1
            for i in range(188):
                if offset + i + 188 <= len(data):
                    if data[offset + i] == 0x47:
                        if offset + i + 188 <= len(data) and data[offset + i + 188] == 0x47:
                            resync = i
                            break
            if resync >= 0:
                offset += resync
            continue

        pid = ((packet[1] & 0x1F) << 8) | packet[2]

        # Только GPS (PID 0x0300)
        if pid != 0x0300:
            continue

        adaptation_control = (packet[3] >> 4) & 0x03
        payload_start = 4
        has_adaptation = bool(adaptation_control & 0x02)
        has_payload = bool(adaptation_control & 0x01)

        if has_adaptation:
            if len(packet) > 4:
                adaptation_length = packet[4]
                payload_start = 5 + adaptation_length
            else:
                continue

        if not has_payload:
            continue

        payload = packet[payload_start:]

        if len(payload) >= 4 and payload[:4] == b'\x00\x00\x01\xbf':
            gps_result = get_gps_data(payload, deobfuscate)
            if gps_result:
                gps_points.append(gps_result)
            partial = b''
        else:
            if partial:
                combined = partial + payload
                gps_result = get_gps_data(combined, deobfuscate)
                if gps_result:
                    gps_points.append(gps_result)
                partial = b''
            else:
                if len(payload) >= 14:
                    partial = payload[-14:]

    return gps_points


# ============================================================
# exiftool — основной метод
# ============================================================

def parse_ts_gps_exiftool(filepath, exiftool_path=None):
    """
    Извлечение GPS через exiftool -ee3.
    Возвращает список словарей в том же формате, что parse_ts_gps.
    """
    if exiftool_path is None:
        exiftool_path = _find_exec('exiftool')
    if exiftool_path is None:
        return None  # exiftool не найден

    try:
        cmd = [
            exiftool_path, '-ee3', '-n', '-G',
            '-GPSDateTime', '-GPSSpeed', '-GPSLatitude', '-GPSLongitude',
            '-GPSDirection', '-GPSStatus',
            '-p', '$GPSDateTime\t$GPSSpeed\t$GPSLatitude\t$GPSLongitude\t$GPSDirection\t$GPSStatus\n',
            filepath
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                                encoding='utf-8', errors='replace')
    except Exception:
        return None

    if result.returncode != 0:
        return None

    gps_points = []
    for line in result.stdout.strip().split('\n'):
        if not line.strip():
            continue
        parts = line.strip().split('\t')
        if len(parts) < 4:
            continue
        try:
            dt_str = parts[0].strip()
            speed_raw = float(parts[1]) if parts[1].strip() else None
            lat_raw = float(parts[2]) if parts[2].strip() else None
            lon_raw = float(parts[3]) if parts[3].strip() else None
            bearing_raw = float(parts[4]) if len(parts) > 4 and parts[4].strip() else None
            active = parts[5].strip() if len(parts) > 5 and parts[5].strip() else 'A'

            # Парсинг даты: "2026:06:02 05:07:35Z" или "2026-06-02T05:07:35Z"
            dt_str = dt_str.replace(':', '-', 2).replace(' ', 'T')
            if not dt_str.endswith('Z'):
                dt_str += 'Z'

            dt_obj = datetime.datetime.strptime(dt_str[:-1], "%Y-%m-%dT%H:%M:%S")
            epoch = int(time.mktime(dt_obj.timetuple()))

            # exiftool -n отдаёт скорость в м/с (если GPS Speed Ref не km/h)
            # На практике exiftool отдаёт в km/h при -n для Novatek
            # Но проверим: если скорость > 200 — вероятно м/с, конвертируем
            speed_kmh = speed_raw
            if speed_kmh is not None and speed_kmh > 200:
                speed_kmh = speed_raw * 3.6  # м/с -> км/ч

            # Координаты при -n уже в десятичных градусах
            lat_float = lat_raw
            lon_float = lon_raw

            gps = {
                'Epoch': epoch,
                'DT': {'Year': dt_obj.year - 2000, 'Month': dt_obj.month,
                       'Day': dt_obj.day, 'Hour': dt_obj.hour,
                       'Minute': dt_obj.minute, 'Second': dt_obj.second,
                       'DT': dt_str},
                'Loc': {'Lat': {'Raw': lat_raw, 'Hemi': 'N' if lat_float and lat_float >= 0 else 'S',
                                'Float': lat_float},
                        'Lon': {'Raw': lon_raw, 'Hemi': 'E' if lon_float and lon_float >= 0 else 'W',
                                'Float': lon_float},
                        'Speed': speed_kmh, 'Bearing': bearing_raw},
                'Source': 'exiftool',
            }
            gps_points.append(gps)
        except (ValueError, TypeError, IndexError):
            continue

    return gps_points


# ============================================================
# Унифицированный интерфейс
# ============================================================

def extract_gps(filepath, deobfuscate=False, exiftool_path=None):
    """
    Главная функция: извлекает GPS из .ts-файла.
    Сначала пробует exiftool (если найден), потом fallback на прямой разбор.
    Возвращает (gps_points, method_str).
    """
    # Пробуем exiftool
    if exiftool_path is None:
        exiftool_path = _find_exec('exiftool')

    if exiftool_path:
        gps_points = parse_ts_gps_exiftool(filepath, exiftool_path)
        if gps_points is not None and len(gps_points) > 0:
            return gps_points, 'exiftool'

    # Fallback: прямой разбор
    gps_points = parse_ts_gps(filepath, deobfuscate)
    if gps_points:
        return gps_points, 'direct'

    return [], 'none'


def gps_to_relative_time(gps_points, first_epoch=None):
    """
    Преобразует GPS-точки в список (t_relative_sec, speed_kmh, lat, lon).
    t_relative — секунды от первой GPS-точки (или от first_epoch).
    """
    if not gps_points:
        return []

    if first_epoch is None:
        first_epoch = gps_points[0]['Epoch']

    result = []
    for g in gps_points:
        if g['Epoch'] is None:
            continue
        t_rel = g['Epoch'] - first_epoch
        speed = g['Loc']['Speed']
        lat = g['Loc']['Lat']['Float']
        lon = g['Loc']['Lon']['Float']
        bearing = g['Loc']['Bearing']
        result.append((t_rel, speed, lat, lon, bearing))

    return result


# ============================================================
# Кросс-корреляция: поиск запаздывания GPS относительно аудио
# ============================================================

def find_gps_lag(audio_times, audio_speeds, gps_times, gps_speeds,
                 lag_range=(-3.0, 3.0), lag_step=0.033):
    """
    Ищет оптимальный сдвиг GPS относительно аудио (кросс-корреляция).
    audio_times: массив временных меток аудио (сек от начала файла)
    audio_speeds: массив скоростей из аудио (км/ч)
    gps_times: массив временных меток GPS (сек от начала файла)
    gps_speeds: массив скоростей GPS (км/ч)
    Возвращает (best_lag, best_correlation, results_list).
    """
    audio_times = np.array(audio_times, dtype=float)
    audio_speeds = np.array(audio_speeds, dtype=float)
    gps_times = np.array(gps_times, dtype=float)
    gps_speeds = np.array(gps_speeds, dtype=float)

    best_lag = 0.0
    best_corr = 0.0
    results = []

    lags = np.arange(lag_range[0], lag_range[1] + lag_step, lag_step)

    for lag in lags:
        # Сдвигаем GPS-время на lag
        shifted_gps_t = gps_times + lag

        # Интерполируем GPS скорость на временные метки аудио
        # (только в зоне перекрытия)
        mask = (audio_times >= shifted_gps_t[0]) & (audio_times <= shifted_gps_t[-1])

        if mask.sum() < 5:
            results.append((lag, 0.0, 0))
            continue

        gps_interp = np.interp(audio_times[mask], shifted_gps_t, gps_speeds)

        # Корреляция Пирсона
        a = audio_speeds[mask]
        g = gps_interp
        if a.std() < 0.01 or g.std() < 0.01:
            results.append((lag, 0.0, int(mask.sum())))
            continue

        corr = np.corrcoef(a, g)[0, 1]
        results.append((lag, corr, int(mask.sum())))

        if abs(corr) > best_corr:
            best_corr = abs(corr)
            best_lag = lag

    return best_lag, best_corr, results


# ============================================================
# Тест при запуске напрямую
# ============================================================

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Использование: python gps_parser.py file.TS [-d]")
        print("  -d  деобфускация координат")
        sys.exit(1)

    filepath = sys.argv[1]
    deobf = '-d' in sys.argv

    # Поиск exiftool
    exiftool = _find_exec('exiftool')
    if exiftool:
        print("exiftool: %s" % exiftool)
    else:
        print("exiftool: не найден, будет использован прямой разбор")

    gps_points, method = extract_gps(filepath, deobfuscate=deobf)

    print("\nМетод извлечения: %s" % method)
    print("GPS-меток: %d" % len(gps_points))

    if gps_points:
        rel = gps_to_relative_time(gps_points)
        print("\n  t,сек    V,км/ч     Lat        Lon      Курс")
        print("  " + "-" * 55)
        for t, speed, lat, lon, bearing in rel:
            print("  %7.1f  %7.1f  %9.5f  %9.5f  %6.1f" % (t, speed, lat, lon, bearing))

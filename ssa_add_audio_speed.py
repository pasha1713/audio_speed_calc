#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ssa_add_audio_speed.py — добавляет " [аудио: X.X км/ч; GPS: Y.Y км/ч]"
к строке "Скорость: N км/ч" в каждой Dialogue .ssa.

v0.58.10.7:
  * --time-offset SEC: смещение для поиска в audio_speeds.csv.
    Для полного .ssa: 0. Для фрагмента: t_start_eff из _full.txt.
  * Сохраняет end time (был баг в v0.58.10.3).
  * Читает 3 колонки CSV (t, V_audio, V_gps).

usage:
    ssa_add_audio_speed.py --ssa FILE.ssa --speeds-csv PATH
                           [--split-fps N] [--time-offset SEC]
                           [--dry-run]
"""
import argparse
import os
import re
import statistics
import sys

RE_DIALOGUE = re.compile(
    r'^(Dialogue:\s*\d+,)'
    r'(\d+:\d+:\d+\.\d+),'
    r'(\d+:\d+:\d+\.\d+),'
    r'(.*)$'
)
RE_SPEED = re.compile(r'Скорость:\s*[0-9.]+\s*км/ч')
RE_OLD_MARK = re.compile(r'\s*\[[^\]]*(?:аудио:|GPS:)[^\]]*\]')


def hms_to_cs(hms):
    # [PATCH-fix12] Возвращает ms (не cs, как раньше). Имя
    # оставлено ради совместимости, но единица — миллисекунды.
    h, m, rest = hms.split(':')
    if '.' in rest:
        s_part, frac = rest.split('.')
        n = len(frac)
        if n == 2:
            sub_ms = int(frac) * 10
        elif n == 3:
            sub_ms = int(frac)
        else:
            sub_ms = int(round(int(frac) * 1000.0 / (10 ** n)))
        return (int(h) * 3600 + int(m) * 60 + int(s_part)) * 1000 + sub_ms
    return (int(h) * 3600 + int(m) * 60 + int(rest)) * 1000


def cs_to_hms(cs):
    # [PATCH-fix12] Принимает ms, отдаёт .mmm (3 знака).
    if cs < 0:
        cs = 0
    h = cs // 3600000
    cs -= h * 3600000
    m = cs // 60000
    cs -= m * 60000
    s = cs // 1000
    ms = cs % 1000
    return "%02d:%02d:%02d.%03d" % (h, m, s, ms)


def load_speeds(path):
    times, v_audio, v_gps = [], [], []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.replace(';', ',').split(',')
            if len(parts) < 2:
                continue
            try:
                t = float(parts[0])
                va = float(parts[1])
            except ValueError:
                continue
            vg = None
            if len(parts) >= 3 and parts[2].strip():
                try:
                    vg = float(parts[2])
                except ValueError:
                    vg = None
            times.append(t)
            v_audio.append(va)
            v_gps.append(vg)
    return times, v_audio, v_gps


def _interp(t, times, values):
    pairs = [(times[i], values[i]) for i in range(len(times))
             if values[i] is not None]
    if not pairs:
        return None
    if len(pairs) == 1:
        return pairs[0][1]
    ts = [p[0] for p in pairs]
    vs = [p[1] for p in pairs]
    if t <= ts[0]:
        return vs[0]
    if t >= ts[-1]:
        return vs[-1]
    lo, hi = 0, len(ts) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if ts[mid] <= t:
            lo = mid
        else:
            hi = mid
    t0, t1 = ts[lo], ts[hi]
    v0, v1 = vs[lo], vs[hi]
    if t1 <= t0:
        return v0
    frac = (t - t0) / (t1 - t0)
    return v0 + frac * (v1 - v0)


def _build_suffix(t_shown, time_offset, times, v_audio, v_gps):
    """t_shown — время в .ssa; time_offset — сдвиг в полное время."""
    t = t_shown + time_offset
    va = _interp(t, times, v_audio)
    vg = _interp(t, times, v_gps) if v_gps else None
    parts = []
    if va is not None:
        parts.append('аудио: %.1f км/ч' % va)
    if vg is not None:
        parts.append('GPS: %.1f км/ч' % vg)
    return (' [' + '; '.join(parts) + ']') if parts else ''


def detect_eol(raw):
    if '\r\n' in raw:
        return '\r\n'
    if '\r' in raw:
        return '\r'
    return '\n'


def _median_dialogue_duration_sec(lines):
    durs = []
    for line in lines:
        m = RE_DIALOGUE.match(line)
        if not m:
            continue
        try:
            # [PATCH-fix12] hms_to_cs теперь в ms, делим на 1000.
            d = (hms_to_cs(m.group(3)) - hms_to_cs(m.group(2))) / 1000.0
            if d > 0:
                durs.append(d)
        except Exception:
            pass
    return statistics.median(durs) if durs else 0.0


def process_ssa(path, times, v_audio, v_gps, split_fps=0,
                time_offset=0.0, dry_run=False):
    with open(path, 'r', encoding='utf-8', newline='') as f:
        raw = f.read()
    eol = detect_eol(raw)
    lines = raw.split(eol)

    med = _median_dialogue_duration_sec(lines)
    if split_fps > 0 and med > 0 and med < 0.7 / split_fps:
        print('[ssa_add_audio_speed] median=%.4fs < %.4fs -> '
              'already split, split_fps=0' % (med, 0.7 / split_fps),
              file=sys.stderr)
        split_fps = 0

    out = []
    n_modified = 0
    n_total = 0
    n_sub = 0

    for line in lines:
        m = RE_DIALOGUE.match(line)
        if not m:
            out.append(line)
            continue
        n_total += 1
        start_str, end_str, tail = m.group(2), m.group(3), m.group(4)
        start_cs = hms_to_cs(start_str)
        end_cs = hms_to_cs(end_str)
        tail_clean = RE_OLD_MARK.sub('', tail)

        if split_fps > 0 and end_cs > start_cs:
            D = end_cs - start_cs
            # [PATCH-fix12] D в ms, делим на 1000.
            n = max(1, int(round(D / 1000.0 * split_fps)))
            any_sub = False
            for i in range(n):
                sub_s = start_cs + int(round(i * D / n))
                sub_e = start_cs + int(round((i + 1) * D / n))
                if sub_e <= sub_s:
                    continue
                # [PATCH-fix12] sub_s в ms.
                t_shown = sub_s / 1000.0
                suffix = _build_suffix(t_shown, time_offset,
                                        times, v_audio, v_gps)
                if RE_SPEED.search(tail_clean):
                    nt = RE_SPEED.subn(
                        lambda mm, _s=suffix: mm.group(0) + _s,
                        tail_clean, count=1)[0]
                else:
                    nt = tail_clean
                out.append(m.group(1) + cs_to_hms(sub_s) + ','
                           + cs_to_hms(sub_e) + ',' + nt)
                n_sub += 1
                any_sub = True
            if any_sub:
                n_modified += 1
            else:
                out.append(line)
        else:
            # [PATCH-fix12] start_cs в ms.
            t_shown = start_cs / 1000.0
            suffix = _build_suffix(t_shown, time_offset,
                                    times, v_audio, v_gps)
            if not suffix:
                out.append(line)
                continue
            if not RE_SPEED.search(tail_clean):
                out.append(line)
                continue
            nt = RE_SPEED.subn(
                lambda mm, _s=suffix: mm.group(0) + _s,
                tail_clean, count=1)[0]
            out.append(m.group(1) + start_str + ',' + end_str + ',' + nt)
            n_modified += 1

    if dry_run:
        print('[ssa_add_audio_speed] DRY: %d/%d dialogues, '
              'split=%d, offset=%.3fs, sub=%d'
              % (n_modified, n_total, split_fps, time_offset,
                 n_sub if n_sub else n_total), file=sys.stderr)
        return n_modified, n_total

    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(eol.join(out))
    print('[ssa_add_audio_speed] %d/%d dialogues, split=%d, '
          'offset=%.3fs, sub=%d -> %s'
          % (n_modified, n_total, split_fps, time_offset,
             n_sub if n_sub else n_total, path), file=sys.stderr)
    return n_modified, n_total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ssa', required=True)
    ap.add_argument('--speeds-csv', required=True)
    ap.add_argument('--split-fps', type=int, default=0)
    ap.add_argument('--time-offset', type=float, default=0.0)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    if not os.path.isfile(a.ssa):
        print('ERROR: ssa not found: %s' % a.ssa, file=sys.stderr)
        return 2
    if not os.path.isfile(a.speeds_csv):
        print('ERROR: speeds csv not found: %s' % a.speeds_csv,
              file=sys.stderr)
        return 2
    try:
        times, v_audio, v_gps = load_speeds(a.speeds_csv)
    except OSError as e:
        print('ERROR: read csv: %s' % e, file=sys.stderr)
        return 2
    if not times:
        print('ERROR: empty speeds csv', file=sys.stderr)
        return 3
    try:
        n_mod, _ = process_ssa(a.ssa, times, v_audio, v_gps,
                                split_fps=a.split_fps,
                                time_offset=a.time_offset,
                                dry_run=a.dry_run)
    except OSError as e:
        print('ERROR: ssa: %s' % e, file=sys.stderr)
        return 2
    return 0 if n_mod > 0 else 4


if __name__ == '__main__':
    sys.exit(main())

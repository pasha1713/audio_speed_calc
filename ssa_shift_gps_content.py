#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ssa_shift_gps_content.py

Переставляет GPS-содержимое между Dialogue на K кадров.
НЕ трогает MSK-строку \\NКадр:… MSK.

  gps_part[N] = gps_part_old[N + K]     (знак K = направление)
  msk_part[N] = msk_part_old[N]         (не меняется)

Крайние |K| кадров — fallback: свои данные.

usage:
    ssa_shift_gps_content.py --ssa FILE.ssa --shift-frames K [--dry-run]
"""

import argparse
import os
import re
import sys

RE_DIALOGUE = re.compile(
    r'^(Dialogue:\s*\d+,)'
    r'(\d+:\d+:\d+\.\d+),'
    r'(\d+:\d+:\d+\.\d+),'
    r'(.*)$'
)
MARKER = '\\NКадр:'


def split_tail(tail):
    idx = tail.rfind(MARKER)
    if idx < 0:
        return tail, ''
    return tail[:idx], tail[idx:]


def detect_eol(raw):
    if '\r\n' in raw:
        return '\r\n'
    if '\r' in raw:
        return '\r'
    return '\n'


def process(ssa_path, shift_frames, dry_run=False):
    if shift_frames == 0:
        print('[gps_shift] K=0 — nothing to do', file=sys.stderr)
        return 0, 0

    with open(ssa_path, 'r', encoding='utf-8-sig', newline='') as f:
        raw = f.read()
    eol = detect_eol(raw)
    lines = raw.split(eol)

    dialogues = []
    line_map = []
    for line in lines:
        m = RE_DIALOGUE.match(line)
        if m:
            gps, msk = split_tail(m.group(4))
            dialogues.append({
                'prefix': m.group(1),
                'start':  m.group(2),
                'end':    m.group(3),
                'gps':    gps,
                'msk':    msk,
            })
            line_map.append(('dialogue', len(dialogues) - 1))
        else:
            line_map.append(('other', line))

    n = len(dialogues)
    if n == 0:
        print('[gps_shift] no Dialogue in %s' % ssa_path,
              file=sys.stderr)
        return 0, 0

    new_gps = []
    shifted = 0
    # v5-fix: src = i - K (компенсация отставания GPS).
    # ssa_gps_time_correction_sec > 0 (GPS отстаёт):
    #   K = round(sec*fps) > 0, gps_part[N] = gps_part_old[N-K]
    #   Кадр K получает GPS из кадра 0.
    for i in range(n):
        src = i - shift_frames
        # v5-fallback-fix: clamp src в [0, n-1].
        # Раньше для i<0 или i>=n fallback = dialogues[i]
        # (свои данные) -> блок GPS дублировался.
        if src < 0:
            src = 0
        elif src >= n:
            src = n - 1
        new_gps.append(dialogues[src]['gps'])
        if src != i:
            shifted += 1

    if dry_run:
        print('[gps_shift] DRY: %d/%d, K=%d'
              % (shifted, n, shift_frames), file=sys.stderr)
        return shifted, n

    out = []
    for kind, val in line_map:
        if kind == 'dialogue':
            d = dialogues[val]
            out.append(d['prefix'] + d['start'] + ',' + d['end']
                       + ',' + new_gps[val] + d['msk'])
        else:
            out.append(val)

    with open(ssa_path, 'w', encoding='utf-8-sig', newline='') as f:
        f.write(eol.join(out))

    print('[gps_shift] %d/%d, K=%d -> %s'
          % (shifted, n, shift_frames, ssa_path), file=sys.stderr)
    return shifted, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ssa', required=True)
    ap.add_argument('--shift-frames', type=int, required=True)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    if not os.path.isfile(a.ssa):
        print('ERROR: %s' % a.ssa, file=sys.stderr)
        return 2
    shifted, _n = process(a.ssa, a.shift_frames, dry_run=a.dry_run)
    if a.dry_run:
        return 0
    return 0 if shifted > 0 else 4


if __name__ == '__main__':
    sys.exit(main())

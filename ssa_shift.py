#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ssa_shift.py — прибавляет offset секунд к Start/End во всех Dialogue."""
import argparse, re, sys

RE = re.compile(r'^(Dialogue:\s*\d+,)(\d+:\d+:\d+\.\d+),(\d+:\d+:\d+\.\d+)(,.*)$')

def to_sec(s):
    h, m, rest = s.split(':')
    a, b = rest.split('.')
    # [PATCH-fix12] tolerant: .cc (2 знака) и .mmm (3 знака).
    n = len(b)
    if n == 2:
        return int(h)*3600 + int(m)*60 + int(a) + int(b) / 100.0
    if n == 3:
        return int(h)*3600 + int(m)*60 + int(a) + int(b) / 1000.0
    return int(h)*3600 + int(m)*60 + int(a) + int(b) / (10.0 ** n)

def to_ssa(t):
    if t < 0: t = 0
    h = int(t // 3600); t -= h * 3600
    m = int(t // 60); t -= m * 60
    s = int(t)
    # [PATCH-fix12] ms (было cs). При frame-snap в ssa_frame_time
    # cs-точность даёт дубликаты/пропуски кадров.
    ms = int(round((t - s) * 1000))
    if ms >= 1000: s += 1; ms -= 1000
    if s >= 60: m += 1; s -= 60
    if m >= 60: h += 1; m -= 60
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"

ap = argparse.ArgumentParser()
ap.add_argument('--ssa', required=True)
ap.add_argument('--offset', type=float, required=True)
args = ap.parse_args()

try:
    with open(args.ssa, 'r', encoding='utf-8') as f:
        lines = f.readlines()
except OSError as e:
    print(f'ERROR: {e}', file=sys.stderr); sys.exit(2)

out, n = [], 0
for line in lines:
    m = RE.match(line)
    if not m:
        out.append(line); continue
    st = to_sec(m.group(2)) + args.offset
    en = to_sec(m.group(3)) + args.offset
    new_line = m.group(1) + to_ssa(st) + ',' + to_ssa(en) + m.group(4)
    if line.endswith('\n'):
        new_line += '\n'
    out.append(new_line)
    n += 1

with open(args.ssa, 'w', encoding='utf-8') as f:
    f.writelines(out)
print(f'[ssa_shift] {n} dialogues, offset {args.offset:+.3f}s -> {args.ssa}',
      file=sys.stderr)
sys.exit(0)

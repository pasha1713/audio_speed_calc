#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tsp_frag_calc.py — расчёт start_pkt/end_pkt для нарезки TS по _full.txt.
Генерируется автоматически из audio_speed_calc.py (HELPER_SOURCE).

usage:
    tsp_frag_calc.py --full-txt <path> --fps <float>
                     --frame-start <1-based> --frame-end <1-based>
                     [--plan Z|X] [--next-idr-pkt <N>]
                     --out <path>

вывод: одна строка "START_PKT END_PKT" в файл <path>.
"""
import argparse
import re
import sys

LINE_RE = re.compile(
    r"^Фр\.(\d+)\s+PID:0x([0-9A-Fa-f]{4})\s+Пакеты:(\d+)\((\d+)-(\d+)\)\s*$"
)
ALLOWED_START_PIDS = {0x0000, 0x0040, 0x0100, 0x0200, 0x0201}


def parse_full_txt(path):
    frame_map = {}
    all_rows = []
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            m = LINE_RE.match(line)
            if not m:
                continue
            fr = int(m.group(1))
            pid = int(m.group(2), 16)
            pmin = int(m.group(4))
            pmax = int(m.group(5))
            all_rows.append((fr, pid, pmin, pmax))
            if pid == 0x0200:
                if fr not in frame_map:
                    frame_map[fr] = {'min': pmin, 'max': pmax}
                else:
                    frame_map[fr]['min'] = min(frame_map[fr]['min'], pmin)
                    frame_map[fr]['max'] = max(frame_map[fr]['max'], pmax)
    return frame_map, all_rows


def _video_frames(all_rows):
    frames = {}
    for fr, pid, pmin, pmax in all_rows:
        if pid != 0x0200:
            continue
        if fr not in frames:
            frames[fr] = [pmin, pmax]
        else:
            if pmin < frames[fr][0]:
                frames[fr][0] = pmin
            if pmax > frames[fr][1]:
                frames[fr][1] = pmax
    return frames


def calc_start_pkt(frame_start, all_rows):
    """v0.58.10.5: старт = first_video_packet(frame_start)
    минус PAT/PMT/SDT/audio в окне 500 пакетов перед ним.
    GPS (0x0300) в прелюдии не берётся."""
    if frame_start <= 1:
        return 0
    vid = _video_frames(all_rows)
    if frame_start not in vid:
        return 0
    first_vid = vid[frame_start][0]

    prev_max = 0
    for fr, (mn, mx) in vid.items():
        if fr < frame_start and mx > prev_max:
            prev_max = mx

    WINDOW = 500
    lo = max(prev_max + 1, first_vid - WINDOW)
    hi = first_vid
    allowed = {0x0000, 0x0040, 0x0100, 0x0200, 0x0201}
    candidates = []
    for fr, pid, pmin, pmax in all_rows:
        if pid not in allowed:
            continue
        if pmax < lo:
            continue
        if pmin >= hi:
            continue
        cand = max(pmin, lo)
        if cand < hi:
            candidates.append(cand)
    return min(candidates) if candidates else first_vid


def calc_end_pkt(frame_end, all_rows, plan='Z', next_idr_pkt=None):
    """v0.58.10.5: конец = last_video_packet(frame_end)
    плюс PAT/PMT/SDT/audio в окне 500 пакетов после него,
    но не дальше first_video_packet(frame_end+1)."""
    if plan == 'X' and next_idr_pkt is not None and next_idr_pkt > 0:
        return next_idr_pkt - 1
    vid = _video_frames(all_rows)
    if not vid:
        return None
    if frame_end not in vid:
        cands = [fr for fr in vid if fr <= frame_end]
        frame_end = max(cands) if cands else min(vid.keys())
    if frame_end not in vid:
        return None
    last_vid = vid[frame_end][1]

    next_vid_min = None
    for fr, (mn, mx) in vid.items():
        if fr > frame_end:
            if next_vid_min is None or mn < next_vid_min:
                next_vid_min = mn

    WINDOW = 500
    hi = last_vid + WINDOW
    if next_vid_min is not None:
        hi = min(hi, next_vid_min)
    allowed = {0x0000, 0x0040, 0x0100, 0x0200, 0x0201}
    max_end = last_vid
    for fr, pid, pmin, pmax in all_rows:
        if pid not in allowed:
            continue
        if pmax <= last_vid:
            continue
        if pmin >= hi:
            continue
        end_cand = min(pmax, hi - 1)
        if end_cand > max_end:
            max_end = end_cand
    return max_end


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--full-txt', required=True)
    ap.add_argument('--fps', type=float, required=True)
    ap.add_argument('--frame-start', type=int, required=True)
    ap.add_argument('--frame-end', type=int, required=True)
    ap.add_argument('--plan', choices=['Z', 'X'], default='Z')
    ap.add_argument('--next-idr-pkt', type=int, default=None)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    if args.fps <= 0:
        print('ERROR: fps must be > 0', file=sys.stderr)
        sys.exit(3)
    if args.frame_start < 1:
        print('ERROR: frame_start must be >= 1', file=sys.stderr)
        sys.exit(4)
    if args.frame_end < args.frame_start:
        print('ERROR: frame_end < frame_start', file=sys.stderr)
        sys.exit(4)

    try:
        frame_map, all_rows = parse_full_txt(args.full_txt)
    except OSError as e:
        print('ERROR: read _full.txt: %s' % e, file=sys.stderr)
        sys.exit(2)
    if not all_rows:
        print('ERROR: _full.txt empty or unparsable', file=sys.stderr)
        sys.exit(2)
    if not frame_map:
        print('ERROR: no video packets (PID 0x0200) in _full.txt',
              file=sys.stderr)
        sys.exit(3)

    start_pkt = calc_start_pkt(args.frame_start, all_rows)
    end_pkt = calc_end_pkt(args.frame_end, all_rows,
                           args.plan, args.next_idr_pkt)
    if end_pkt is None:
        print('ERROR: end_pkt not found', file=sys.stderr)
        sys.exit(5)
    if start_pkt >= end_pkt:
        print('ERROR: start_pkt (%d) >= end_pkt (%d)'
              % (start_pkt, end_pkt), file=sys.stderr)
        sys.exit(5)

    print('[helper] frame_start=%d frame_end=%d plan=%s '
          'start_pkt=%d end_pkt=%d'
          % (args.frame_start, args.frame_end, args.plan,
             start_pkt, end_pkt), file=sys.stderr)

    try:
        with open(args.out, 'w', encoding='utf-8') as f:
            f.write('%d %d\n' % (start_pkt, end_pkt))
    except OSError as e:
        print('ERROR: write %s: %s' % (args.out, e), file=sys.stderr)
        sys.exit(6)

    sys.exit(0)


if __name__ == '__main__':
    main()

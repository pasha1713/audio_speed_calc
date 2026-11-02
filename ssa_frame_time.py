#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ssa_frame_time.py - frame/time/MSK info per Dialogue."""
import argparse
import os
import re
import sys
from datetime import datetime, timedelta


RE_DIALOGUE = re.compile(
    r'^(Dialogue:\s*\d+,)'
    r'(\d+:\d+:\d+\.\d+),'
    r'(\d+:\d+:\d+\.\d+),'
    r'(.*)$'
)


def hms_to_sec(hms):
    """Tolerant: .cc (2 знака) и .mmm (3 знака)."""
    h, m, rest = hms.split(':')
    s, frac = rest.split('.')
    n = len(frac)
    mult = 100 if n == 2 else (1000 if n == 3 else 10 ** n)
    return int(h) * 3600 + int(m) * 60 + int(s) + int(frac) / float(mult)


def sec_to_hms(t, precision='ms'):
    """HH:MM:SS.cc или HH:MM:SS.mmm."""
    if t < 0:
        t = 0.0
    if precision == 'cc':
        cs_total = int(round(t * 100))
        cc = cs_total % 100
        s_total = cs_total // 100
        s = s_total % 60
        m = (s_total // 60) % 60
        h = s_total // 3600
        return '%02d:%02d:%02d.%02d' % (h, m, s, cc)
    ms_total = int(round(t * 1000))
    ms = ms_total % 1000
    s_total = ms_total // 1000
    s = s_total % 60
    m = (s_total // 60) % 60
    h = s_total // 3600
    return '%02d:%02d:%02d.%03d' % (h, m, s, ms)


def sec_to_ms_str(t):
    """Время для доп. строки Кадр:.../time/ - всегда .3f."""
    return sec_to_hms(t, precision='ms')


def fmt_delta(sec):
    a = abs(sec)
    if a < 60:
        return '%.0fs' % a
    if a < 3600:
        return '%.1fm' % (a / 60.0)
    if a < 86400:
        return '%.1fh' % (a / 3600.0)
    return '%.1fd' % (a / 86400.0)


def _read_text_auto(path):
    try:
        with open(path, 'rb') as f:
            raw = f.read()
    except Exception:
        return None
    for enc in ('utf-8-sig', 'utf-8', 'cp1251'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('latin-1', errors='replace')


def _parse_ssa_dt(ssa_path):
    if not ssa_path or not os.path.isfile(ssa_path):
        return None
    text = _read_text_auto(ssa_path)
    if text is None:
        return None
    for line in text.splitlines():
        if not line.startswith('Dialogue:'):
            continue
        m = re.search(
            r'Дата/Время:\s*'
            r'(\d{4})-(\d{2})-(\d{2})\s+'
            r'(\d{2}):(\d{2}):(\d{2})', line)
        if m:
            try:
                return datetime(*map(int, m.groups()))
            except Exception:
                return None
        break
    return None


def resolve_base_dt(ssa_path, mtime_path, warn_sec):
    mt = None
    if mtime_path and os.path.isfile(mtime_path):
        try:
            mt = datetime.fromtimestamp(os.path.getmtime(mtime_path))
        except Exception:
            pass

    ssa_dt = _parse_ssa_dt(ssa_path)
    if ssa_dt is not None:
        if mt is not None:
            try:
                d = (ssa_dt - mt).total_seconds()
                if abs(d) > warn_sec:
                    print('[ssa_frame_time] SSA? ssa=%s mtime=%s d=%+.0fs'
                          % (ssa_dt.isoformat(),
                             mt.isoformat(), d), file=sys.stderr)
                    return ssa_dt, '[ssa?]', 'd=' + fmt_delta(d)
                print('[ssa_frame_time] SSA ssa=%s mtime=%s d=%+.0fs'
                      % (ssa_dt.isoformat(), mt.isoformat(), d),
                      file=sys.stderr)
            except Exception:
                pass
        return ssa_dt, '[ssa]', ''

    if mt is not None:
        if mt.year < 2020 or mt.year > 2030:
            print('[ssa_frame_time] MTIME? year=%d' % mt.year,
                  file=sys.stderr)
            return mt, '[mtime?]', 'year=%d' % mt.year
        return mt, '[mtime]', ''

    if mtime_path:
        m = re.search(r'(\d{14})', os.path.basename(mtime_path))
        if m:
            s = m.group(1)
            try:
                return (datetime(int(s[0:4]), int(s[4:6]),
                                  int(s[6:8]), int(s[8:10]),
                                  int(s[10:12]), int(s[12:14])),
                        '[name]', '')
            except Exception:
                pass

    return None, '[-]', ''


def build_tag(tag, reason):
    if tag.endswith('?]') and reason:
        return tag[:-1] + ' ' + reason + ']'
    return tag


def detect_eol(raw):
    if '\r\n' in raw:
        return '\r\n'
    if '\r' in raw:
        return '\r'
    return '\n'


def op_add_info(ssa_path, fps, t_start_eff, time_correction,
                 mtime_path, warn_sec, time_precision,
                 frame_end_frac=0.97, frame_start_eff_arg=0,
                 ssa_full_path='', dry_run=False):
    with open(ssa_path, 'r', encoding='utf-8', newline='') as f:
        raw = f.read()
    eol = detect_eol(raw)
    lines = raw.split(eol)

    # [fix14] base_dt приоритет: --ssa-full > --ssa > mtime > name
    base_dt = None
    src_tag = '[-]'
    src_reason = ''
    if ssa_full_path and os.path.isfile(ssa_full_path):
        _bd_full = _parse_ssa_dt(ssa_full_path)
        if _bd_full is not None:
            base_dt = _bd_full
            src_tag = '[ssa-full]'
            src_reason = ''
            print('[ssa_frame_time] base_dt: --ssa-full %s'
                  % ssa_full_path, file=sys.stderr)
        else:
            print('[ssa_frame_time] WARN: --ssa-full %s не содержит '
                  'Дата/Время, fallback на --ssa'
                  % ssa_full_path, file=sys.stderr)
    if base_dt is None:
        base_dt, src_tag, src_reason = resolve_base_dt(
            ssa_path, mtime_path, warn_sec)
    tag_full = build_tag(src_tag, src_reason)
    print('[ssa_frame_time] base_dt=%s tag=%s precision=%s'
          % (base_dt.isoformat() if base_dt else None,
             tag_full, time_precision), file=sys.stderr)

    out = []
    n_added = 0
    n_total = 0

    for line in lines:
        m = RE_DIALOGUE.match(line)
        if not m:
            out.append(line)
            continue
        n_total += 1
        start_str, end_str, tail = m.group(2), m.group(3), m.group(4)

        if '\\NКадр:' in tail:
            idx = tail.rfind('\\NКадр:')
            tail = tail[:idx]

        t_start = hms_to_sec(start_str)
        t_end = hms_to_sec(end_str)

        # [PATCH-fix11] Start — frame-snap (как fix9/fix10).
        # End — две стратегии в зависимости от входной длительности:
        #
        # 1) SPLIT (input_dur_frames <= 1, кадр на Dialogue):
        #    End = Start + (round(1000/fps) - 1) мс
        #    -> единая длительность 32 мс для 30 fps.
        #    Зазоры между Dialogue 1/2 мс чередуются.
        #
        # 2) FULL (input_dur_frames > 1, N кадров на Dialogue):
        #    End = frame-snap(t_end) - 1 мс (как fix7).
        #    -> сохраняет исходную длительность N/fps секунд
        #    (например, 1 секунда = 999 мс, а не 32).
        #    Без этого fix8-unified ломал полный .ssa.
        frag_frame     = int(round(t_start * fps))
        frag_end_frame = int(round(t_end   * fps))
        # [fix13-msk] src_frame = frame_start_eff + frag_frame
        # Привязка MSK к реальному кадру видео, а не
        # round(t_start_eff*fps). Для фрагмента frame_start_eff
        # приходит из _compute_fragment_info (floor(t_start_eff*fps)+2).
        # Для full (t_start_eff=0, frame_start_eff_arg=0) — fallback
        # на старую формулу.
        if frame_start_eff_arg > 0:
            src_frame = frame_start_eff_arg + frag_frame
        else:
            src_frame = int(round((t_start + t_start_eff) * fps))

        t_start_exact = frag_frame / fps
        _end_sec_raw  = frag_end_frame / fps

        if time_precision == 'ms' and frag_end_frame > 0:
            _start_ms         = int(round(t_start_exact * 1000))
            _next_start_ms    = int(round(_end_sec_raw * 1000))
            _input_dur_frames = frag_end_frame - frag_frame
            if _input_dur_frames < 1:
                _input_dur_frames = 1

            # [fix11] Единая формула для SPLIT (N=1) и FULL (N>1):
            #   dur_ms = round((1000/fps) * N * frac)
            # SPLIT 30fps frac=0.97 -> 32 мс (gap 1 мс).
            # SPLIT 30fps frac=1.00 -> 33 мс (наезд 1 точка).
            # FULL  30fps 1с frac=0.97 -> 970 мс (gap 30 мс).
            # FULL  30fps 1с frac=1.00 -> 1000 мс (касание,
            #                          gap 0 мс).
            _full_dur_ms = (1000.0 / fps) * _input_dur_frames \
                * frame_end_frac
            _dur_ms = int(round(_full_dur_ms))
            if _dur_ms < 1:
                _dur_ms = 1
            _end_ms = _start_ms + _dur_ms

            # clamp
            if frame_end_frac < 0.9999:
                # gap >= 1 мс: не наезжаем на следующий Dialogue
                if _end_ms >= _next_start_ms:
                    _end_ms = _next_start_ms - 1
            else:
                # frac ~ 1.0: касание D(N).end == D(N+1).start
                # допустимо (устраняет "мёртвую зону" на стыке,
                # из-за которой некоторые плееры "двоят" старый
                # и новый субтитр), но не больше — иначе оба
                # видны дольше 1 точки.
                if _end_ms > _next_start_ms:
                    _end_ms = _next_start_ms
            if _end_ms < _start_ms:
                _end_ms = _start_ms
            t_end_exact = _end_ms / 1000.0
        else:
            t_end_exact = _end_sec_raw

        new_start = sec_to_hms(t_start_exact, precision=time_precision)
        new_end   = sec_to_hms(t_end_exact,   precision=time_precision)

        t_frag = frag_frame / fps
        time_str = sec_to_ms_str(t_frag)

        if base_dt is not None:
            t_src = src_frame / fps
            dt = base_dt + timedelta(
                seconds=t_src + time_correction)
            msk_str = (dt.strftime('%d.%m.%Y %H:%M:%S.')
                       + '%03d MSK' % (dt.microsecond // 1000))
        else:
            msk_str = '[-]'

        if t_start_eff > 0.001:
            prefix = ('\\NКадр:%d(в исходнике:%d кадр) / %s / %s %s'
                      % (frag_frame, src_frame, time_str,
                         msk_str, tag_full))
        else:
            prefix = ('\\NКадр:%d / %s / %s %s'
                      % (src_frame, time_str, msk_str, tag_full))

        new_line = (m.group(1) + new_start + ',' + new_end
                    + ',' + tail + prefix)
        out.append(new_line)
        n_added += 1

    if dry_run:
        print('[ssa_frame_time] DRY: %d/%d precision=%s'
              % (n_added, n_total, time_precision), file=sys.stderr)
        return n_added, n_total

    with open(ssa_path, 'w', encoding='utf-8', newline='') as f:
        f.write(eol.join(out))
    print('[ssa_frame_time] add-info: %d/%d -> %s (precision=%s)'
          % (n_added, n_total, ssa_path, time_precision),
          file=sys.stderr)
    return n_added, n_total


def op_apply_style(in_path, out_path):
    with open(in_path, 'r', encoding='utf-8', newline='') as f:
        raw = f.read()
    eol = detect_eol(raw)
    lines = raw.split(eol)

    out = []
    n_styles = 0
    for line in lines:
        if line.startswith('Style:'):
            parts = line.split(',')
            if len(parts) >= 16:
                try:
                    parts[12] = '5'
                    parts[15] = '3'
                    n_styles += 1
                except IndexError:
                    pass
            out.append(','.join(parts))
        else:
            out.append(line)

    with open(out_path, 'w', encoding='utf-8', newline='') as f:
        f.write(eol.join(out))
    print('[ssa_frame_time] apply-style: %d Style'
          % n_styles, file=sys.stderr)
    return n_styles


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='op', required=True)

    a1 = sub.add_parser('add-info')
    a1.add_argument('--ssa', required=True)
    a1.add_argument('--ts-path', default='')
    a1.add_argument('--fps', type=float, required=True)
    a1.add_argument('--t-start-eff', type=float, default=0.0)
    a1.add_argument('--time-correction', type=float, default=0.0)
    a1.add_argument('--warn-sec', type=float, default=300.0)
    a1.add_argument('--time-precision', choices=('cc', 'ms'),
                    default='ms')
    a1.add_argument('--frame-end-frac', type=float, default=0.97)
    a1.add_argument('--frame-start-eff', type=int, default=0,
                    dest='frame_start_eff_arg')
    a1.add_argument('--ssa-full', default='',
                    dest='ssa_full_arg')
    a1.add_argument('--dry-run', action='store_true')
    a1.add_argument('--base-dt', default='')      # back-compat
    a1.add_argument('--source-tag', default='')   # back-compat

    a2 = sub.add_parser('apply-style')
    a2.add_argument('--in', dest='in_path', required=True)
    a2.add_argument('--out', dest='out_path', required=True)

    args = ap.parse_args()

    if args.op == 'add-info':
        if not os.path.isfile(args.ssa):
            print('ERROR: ssa not found: %s' % args.ssa,
                  file=sys.stderr)
            return 2
        if args.fps <= 0:
            print('ERROR: fps <= 0', file=sys.stderr)
            return 3
        op_add_info(args.ssa, args.fps, args.t_start_eff,
                     args.time_correction, args.ts_path,
                     args.warn_sec, args.time_precision,
                     frame_end_frac=args.frame_end_frac,
                     frame_start_eff_arg=args.frame_start_eff_arg,
                     ssa_full_path=args.ssa_full_arg,
                     dry_run=args.dry_run)
        return 0

    if args.op == 'apply-style':
        if not os.path.isfile(args.in_path):
            print('ERROR: ssa not found: %s' % args.in_path,
                  file=sys.stderr)
            return 2
        op_apply_style(args.in_path, args.out_path)
        return 0

    return 1


if __name__ == '__main__':
    sys.exit(main())

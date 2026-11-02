#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patch_otchet_tolerant.py — tolerant-парсер SSA.

Режимы:
  --ssa FILE       починить один .ssa (in-place)
  --dir DIR        починить все *.ssa в папке
  --rename-ts-ssa  в --dir: *.TS.ssa -> *.ssa
"""
import argparse, os, re, sys

DIALOGUE_RE = re.compile(r'(?=Dialogue:\s*0,)')


def fix_ssa_text(text):
    warnings = []
    if text.startswith('\ufeff'):
        text = text[1:]
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'^ScriptType\s*=\s*v4\.00',
                  'ScriptType: v4.00', text, flags=re.MULTILINE)
    out_lines = []
    n_dlg = 0
    for line in text.split('\n'):
        cnt = line.count('Dialogue:')
        if cnt > 1:
            pieces = DIALOGUE_RE.split(line)
            buf = ''
            for p in pieces:
                p = p.strip()
                if not p:
                    continue
                if p.startswith('Dialogue:'):
                    if buf:
                        out_lines.append(buf); buf = ''
                    out_lines.append(p); n_dlg += 1
                else:
                    if out_lines and out_lines[-1].startswith('Dialogue:'):
                        out_lines[-1] = out_lines[-1] + p
                    else:
                        buf = p
            if buf:
                out_lines.append(buf)
        elif cnt == 1 and not line.startswith('Dialogue:'):
            idx = line.find('Dialogue:')
            head = line[:idx].rstrip()
            tail = line[idx:].strip()
            if head and out_lines and out_lines[-1].startswith('Dialogue:'):
                out_lines[-1] = out_lines[-1] + head
            elif head:
                out_lines.append(head)
            out_lines.append(tail); n_dlg += 1
        elif cnt == 1:
            out_lines.append(line); n_dlg += 1
        else:
            out_lines.append(line)
    fixed = '\r\n'.join(out_lines)
    fixed = re.sub(r'(\r\n){3,}', '\r\n\r\n', fixed)
    if not fixed.endswith('\r\n'):
        fixed += '\r\n'
    if '[Events]' not in fixed:
        warnings.append('[Events] not found')
    return fixed, n_dlg, warnings


def fix_ssa_file(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
    except OSError as e:
        print('[patch_otchet] READ FAIL %s: %s' % (path, e),
              file=sys.stderr)
        return False, 0
    if not text.strip():
        print('[patch_otchet] EMPTY: %s' % path, file=sys.stderr)
        return False, 0
    fixed, n, warns = fix_ssa_text(text)
    for w in warns:
        print('[patch_otchet] WARN %s: %s' % (path, w), file=sys.stderr)
    try:
        with open(path, 'w', encoding='utf-8', newline='') as f:
            f.write(fixed)
    except OSError as e:
        print('[patch_otchet] WRITE FAIL %s: %s' % (path, e),
              file=sys.stderr)
        return False, n
    ok = (n > 0) and ('[Events]' in fixed)
    print('[patch_otchet] %s: %d Dialogue, %d bytes, ok=%s'
          % (os.path.basename(path), n, len(fixed), ok), file=sys.stderr)
    return ok, n


def rename_ts_ssa(dirpath):
    if not os.path.isdir(dirpath):
        return 0
    n = 0
    for name in os.listdir(dirpath):
        if not name.lower().endswith('.ts.ssa'):
            continue
        src = os.path.join(dirpath, name)
        base = name[:-len('.TS.ssa')]
        dst = os.path.join(dirpath, base + '.ssa')
        if os.path.exists(dst):
            print('[patch_otchet] SKIP (target exists): %s' % dst,
                  file=sys.stderr)
            continue
        try:
            os.replace(src, dst)
            print('[patch_otchet] rename: %s -> %s'
                  % (name, os.path.basename(dst)), file=sys.stderr)
            n += 1
        except OSError as e:
            print('[patch_otchet] RENAME FAIL %s: %s' % (src, e),
                  file=sys.stderr)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ssa')
    ap.add_argument('--dir')
    ap.add_argument('--rename-ts-ssa', action='store_true')
    a = ap.parse_args()
    if a.ssa:
        ok, n = fix_ssa_file(a.ssa)
        return 0 if (ok and n > 0) else 1
    if a.dir:
        if a.rename_ts_ssa:
            print('[patch_otchet] renamed: %d' % rename_ts_ssa(a.dir),
                  file=sys.stderr)
        total = fixed = 0
        for name in os.listdir(a.dir):
            if not name.lower().endswith('.ssa'):
                continue
            total += 1
            ok, _n = fix_ssa_file(os.path.join(a.dir, name))
            if ok:
                fixed += 1
        print('[patch_otchet] dir %s: %d/%d fixed'
              % (a.dir, fixed, total), file=sys.stderr)
        return 0 if total > 0 and fixed == total else 1
    ap.print_help(sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())

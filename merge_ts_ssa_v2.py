#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
merge_ts_ssa_v2.py — каждый .ts -> свой .mp4 с mov_text (v0.58.13-mp4-v2)

ПРАВИЛА
=======
- Каждый .ts -> СВОЙ <base>.mp4 (без конкатенации).
- Выход: <work_root>/mp4/<series>/<base>.mp4
  где work_root = dirname(series_dir), series = 2k | 4k.
- .ssa-источник: <series>/<base>.ssa (финальный) ->
  fallback <series>/ssa/<base>.ssa (промежуточный).
- sidecar .ssa в mp4/ НЕ копируется.
- mp4_full=True (default): полные + фрагменты.
  mp4_full=False: только фрагменты (_\\d+\\.\\d+s.TS).
- ffmpeg:
      -i ts -i ssa
      -map 0:v:0 -map 0:a? -map 1:s:0
      -c copy -c:s mov_text
      -metadata:s:s:0 language=rus -disposition:s:0 default
      -bsf:a aac_adtstoasc -movflags +faststart
  При сбое mov_text -> mp4 без субтитров (fallback).
- Идентификация фрагмента: имя оканчивается на _\\d+\\.\\d+s.TS
  (2k и 4k — суффикс A в base, не в конце).

ИНТЕРФЕЙС
=========
find_ts_list(folder)            -> list[str]
_find_ssa_for(ts_path, series_dir) -> str|None
remux_all(folder, mp4_full=True, log_cb=None)
    -> list[{"ts", "mp4", "rc", "note"}]
"""

import os
import re
import shutil
import subprocess

VERSION = "0.58.13-mp4-v2"

FRAG_RE = re.compile(r"_\d+\.\d+s\.TS$", re.IGNORECASE)


# ==============================================================
# ffmpeg
# ==============================================================
def _find_ffmpeg():
    p = shutil.which("ffmpeg")
    if p:
        return p
    here = os.path.dirname(os.path.abspath(__file__))
    for c in (os.path.join(here, "ffmpeg.exe"),
              os.path.join(here, "ffmpeg"),
              os.path.join(here, "bin", "ffmpeg.exe"),
              os.path.join(here, "bin", "ffmpeg")):
        if os.path.isfile(c):
            return c
    return None


def _run(cmd, timeout=1800):
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=timeout,
            encoding="utf-8", errors="replace")
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except FileNotFoundError as e:
        return -2, "", str(e)
    except Exception as e:
        return -3, "", str(e)


# ==============================================================
# paths
# ==============================================================
def find_ts_list(folder):
    if not os.path.isdir(folder):
        return []
    out = []
    for name in os.listdir(folder):
        if name.upper().endswith(".TS"):
            full = os.path.join(folder, name)
            if os.path.isfile(full):
                out.append(full)
    out.sort()
    return out


def is_fragment(ts_name):
    return bool(FRAG_RE.search(ts_name))


def _detect_series(folder):
    bd = os.path.basename(os.path.abspath(folder)).lower()
    if bd in ("2k", "4k"):
        return (bd, os.path.abspath(folder),
                os.path.dirname(os.path.abspath(folder)))
    # fallback: по первому .ts
    ts_list = find_ts_list(folder)
    series = "2k"
    if ts_list and ts_list[0].upper().endswith("A.TS"):
        series = "4k"
    return (series, os.path.abspath(folder),
            os.path.dirname(os.path.abspath(folder)))


def find_ssa_for(ts_path, series_dir):
    ts_name = os.path.basename(ts_path)
    base = os.path.splitext(ts_name)[0]
    cands = [
        os.path.join(series_dir, base + ".ssa"),
        os.path.join(series_dir, "ssa", base + ".ssa"),
    ]
    for c in cands:
        if os.path.isfile(c) and os.path.getsize(c) > 0:
            return c
    return None


_find_ssa_for = find_ssa_for  # back-compat


def _ensure_dir(p):
    try:
        os.makedirs(p, exist_ok=True)
    except OSError:
        pass


# ==============================================================
# remux
# ==============================================================
def remux_one(ffmpeg, ts_path, ssa_path, mp4_out, log_cb=None):
    """ts (+ssa) -> mp4. mov_text при наличии .ssa; fallback без субтитров.

    Возвращает dict {rc, mp4, note}.
    """
    if not ffmpeg:
        return {"rc": -1, "mp4": None, "note": "ffmpeg not found"}
    _ensure_dir(os.path.dirname(mp4_out))

    if ssa_path:
        cmd = [
            ffmpeg, "-y", "-nostdin", "-hide_banner",
            "-i", ts_path,
            "-i", ssa_path,
            "-map", "0:v:0",
            "-map", "0:a?",
            "-map", "1:s:0",
            "-c", "copy",
            "-c:s", "mov_text",
            "-metadata:s:s:0", "language=rus",
            "-disposition:s:0", "default",
            "-bsf:a", "aac_adtstoasc",
            "-movflags", "+faststart",
            mp4_out,
        ]
        rc, _out, err = _run(cmd)
        if rc == 0 and os.path.isfile(mp4_out) \
                and os.path.getsize(mp4_out) > 0:
            return {"rc": 0, "mp4": mp4_out, "note": "mov_text"}
        if log_cb:
            _last = (err.strip().splitlines()[-1]
                     if err.strip() else "?")
            log_cb("    [mov_text FAIL] " + _last)
        try:
            if os.path.isfile(mp4_out):
                os.remove(mp4_out)
        except OSError:
            pass

    cmd = [
        ffmpeg, "-y", "-nostdin", "-hide_banner",
        "-i", ts_path,
        "-map", "0:v:0",
        "-map", "0:a?",
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart",
        mp4_out,
    ]
    rc, _out, err = _run(cmd)
    if rc == 0 and os.path.isfile(mp4_out) \
            and os.path.getsize(mp4_out) > 0:
        note = "no subs (fallback)" if ssa_path else "no subs"
        return {"rc": 0, "mp4": mp4_out, "note": note}
    _last = err.strip().splitlines()[-1] if err.strip() else "?"
    return {"rc": rc, "mp4": None, "note": _last}


def remux_all(folder, mp4_full=True, log_cb=None):
    """folder = 2k/ или 4k/. Возвращает list[{ts, mp4, rc, note}]."""
    series, series_dir, work_root = _detect_series(folder)
    mp4_dir = os.path.join(work_root, "mp4", series)
    _ensure_dir(mp4_dir)
    ffmpeg = _find_ffmpeg()

    if log_cb:
        log_cb("=" * 64)
        log_cb("merge_ts_ssa_v2 " + VERSION)
        log_cb("folder:   " + series_dir)
        log_cb("series:   " + series)
        log_cb("mp4_dir:  " + mp4_dir)
        log_cb("ffmpeg:   " + (ffmpeg or "NOT FOUND"))
        log_cb("mp4_full: " + str(mp4_full))
        log_cb("=" * 64)

    ts_list = find_ts_list(series_dir)
    if log_cb:
        log_cb("ts files: %d" % len(ts_list))

    results = []
    for ts_path in ts_list:
        ts_name = os.path.basename(ts_path)
        base = os.path.splitext(ts_name)[0]
        is_frag = is_fragment(ts_name)

        if not mp4_full and not is_frag:
            if log_cb:
                log_cb("[SKIP] full .ts (mp4_full=False): " + ts_name)
            results.append({"ts": ts_path, "mp4": None, "rc": 0,
                            "note": "skip (mp4_full=False)"})
            continue

        ssa_path = find_ssa_for(ts_path, series_dir)
        mp4_out = os.path.join(mp4_dir, base + ".mp4")
        if log_cb:
            log_cb("")
            log_cb("[%s] %s" % ("frag" if is_frag else "full", ts_name))
            log_cb("  ssa: " + (ssa_path or "НЕТ"))
            log_cb("  mp4: " + mp4_out)

        res = remux_one(ffmpeg, ts_path, ssa_path, mp4_out, log_cb)
        if log_cb:
            log_cb("  rc=%d  %s" % (res["rc"], res.get("note", "")))
        results.append({"ts": ts_path, "mp4": res["mp4"],
                        "rc": res["rc"], "note": res.get("note", "")})

    ok = sum(1 for r in results if r["rc"] == 0)
    fail = len(results) - ok
    if log_cb:
        log_cb("")
        log_cb("=" * 64)
        log_cb("ИТОГО: OK=%d, FAIL=%d" % (ok, fail))
        log_cb("mp4_dir: " + mp4_dir)
        log_cb("=" * 64)

    return results
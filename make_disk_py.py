#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_disk_py.py — Make Disk на чистом Python (v0.58.13-py3-full)

Полная перезапись (замена .cmd-пайплайна и частичная замена
ssa_frame_time).

Цепочка:
    [1] copy_full_ts      full .ts в <series>/
    [2] exiftool          exiftool_orig.txt
    [3] otchet            ssa + _full.txt + console
    [4] patch_ssa         tolerant-парсер
    [5] rename            .TS.ssa -> .ssa
    [6] ssa_shift full    full_ssa_shift_sec
    [7] base_dt_video     filename + pts_first_idr (или override)
    [8] split full .ssa   N=1 кадр на Dialogue, скорость+MSK
    [9] apply-style       top-left

    [frag pipeline]
    [10] fragment_calc    start_pkt/end_pkt
    [11] tsp              <ts>_<X.XXX>s.TS
    [12] exiftool frag
    [13] otchet frag
    [14] patch_ssa frag
    [15] rename frag
    [16] ssa_shift frag   snap_abs
    [17] split frag .ssa  N=1 кадр, frame_start_in_full=frame_start_eff-1
    [18] apply-style

    [19-21] tsanalyze/MediaInfo/GPX (full+frag)

CLI:
    python make_disk_py.py <ts_file> [--config cfg.json]
                          [--base-dt "YYYY-MM-DDTHH:MM:SS.mmm"]
                          [--dump-meta] [--only-ssa]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta

VERSION = "0.58.13-py3-full"
LINE = "=" * 72

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ==============================================================
# LOG
# ==============================================================
_LOG_FILE = None


def _open_log(path):
    global _LOG_FILE
    try:
        _LOG_FILE = open(path, "w", encoding="utf-8", newline="\n")
    except OSError as e:
        print(f"[make_disk_py] log: {e}", file=sys.stderr)
        _LOG_FILE = None


def _close_log():
    global _LOG_FILE
    if _LOG_FILE is not None:
        try:
            _LOG_FILE.close()
        except Exception:
            pass
        _LOG_FILE = None


def _log(msg, level="I"):
    prefix = {"I": "  ", "W": "  [WARN] ", "E": "  [FAIL] ",
              "S": "  [SKIP] ", "H": ""}.get(level, "  ")
    line = prefix + msg
    try:
        print(line, flush=True)
    except Exception:
        try:
            sys.stdout.buffer.write(
                (line + "\n").encode("utf-8", errors="replace"))
            sys.stdout.buffer.flush()
        except Exception:
            pass
    if _LOG_FILE is not None:
        try:
            _LOG_FILE.write(line + "\n")
            _LOG_FILE.flush()
        except Exception:
            pass


# ==============================================================
# SUBPROCESS
# ==============================================================
def _run(cmd, timeout=600, out_path=None, log_prefix="",
         env_extra=None):
    if log_prefix:
        _log(f"[cmd] {log_prefix}")
    _log(f"[cmd] {' '.join(str(x) for x in cmd)}")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    if env_extra:
        env.update(env_extra)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=timeout,
            encoding="utf-8", errors="replace", env=env,
        )
    except FileNotFoundError as e:
        _log(f"не найден: {cmd[0]} ({e})", "E")
        return -1, "", str(e)
    except subprocess.TimeoutExpired as e:
        _log(f"timeout {timeout}s: {e}", "E")
        return -2, "", str(e)
    except Exception as e:
        _log(f"ошибка subprocess: {e}", "E")
        return -3, "", str(e)

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    if out_path:
        try:
            with open(out_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(stdout)
        except OSError as e:
            _log(f"write {out_path}: {e}", "W")

    if proc.returncode != 0 and stderr.strip():
        for ln in stderr.splitlines()[:20]:
            _log(f"  stderr: {ln}", "W")

    return proc.returncode, stdout, stderr


def _find_exec(*names, extra_dirs=()):
    suffix = ".exe" if sys.platform == "win32" else ""
    for base in extra_dirs:
        if not base:
            continue
        for n in names:
            for cand in (n, n + suffix):
                p = os.path.join(base, cand)
                if os.path.isfile(p):
                    return p
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


# ==============================================================
# FFPROBE
# ==============================================================
def _ffprobe(ffprobe_path, ts_path, entries, extra_args=None):
    if not ffprobe_path:
        return {}
    cmd = [ffprobe_path, "-v", "error",
           "-show_entries", entries,
           "-of", "default=noprint_wrappers=1"]
    if extra_args:
        cmd += extra_args
    cmd.append(ts_path)
    rc, out, _ = _run(cmd, timeout=60)
    if rc != 0:
        return {}
    data = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip()
    return data


def _ffprobe_creation_utc(ffprobe_path, ts_path):
    d = _ffprobe(ffprobe_path, ts_path, "format_tags=creation_time")
    s = d.get("TAG:creation_time", "")
    if not s:
        return None
    s = s.rstrip("Z").split(".")[0]
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _ffprobe_fps(ffprobe_path, ts_path, fallback=30.0):
    d = _ffprobe(
        ffprobe_path, ts_path,
        "stream=avg_frame_rate,r_frame_rate,has_b_frames")
    for key in ("avg_frame_rate", "r_frame_rate"):
        s = d.get(key, "")
        if "/" in s:
            a, b = s.split("/")
            try:
                v = float(a) / float(b)
                if v > 0:
                    return v, int(d.get("has_b_frames", "0") or 0)
            except Exception:
                pass
    return fallback, 0


def _ffprobe_pts_first_idr(ffprobe_path, ts_path, max_scan_sec=10.0):
    """pts_time первого I-кадра в .ts.

    ВАЖНО: -read_intervals '10.000' означает 'читать С 10-й секунды',
    а '%10.000' — 'первые 10 секунд'. Без % pts_first_idr уезжает
    на ~10с вперёд (было 10.033 вместо 0.233).
    """
    if not ffprobe_path:
        return None
    cmd = [ffprobe_path, "-v", "error",
           "-select_streams", "v:0",
           "-show_entries", "frame=pts_time,pict_type",
           "-of", "csv=p=0",
           "-read_intervals", "%%%.3f" % max_scan_sec,
           ts_path]
    rc, out, _ = _run(cmd, timeout=60)
    if rc != 0:
        return None
    for line in out.splitlines():
        parts = line.strip().split(",")
        if len(parts) < 2:
            continue
        try:
            pts = float(parts[0])
            ptype = parts[1].strip().upper()
        except ValueError:
            continue
        if ptype == "I" and pts >= 0:
            return pts
    return None


def _ffprobe_video_start(ffprobe_path, ts_path):
    """pts первого пакета ВИДЕО (v:0). У нас: 0.233.

    format=start_time = min(PTS) по всем потокам (0.033 у аудио).
    stream=start_time для v:0 — это уже видео (0.233).
    """
    if not ffprobe_path:
        return None
    cmd = [ffprobe_path, "-v", "error",
           "-select_streams", "v:0",
           "-show_entries", "stream=start_time",
           "-of", "default=noprint_wrappers=1",
           ts_path]
    rc, out, _ = _run(cmd, timeout=30)
    if rc != 0:
        return None
    for line in out.splitlines():
        if line.startswith("start_time="):
            s = line.split("=", 1)[1].strip()
            try:
                return float(s)
            except ValueError:
                return None
    return None


def _ffprobe_audio_start(ffprobe_path, ts_path):
    """pts первого пакета АУДИО (a:0). У нас: 0.033."""
    if not ffprobe_path:
        return None
    cmd = [ffprobe_path, "-v", "error",
           "-select_streams", "a:0",
           "-show_entries", "stream=start_time",
           "-of", "default=noprint_wrappers=1",
           ts_path]
    rc, out, _ = _run(cmd, timeout=30)
    if rc != 0:
        return None
    for line in out.splitlines():
        if line.startswith("start_time="):
            s = line.split("=", 1)[1].strip()
            try:
                return float(s)
            except ValueError:
                return None
    return None


def _dt_from_iso(s):
    if not s:
        return None
    s2 = s.strip().replace(" ", "T")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s2, fmt)
        except ValueError:
            continue
    return None


def _dt_from_filename_14(ts_path):
    m = re.search(r"(\d{14})", os.path.basename(ts_path))
    if not m:
        return None
    s = m.group(1)
    try:
        return datetime(int(s[0:4]), int(s[4:6]), int(s[6:8]),
                        int(s[8:10]), int(s[10:12]), int(s[12:14]))
    except ValueError:
        return None


# ==============================================================
# PATHS
# ==============================================================
def _detect_series_dir(ts_file):
    """Возвращает (series, series_dir, work_root).

    Если ts_file уже в 2k/4k — series_dir = родитель, work_root =
    родитель родителя.
    Иначе — series_dir = <ts_dir>/<series>, work_root = ts_dir.
    """
    ts_file = os.path.abspath(ts_file)
    ts_dir = os.path.dirname(ts_file)
    bd = os.path.basename(ts_dir).lower()
    ts_name = os.path.basename(ts_file)
    if bd in ("2k", "4k"):
        return bd, ts_dir, os.path.dirname(ts_dir)
    series = "4k" if ts_name.upper().endswith("A.TS") else "2k"
    return series, os.path.join(ts_dir, series), ts_dir


def _full_txt_path(ts_path, series_dir):
    """otchet создаёт <ts_name>_full.txt в <series_dir>/txt/."""
    ts_name = os.path.basename(ts_path)
    return os.path.join(series_dir, "txt", ts_name + "_full.txt")


def _first_gps_frame_in_full_txt(full_txt_path):
    if not os.path.isfile(full_txt_path):
        return None
    rx = re.compile(r"^Фр\.(\d+)\s+PID:0x0300", re.MULTILINE)
    try:
        with open(full_txt_path, "r",
                  encoding="utf-8", errors="replace") as f:
            txt = f.read()
    except OSError:
        return None
    hits = [int(m.group(1)) for m in rx.finditer(txt)]
    return min(hits) if hits else None


# ==============================================================
# base_dt_video
# ==============================================================
def compute_base_dt_video(ts_path, ffprobe_path, series_dir,
                           full_ssa_path=None, full_fps=30.0,
                           override_iso=None,
                           base_dt_offset_sec=0.0):
    """Возвращает (base_dt_video, source_str).

    Приоритет:
      0. override_iso (config/CLI)
      1. ffprobe creation_time                    (MP4/MOV)
      2. filename + video_start + offset_sec      <- основной для .ts
         (video_start = pts первого пакета v:0, OSD кадра 0)
      3. filename + pts_first_idr + offset_sec
      4. filename + offset_sec                    (грубо, +-1с)
      5. gps_first - N0/fps                       (без filename)
      6. gps_first                                (последний fallback)

    mtime убран — это время копирования файла, а не кадра 0.

    base_dt_offset_sec: ручная поправка (msk_base_dt_offset_sec),
    если OSD всё-таки не совпал. Применяется только к вариантам,
    где используется filename.
    """
    _off = float(base_dt_offset_sec or 0.0)

    dt_override = _dt_from_iso(override_iso)
    if dt_override is not None:
        return dt_override, "override (config/CLI)"

    ct = _ffprobe_creation_utc(ffprobe_path, ts_path)
    if ct is not None:
        return ct, "ffprobe creation_time"

    dt_name = _dt_from_filename_14(ts_path)
    video_start = _ffprobe_video_start(ffprobe_path, ts_path)
    pts_idr = _ffprobe_pts_first_idr(ffprobe_path, ts_path)

    if dt_name is not None and video_start is not None:
        return (dt_name + timedelta(seconds=video_start + _off),
                "filename + video_start (%.3fs)" % video_start)

    if dt_name is not None and pts_idr is not None:
        return (dt_name + timedelta(seconds=pts_idr + _off),
                "filename + pts_first_idr (%.3fs)" % pts_idr)

    if dt_name is not None:
        return (dt_name + timedelta(seconds=_off),
                "filename (+%.3fs)" % _off)

    full_txt = _full_txt_path(ts_path, series_dir)
    n0 = _first_gps_frame_in_full_txt(full_txt)
    gps_first = None
    if full_ssa_path and os.path.isfile(full_ssa_path):
        doc = _parse_ssa(full_ssa_path)
        if doc and doc["dialogues"]:
            gps_first = doc["dialogues"][0].get("gps_dt")
    if gps_first and n0 is not None and full_fps > 0:
        return (gps_first - timedelta(seconds=n0 / full_fps),
                "gps_first - N0/fps (N0=%d)" % n0)
    if gps_first:
        return gps_first, "gps_first (без N0)"

    return None, "not_found"


# ==============================================================
# SSA parsing
# ==============================================================
RE_DIALOGUE = re.compile(
    r"^(Dialogue:\s*\d+,)"
    r"(\d+:\d+:\d+\.\d+),"
    r"(\d+:\d+:\d+\.\d+),"
    r"(.*)$"
)
RE_DATE_TIME = re.compile(
    r"Дата/Время:\s*(\d{4})-(\d{2})-(\d{2})\s+"
    r"(\d{2}):(\d{2}):(\d{2})"
)


def _hms_to_sec(hms):
    h, m, rest = hms.split(":")
    if "." in rest:
        s, frac = rest.split(".")
        n = len(frac)
        mult = 100 if n == 2 else (1000 if n == 3 else 10 ** n)
        return (int(h) * 3600 + int(m) * 60 + int(s)
                + int(frac) / float(mult))
    return int(h) * 3600 + int(m) * 60 + int(rest)


def _sec_to_hms(t, precision="ms"):
    if t < 0:
        t = 0.0
    if precision == "cc":
        cs = int(round(t * 100))
        return "%02d:%02d:%02d.%02d" % (
            cs // 360000, (cs // 6000) % 60, (cs // 100) % 60, cs % 100)
    ms = int(round(t * 1000))
    return "%02d:%02d:%02d.%03d" % (
        ms // 3600000, (ms // 60000) % 60, (ms // 1000) % 60, ms % 1000)


def _parse_ssa(path):
    if not os.path.isfile(path):
        return None
    # py10: пробуем utf-8-sig, потом cp1251
    raw = None
    for _enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            with open(path, "r", encoding=_enc, newline="") as f:
                raw = f.read()
            break
        except UnicodeDecodeError:
            continue
    if raw is None:
        return None
    eol = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.split(eol)

    header = []
    styles = []
    dialogues = []
    in_events = False
    format_line = None

    for line in lines:
        if line.startswith("[Events]"):
            in_events = True
            header.append(line)
            continue
        if line.startswith("[") and not line.startswith("[Events]"):
            in_events = False
        if not in_events:
            if line.startswith("Style:"):
                styles.append(line)
            elif line.strip():
                header.append(line)
            continue
        if line.startswith("Format:"):
            format_line = line
            header.append(line)
            continue
        m = RE_DIALOGUE.match(line)
        if not m:
            continue
        st = _hms_to_sec(m.group(2))
        en = _hms_to_sec(m.group(3))
        tail = m.group(4)
        gps_dt = None
        gm = RE_DATE_TIME.search(tail)
        if gm:
            try:
                gps_dt = datetime(*map(int, gm.groups()))
            except Exception:
                gps_dt = None
        dialogues.append({"start": st, "end": en, "tail": tail,
                          "gps_dt": gps_dt})
    return {"header": header, "styles": styles, "dialogues": dialogues,
            "eol": eol, "format": format_line}


def _write_ssa(path, doc, precision="ms", encoding="utf-8-sig"):
    eol = doc.get("eol", "\r\n")
    out = []
    # py11: вставить styles перед [Events]
    _events_idx = None
    for i, ln in enumerate(doc["header"]):
        if ln.startswith("[Events]"):
            _events_idx = i
            break
    for i, ln in enumerate(doc["header"]):
        if i == _events_idx and doc.get("styles"):
            for st in doc["styles"]:
                out.append(st)
        out.append(ln)
    if _events_idx is None and doc.get("styles"):
        for st in doc["styles"]:
            out.append(st)
    if not any(ln.startswith("Format:") for ln in doc["header"]):
        out.append("Format: Marked, Start, End, Style, Name, "
                   "MarginL, MarginR, MarginV, Effect, Text")
    for d in doc["dialogues"]:
        start = _sec_to_hms(d["start"], precision=precision)
        end = _sec_to_hms(d["end"], precision=precision)
        # py11: tail уже содержит "Default,NTP,0000,0000,0000,,"
        # (из _parse_ssa). Не добавляем префикс повторно.
        out.append("Dialogue: 0," + start + "," + end
                   + "," + d["tail"])
    _enc = encoding or "utf-8-sig"
    try:
        with open(path, "w", encoding=_enc, newline="") as f:
            f.write(eol.join(out))
    except UnicodeEncodeError:
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            f.write(eol.join(out))


# ==============================================================
# audio_speeds.csv
# ==============================================================
def _load_speeds(path):
    if not path or not os.path.isfile(path):
        return []
    times, va, vg = [], [], []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.replace(";", ",").split(",")
            if len(parts) < 2:
                continue
            try:
                t = float(parts[0])
                a = float(parts[1])
            except ValueError:
                continue
            g = None
            if len(parts) >= 3 and parts[2].strip():
                try:
                    g = float(parts[2])
                except ValueError:
                    pass
            times.append(t)
            va.append(a)
            vg.append(g)
    return list(zip(times, va, vg))


def _interp_speed(t, speeds):
    if not speeds:
        return None, None
    valid = [(tt, a, g) for (tt, a, g) in speeds if a is not None]
    if not valid:
        return None, None
    if len(valid) == 1:
        return valid[0][1], valid[0][2]
    ts = [x[0] for x in valid]
    if t <= ts[0]:
        return valid[0][1], valid[0][2]
    if t >= ts[-1]:
        return valid[-1][1], valid[-1][2]
    lo, hi = 0, len(ts) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if ts[mid] <= t:
            lo = mid
        else:
            hi = mid
    t0, t1 = ts[lo], ts[hi]
    a0, a1 = valid[lo][1], valid[hi][1]
    g0, g1 = valid[lo][2], valid[hi][2]
    frac = 0.0 if t1 <= t0 else (t - t0) / (t1 - t0)
    av = a0 + frac * (a1 - a0) if (a0 is not None and a1 is not None) else None
    gv = (g0 + frac * (g1 - g0)
          if (g0 is not None and g1 is not None) else None)
    return av, gv


def _insert_audio_speed(tail, av, gv):
    """Добавляет/заменяет [аудио: X.X км/ч; GPS: Y.Y км/ч] после
    'Скорость: N км/ч'."""
    parts = []
    if av is not None:
        parts.append("аудио: %.1f км/ч" % av)
    if gv is not None:
        parts.append("GPS: %.1f км/ч" % gv)
    if not parts:
        return tail
    suffix = " [" + "; ".join(parts) + "]"
    rx_old = re.compile(r"\s*\[[^\]]*(?:аудио:|GPS:)[^\]]*\]")
    tail = rx_old.sub("", tail)
    rx_spd = re.compile(r"Скорость:\s*[0-9.]+\s*км/ч")
    if rx_spd.search(tail):
        return rx_spd.sub(lambda m: m.group(0) + suffix, tail, count=1)
    return tail


# ==============================================================
# split_ssa_to_frames
# ==============================================================
def split_ssa_to_frames(src_ssa, dst_ssa, fps, base_dt_video,
                         frame_start_in_full, speeds,
                         time_offset_for_speeds,
                         time_corr=0.0, msk_offset=0.0,
                         precision="ms"):
    """Каждый Dialogue -> N=round(dur*fps) суб-Dialogue по 1/fps.

    MSK[k] = base_dt_video + (frame_start_in_full + k)/fps
             + time_corr + msk_offset.

    Скорость в каждой Dialogue: интерполяция из speeds по
    (локальное_время_кадра + time_offset_for_speeds).

    Возвращает (n_in, n_out, base_dt_used) или None.
    """
    doc = _parse_ssa(src_ssa)
    if not doc or not doc["dialogues"]:
        return None
    if fps <= 0:
        return None

    out_dialogues = []
    frame_cursor = 0
    step = 1.0 / fps

    for dlg in doc["dialogues"]:
        dur = dlg["end"] - dlg["start"]
        n = max(1, int(round(dur * fps)))
        for k in range(n):
            t_local = frame_cursor * step
            t_for_speed = t_local + time_offset_for_speeds
            av, gv = _interp_speed(t_for_speed, speeds)
            tail = _insert_audio_speed(dlg["tail"], av, gv)

            msk_str = "[-]"
            if base_dt_video is not None:
                f_abs = frame_start_in_full + frame_cursor
                msk = (base_dt_video
                       + timedelta(seconds=f_abs / fps
                                   + time_corr + msk_offset))
                msk_str = (msk.strftime("%d.%m.%Y %H:%M:%S.")
                           + "%03d MSK" % (msk.microsecond // 1000))
            time_str = _sec_to_hms(t_local, precision="ms")

            idx = tail.rfind("\\NКадр:")
            if idx >= 0:
                tail = tail[:idx]
            if frame_start_in_full > 0:
                tag = ("\\NКадр:%d(в исходнике:%d кадр) / %s / %s [make_py]"
                       % (frame_cursor,
                          frame_start_in_full + frame_cursor,
                          time_str, msk_str))
            else:
                tag = ("\\NКадр:%d / %s / %s [make_py]"
                       % (frame_cursor, time_str, msk_str))
            tail = tail + tag

            out_dialogues.append({
                "start": frame_cursor * step,
                "end": (frame_cursor + 1) * step,
                "tail": tail,
            })
            frame_cursor += 1

    doc["dialogues"] = out_dialogues
    _write_ssa(dst_ssa, doc, precision=precision)
    n_in = len(_parse_ssa(src_ssa)["dialogues"])
    n_out = len(out_dialogues)
    return (n_in, n_out, base_dt_video)


# ==============================================================
# apply-style
# ==============================================================
def _apply_style(ssa_path):
    if not os.path.isfile(ssa_path):
        return 0
    with open(ssa_path, "r", encoding="utf-8-sig", newline="") as f:
        raw = f.read()
    eol = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.split(eol)
    out = []
    n = 0
    for line in lines:
        if line.startswith("Style:"):
            parts = line.split(",")
            if len(parts) >= 16:
                try:
                    parts[12] = "5"
                    parts[15] = "3"
                    n += 1
                except IndexError:
                    pass
            out.append(",".join(parts))
        else:
            out.append(line)
    if n:
        with open(ssa_path, "w", encoding="utf-8", newline="") as f:
            f.write(eol.join(out))
    return n


# ==============================================================
# fragment_info
# ==============================================================
def _frag_from_full(full_ssa, frag_ssa, frame_start, frame_end, fps,
                     base_dt_video, time_corr=0.0, msk_off=0.0,
                     precision="ms"):
    """py9: fallback frag .ssa из полного .ssa.

    Берёт dialogues[frame_start:frame_end+1] из полного .ssa
    (там уже 1 кадр на Dialogue после split full), пересчитывает
    локальные времена 0..dur, MSK, тег Кадр.

    Возвращает True/False.
    """
    full_doc = _parse_ssa(full_ssa)
    if not full_doc or not full_doc["dialogues"]:
        return False
    all_dlg = full_doc["dialogues"]
    if frame_end >= len(all_dlg):
        frame_end = len(all_dlg) - 1
    if frame_start < 0 or frame_start > frame_end:
        return False
    # py13: обрезаем по offset_frames (slice_len = frame_end - frame_start)
    # frame_start уже сдвинут на offset_frames снаружи.
    _slice_len = frame_end - frame_start + 1
    slice_dlg = all_dlg[frame_start:frame_start + _slice_len]
    if not slice_dlg:
        return False

    new_dlg = []
    for i, d in enumerate(slice_dlg):
        tail = d["tail"]
        idx = tail.rfind("\\NКадр:")
        if idx >= 0:
            tail = tail[:idx]
        msk_str = "[-]"
        if base_dt_video is not None:
            f_abs = frame_start + i
            msk = base_dt_video + timedelta(
                seconds=f_abs / fps + time_corr + msk_off)
            msk_str = (msk.strftime("%d.%m.%Y %H:%M:%S.")
                       + "%03d MSK" % (msk.microsecond // 1000))
        time_str = _sec_to_hms(i / fps, precision="ms")
        tag = ("\\NКадр:%d(в исходнике:%d кадр) / %s / %s [make_py]"
               % (i, frame_start + i, time_str, msk_str))
        new_dlg.append({
            "start": i / fps,
            "end": (i + 1) / fps,
            "tail": tail + tag,
            "gps_dt": d.get("gps_dt"),
        })

    out_doc = {
        "header": full_doc["header"],
        "styles": full_doc["styles"],
        "dialogues": new_dlg,
        "eol": full_doc["eol"],
        "format": full_doc.get("format"),
    }
    _write_ssa(frag_ssa, out_doc, precision=precision)
    return True


def _compute_fragment_info(ts_file, cfg, fps_used, has_b,
                            script_dir=None):
    a_start = int(cfg.get("analyze_start", 0) or 0)
    a_dur = int(cfg.get("analyze_dur", 0) or 0)
    if a_dur <= 0:
        return None

    fps_cfg = float(cfg.get("target_fps", 30.0))
    t_start_req = a_start / fps_cfg
    t_end_req = (a_start + a_dur) / fps_cfg

    # py8: локальный ffprobe приоритетнее PATH
    extra = (script_dir,) if script_dir else ()
    ffprobe = _find_exec("ffprobe", extra_dirs=extra)
    max_snap = 3.0
    idr_found = None
    if ffprobe:
        t_lo = max(0.0, t_start_req - max_snap)
        t_hi = t_start_req + 0.05
        rc, out, _ = _run(
            [ffprobe, "-v", "error",
             "-select_streams", "v:0",
             "-skip_frame", "nokey",
             "-show_entries", "frame=pkt_pts_time,pts_time",
             "-of", "default=noprint_wrappers=1",
             "-read_intervals", "%.3f%%%.3f" % (t_lo, t_hi),
             ts_file],
            timeout=60,
        )
        if rc == 0:
            times = []
            for line in out.splitlines():
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() in ("pkt_pts_time", "pts_time"):
                    try:
                        times.append(float(v.strip()))
                    except ValueError:
                        pass
            le = [t for t in times if t <= t_start_req + 1e-6]
            if le:
                idr_found = max(le)

    if idr_found is not None and (t_start_req - idr_found) <= max_snap:
        t_start_eff = idr_found
    else:
        t_start_eff = t_start_req

    # py8: frame_start_eff = a_start (analyze_start), frame_end_final =
    # a_start + a_dur - 1. Раньше было floor(t_start_eff*fps)+2 и
    # round(t_end*fps)+1 — сдвиг +2/+1 от выделения. tsp резал
    # не те кадры, Vdub2 показывал MSK не того кадра.
    frame_start_eff = a_start
    frame_end_final = a_start + a_dur - 1
    if frame_end_final < frame_start_eff:
        frame_end_final = frame_start_eff
    actual_dur = (frame_end_final - frame_start_eff + 1) / fps_used
    ts_base = os.path.splitext(os.path.basename(ts_file))[0]
    frag_name = "%s_%.3fs.TS" % (ts_base, actual_dur)
    corr = float(cfg.get("ssa_shift_correction_sec", -1.0))
    snap_abs = (t_start_req - t_start_eff) + corr
    return {
        "enabled": True, "fps_used": fps_used, "has_b_frames": has_b,
        "t_start_req": t_start_req, "t_end_req": t_end_req,
        "t_start_eff": t_start_eff,
        "frame_start_eff": frame_start_eff,
        "frame_end_final": frame_end_final,
        "actual_dur": actual_dur,
        "fragment_filename": frag_name,
        "snap_abs": snap_abs,
        "plan": "Z" if has_b == 0 else "X",
        "next_idr_pkt": None,
    }


# ==============================================================
# MAIN run
# ==============================================================
def make_disk_run(ts_file, cfg, log_path=None, script_dir=None,
                   base_dt_override_iso=None, only_ssa=False):
    ts_file = os.path.abspath(ts_file)
    ts_name = os.path.basename(ts_file)
    base = os.path.splitext(ts_name)[0]

    series, series_dir, work_root = _detect_series_dir(ts_file)
    _ensure_dir(series_dir)

    reports_dir = os.path.join(work_root, base + "_reports")
    _ensure_dir(reports_dir)

    if log_path is None:
        log_path = os.path.join(reports_dir, "make_disk.log")
    _open_log(log_path)

    _log(LINE)
    _log(f"make_disk_py v{VERSION}")
    _log(f"ts:         {ts_file}")
    _log(f"series:     {series} -> {series_dir}")
    _log(f"reports:    {reports_dir}")
    _log(LINE)

    for sub in ("", "ssa", "exiftool", "tsanalize",
                "MediaInfo", "txt", "gpx", "video_integrity"):
        _ensure_dir(os.path.join(series_dir, sub))

    if script_dir is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))

    exiftool = _find_exec("exiftool", extra_dirs=(script_dir, series_dir))
    ffprobe = _find_exec("ffprobe", extra_dirs=(script_dir,))
    ffmpeg = _find_exec("ffmpeg", extra_dirs=(script_dir,))
    tsp = _find_exec("tsp", extra_dirs=(script_dir, series_dir))
    tsanalyze = _find_exec("tsanalyze", extra_dirs=(script_dir, series_dir))
    mediainfo = _find_exec("mediainfo", "MediaInfo",
                            extra_dirs=(script_dir, series_dir))
    otchet = _find_exec("otchet_0x300_для_ssa_26.py",
                        "otchet_0x300_для_ssa_26.exe",
                        extra_dirs=(script_dir, series_dir))
    gpx_fmt = None
    for cand in (os.path.join(script_dir, "gpx.fmt"),
                 os.path.join(series_dir, "gpx.fmt")):
        if os.path.isfile(cand):
            gpx_fmt = cand
            break

    for nm, p in (("exiftool", exiftool), ("ffprobe", ffprobe),
                  ("tsp", tsp), ("tsanalyze", tsanalyze),
                  ("mediainfo", mediainfo), ("otchet", otchet),
                  ("gpx.fmt", gpx_fmt)):
        _log(f"{nm}: {p or 'NOT FOUND'}", "I" if p else "W")

    fps_cfg = float(cfg.get("target_fps", 30.0))
    fps_used, has_b = _ffprobe_fps(ffprobe, ts_file, fallback=fps_cfg)
    _log(f"fps: target={fps_cfg}, used={fps_used}, "
         f"has_b_frames={has_b}")

    results = []
    py_exe = sys.executable

    patch_helper = os.path.join(script_dir, "patch_otchet_tolerant.py")
    ssa_shift_helper = os.path.join(script_dir, "ssa_shift.py")
    frag_helper = os.path.join(script_dir, "tsp_frag_calc.py")

    speeds_path = os.path.join(reports_dir, "audio_speeds.csv")
    speeds = _load_speeds(speeds_path)
    if speeds:
        _log(f"audio_speeds.csv: {len(speeds)} точек")
    else:
        _log(f"audio_speeds.csv: НЕТ (скорость не вставится)", "W")

    # --- base_dt_video ---
    full_ssa_guess = os.path.join(series_dir, "ssa", base + ".ssa")
    override_iso = base_dt_override_iso
    if not override_iso and cfg.get("msk_base_dt_override"):
        override_iso = str(cfg["msk_base_dt_override"])
    _bd_off = float(cfg.get("msk_base_dt_offset_sec", 0.0))
    base_dt_video, src = compute_base_dt_video(
        ts_file, ffprobe, series_dir,
        full_ssa_path=full_ssa_guess if os.path.isfile(full_ssa_guess)
        else None,
        full_fps=fps_used,
        override_iso=override_iso,
        base_dt_offset_sec=_bd_off,
    )
    _log(f"[base] base_dt_video = {base_dt_video} (source: {src})")
    if base_dt_video is None:
        _log(f"[base] base_dt_video НЕ НАЙДЕН — MSK не будет", "W")

    # ============ [1] copy_full_ts ============
    full_ts_dst = os.path.join(series_dir, ts_name)
    if os.path.abspath(full_ts_dst) == ts_file:
        _log(f"[1] copy_full_ts SKIP (уже в {series_dir})", "S")
        results.append(("copy_full_ts", 0))
    elif os.path.isfile(full_ts_dst):
        _log(f"[1] copy_full_ts SKIP (already exists)", "S")
        results.append(("copy_full_ts", 0))
    else:
        try:
            shutil.copy2(ts_file, full_ts_dst)
            _log(f"[1] copy_full_ts OK")
            results.append(("copy_full_ts", 0))
        except OSError as e:
            _log(f"[1] copy_full_ts: {e}", "E")
            results.append(("copy_full_ts", 1))
            _close_log()
            return results

    # ============ [2] exiftool full ============
    exif_full = os.path.join(series_dir, "exiftool",
                              ts_name + "_exiftool_orig.txt")
    if exiftool:
        if os.path.isfile(exif_full) and os.path.getsize(exif_full) > 0:
            _log(f"[2] exiftool full SKIP (already exists)", "S")
        else:
            rc, _, _ = _run(
                [exiftool, "-v2",
                 "-c", "%d deg %d' %.2f",
                 "-d", "%Y:%m:%d %H:%M:%S",
                 "-ee3", "-G", "-t", full_ts_dst],
                out_path=exif_full,
                log_prefix="exiftool full",
            )
            _log(f"[2] exiftool full {'OK' if rc == 0 else 'FAIL'}",
                 "I" if rc == 0 else "E")
            results.append(("exiftool_full", rc))
    else:
        _log(f"[2] exiftool SKIP (not found)", "S")

    # ============ [3] otchet full ============
    ssa_full_raw = os.path.join(series_dir, "ssa", ts_name + ".ssa")
    ssa_full = os.path.join(series_dir, "ssa", base + ".ssa")
    ssa_full_ok = None
    if otchet and os.path.isfile(exif_full):
        for p in (ssa_full_raw, ssa_full,
                  os.path.join(series_dir, base + ".ssa")):
            if os.path.isfile(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        console_full = os.path.join(series_dir, "txt",
                                     ts_name + "_orig.txt")
        if otchet.lower().endswith(".exe"):
            cmd = [otchet, "-i", exif_full, "-o", ssa_full_raw]
        else:
            cmd = [py_exe, otchet, "-i", exif_full, "-o", ssa_full_raw]
        rc, _, _ = _run(cmd, out_path=console_full,
                        log_prefix="otchet full")
        ssa_dir = os.path.join(series_dir, "ssa")
        if os.path.isdir(ssa_dir):
            for f in os.listdir(ssa_dir):
                if f.endswith("_full.txt"):
                    try:
                        shutil.move(os.path.join(ssa_dir, f),
                                    os.path.join(series_dir, "txt", f))
                    except OSError:
                        pass
        if os.path.isfile(ssa_full_raw) and os.path.isfile(patch_helper):
            _run([py_exe, patch_helper, "--ssa", ssa_full_raw],
                 log_prefix="patch_ssa full")
        if os.path.isfile(ssa_full_raw):
            try:
                os.replace(ssa_full_raw, ssa_full)
            except OSError as e:
                _log(f"rename full: {e}", "W")
        ok = os.path.isfile(ssa_full) and os.path.getsize(ssa_full) > 0
        _log(f"[3] otchet full {'OK' if ok else 'FAIL'}",
             "I" if ok else "E")
        results.append(("otchet_full", 0 if ok else 1))
        ssa_full_ok = ssa_full if ok else None
    else:
        _log(f"[3] otchet SKIP", "S")

    # ============ [4] ssa_shift full ============
    if ssa_full_ok and os.path.isfile(ssa_shift_helper):
        full_shift = float(cfg.get("full_ssa_shift_sec", 0.0))
        rc, _, _ = _run(
            [py_exe, ssa_shift_helper, "--ssa", ssa_full_ok,
             "--offset", "%.4f" % full_shift],
            log_prefix="ssa_shift full")
        _log(f"[4] ssa_shift full offset={full_shift:+.4f} "
             f"{'OK' if rc == 0 else 'FAIL'}",
             "I" if rc == 0 else "E")
        results.append(("ssa_shift_full", rc))
        sidecar = os.path.join(series_dir, base + ".ssa")
        try:
            shutil.copy2(ssa_full_ok, sidecar)
        except OSError:
            pass

    # ============ [5] split full .ssa -> кадры ============
    if ssa_full_ok and base_dt_video is not None and not only_ssa:
        time_corr = float(cfg.get("ssa_time_correction_sec", 0.0))
        msk_off = float(cfg.get("msk_manual_offset_sec", 0.0))
        precision = ("ms" if cfg.get("ssa_time_precision_ms", True)
                     else "cc")
        tmp = ssa_full_ok + ".split"
        res = split_ssa_to_frames(
            ssa_full_ok, tmp, fps_used, base_dt_video,
            frame_start_in_full=0, speeds=speeds,
            time_offset_for_speeds=0.0,
            time_corr=time_corr, msk_offset=msk_off,
            precision=precision)
        if res:
            try:
                os.replace(tmp, ssa_full_ok)
            except OSError:
                pass
            _log(f"[5] split full: {res[0]} -> {res[1]} dialogues")
            results.append(("split_full", 0))
            sidecar = os.path.join(series_dir, base + ".ssa")
            try:
                shutil.copy2(ssa_full_ok, sidecar)
            except OSError:
                pass
        else:
            _log(f"[5] split full FAIL", "E")
            results.append(("split_full", 1))

    # ============ [6] apply-style ============
    # py12: apply-style ОТКЛЮЧЁН по умолчанию.
    # Style остаётся как от otchet (Vdub2: мелкий шрифт слева).
    # Включить: cfg ssa_apply_style = true.
    if cfg.get("ssa_apply_style", False):
        n_style = _apply_style(ssa_full_ok)
        if n_style:
            _log(f"[6] apply-style: {n_style} Style -> top-left")
    else:
        _log("[6] apply-style SKIP (ssa_apply_style=false)", "S")

    if only_ssa:
        _log(LINE)
        _log("only_ssa: fragment/tsanalyze пропущены")
        _log(LINE)
        for name, rc in results:
            _log(f"  [{'OK  ' if rc == 0 else 'FAIL'}] {name} (rc={rc})")
        _close_log()
        return results

    # ============ FRAGMENT ============
    frag = _compute_fragment_info(ts_file, cfg, fps_used, has_b,
                                    script_dir=script_dir)
    if frag and frag["enabled"] and tsp and otchet:
        frag_name = frag["fragment_filename"]
        frag_base = os.path.splitext(frag_name)[0]
        frag_ts_abs = os.path.join(series_dir, frag_name)
        frag_ts_rel = os.path.join(series, frag_name)

        # [7] fragment_calc
        full_txt = _full_txt_path(full_ts_dst, series_dir)
        frag_txt = os.path.join(reports_dir, "frag.txt")
        rc = 1
        if os.path.isfile(full_txt) and os.path.isfile(frag_helper):
            args = [py_exe, frag_helper,
                    "--full-txt", full_txt,
                    "--fps", "%.6f" % frag["fps_used"],
                    "--frame-start", str(frag["frame_start_eff"]),
                    "--frame-end", str(frag["frame_end_final"]),
                    "--plan", frag["plan"],
                    "--out", frag_txt]
            if frag.get("next_idr_pkt"):
                args += ["--next-idr-pkt", str(frag["next_idr_pkt"])]
            rc, _, _ = _run(args, log_prefix="fragment_calc")
        _log(f"[7] fragment_calc {'OK' if rc == 0 else 'FAIL'}",
             "I" if rc == 0 else "E")
        results.append(("fragment_calc", rc))

        # [8] tsp
        rc = 1
        if os.path.isfile(frag_txt):
            try:
                with open(frag_txt, "r", encoding="utf-8") as f:
                    toks = f.read().split()
                s_pkt, e_pkt = int(toks[0]), int(toks[1])
            except Exception:
                s_pkt = e_pkt = None
            if s_pkt is not None:
                rc, _, _ = _run(
                    [tsp, "-I", "file", full_ts_dst, "-P", "filter",
                     "-i", "%d-%d" % (s_pkt, e_pkt),
                     "-O", "file", frag_ts_abs],
                    log_prefix="tsp")
        _log(f"[8] tsp {'OK' if rc == 0 else 'FAIL'}",
             "I" if rc == 0 else "E")
        results.append(("tsp", rc))

        # [9] exiftool frag
        if exiftool and os.path.isfile(frag_ts_abs):
            exif_frag = os.path.join(series_dir, "exiftool",
                                      frag_name + "_exiftool_orig.txt")
            rc, _, _ = _run(
                [exiftool, "-v2",
                 "-c", "%d deg %d' %.2f",
                 "-d", "%Y:%m:%d %H:%M:%S",
                 "-ee3", "-G", "-t", frag_ts_abs],
                out_path=exif_frag, log_prefix="exiftool frag")
            results.append(("exiftool_frag", rc))

        # [10] otchet frag
        ssa_frag_raw = os.path.join(series_dir, "ssa", frag_name + ".ssa")
        ssa_frag = os.path.join(series_dir, "ssa", frag_base + ".ssa")
        ssa_frag_ok = None
        if otchet and os.path.isfile(frag_ts_abs):
            exif_frag = os.path.join(series_dir, "exiftool",
                                      frag_name + "_exiftool_orig.txt")
            console_frag = os.path.join(series_dir, "txt",
                                          frag_name + "_orig.txt")
            for p in (ssa_frag_raw, ssa_frag,
                      os.path.join(series_dir, frag_base + ".ssa")):
                if os.path.isfile(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            if otchet.lower().endswith(".exe"):
                cmd = [otchet, "-i", exif_frag, "-o", ssa_frag_raw]
            else:
                cmd = [py_exe, otchet, "-i", exif_frag,
                       "-o", ssa_frag_raw]
            rc, _, _ = _run(cmd, out_path=console_frag,
                            log_prefix="otchet frag")
            ssa_dir = os.path.join(series_dir, "ssa")
            if os.path.isdir(ssa_dir):
                for f in os.listdir(ssa_dir):
                    if f.endswith("_full.txt"):
                        try:
                            shutil.move(os.path.join(ssa_dir, f),
                                        os.path.join(series_dir, "txt", f))
                        except OSError:
                            pass
            if os.path.isfile(ssa_frag_raw) and \
                    os.path.isfile(patch_helper):
                _run([py_exe, patch_helper, "--ssa", ssa_frag_raw],
                     log_prefix="patch_ssa frag")
            if os.path.isfile(ssa_frag_raw):
                try:
                    os.replace(ssa_frag_raw, ssa_frag)
                except OSError as e:
                    _log(f"rename frag: {e}", "W")
            ok = (os.path.isfile(ssa_frag)
                  and os.path.getsize(ssa_frag) > 0)
            _log(f"[10] otchet frag {'OK' if ok else 'FAIL'}",
                 "I" if ok else "E")
            results.append(("otchet_frag", 0 if ok else 1))
            ssa_frag_ok = ssa_frag if ok else None

        # [10b] py9: fallback если frag .ssa пустой
        # (в 0.8с фрагменте может не быть пакета 0x0300)
        _frag_doc_check = _parse_ssa(ssa_frag_ok) if ssa_frag_ok else None
        _n_frag_dlg = (len(_frag_doc_check["dialogues"])
                       if _frag_doc_check else 0)
        _fallback_used = False

        if ssa_frag_ok and _n_frag_dlg == 0 and base_dt_video is not None:
            _log("[10b] frag .ssa пустой (0 Dialogue) — беру из "
                 "полного .ssa", "W")
            time_corr = float(cfg.get("ssa_time_correction_sec", 0.0))
            msk_off = float(cfg.get("msk_manual_offset_sec", 0.0))
            precision = ("ms" if cfg.get("ssa_time_precision_ms", True)
                         else "cc")
            # py13: offset_frames (default -1) — tsp захватывает
            # 1 видео-пакет преролла; реальный кадр 0 в .TS на 1 меньше.
            _frag_off = int(cfg.get(
                "msk_frag_start_offset_frames", -1))
            _fs_eff = int(frag["frame_start_eff"]) + _frag_off
            _fe_eff = int(frag["frame_end_final"])
            ok_fb = _frag_from_full(
                ssa_full_ok, ssa_frag_ok,
                _fs_eff, _fe_eff,
                fps_used, base_dt_video,
                time_corr=time_corr, msk_off=msk_off,
                precision=precision)
            if ok_fb:
                _log(f"[10b] offset_frames={_frag_off:+d}, "
                     f"frame_range={_fs_eff}..{_fe_eff}")
            if ok_fb:
                _frag_doc_check = _parse_ssa(ssa_frag_ok)
                _n_frag_dlg = (len(_frag_doc_check["dialogues"])
                               if _frag_doc_check else 0)
                _log(f"[10b] fallback OK: {_n_frag_dlg} dialogues "
                     f"({frag['frame_start_eff']}..{frag['frame_end_final']})")
                results.append(("frag_from_full", 0))
                _fallback_used = True
            else:
                _log("[10b] fallback FAIL", "E")
                results.append(("frag_from_full", 1))

        # [11] ssa_shift frag — только если не fallback
        if (not _fallback_used
                and ssa_frag_ok
                and os.path.isfile(ssa_shift_helper)):
            snap_abs = float(frag.get("snap_abs", 0.0))
            rc, _, _ = _run(
                [py_exe, ssa_shift_helper, "--ssa", ssa_frag_ok,
                 "--offset", "%.4f" % snap_abs],
                log_prefix="ssa_shift frag")
            _log(f"[11] ssa_shift frag offset={snap_abs:+.4f} "
                 f"{'OK' if rc == 0 else 'FAIL'}",
                 "I" if rc == 0 else "E")
            results.append(("ssa_shift_frag", rc))

        # [12] split frag .ssa -> кадры — только если не fallback
        if (not _fallback_used
                and ssa_frag_ok
                and base_dt_video is not None):
            time_corr = float(cfg.get("ssa_time_correction_sec", 0.0))
            msk_off = float(cfg.get("msk_manual_offset_sec", 0.0))
            precision = ("ms" if cfg.get("ssa_time_precision_ms", True)
                         else "cc")
            first_frame = max(0, int(frag["frame_start_eff"]))
            tmp = ssa_frag_ok + ".split"
            res = split_ssa_to_frames(
                ssa_frag_ok, tmp, fps_used, base_dt_video,
                frame_start_in_full=first_frame, speeds=speeds,
                time_offset_for_speeds=frag.get("t_start_eff", 0.0),
                time_corr=time_corr, msk_offset=msk_off,
                precision=precision)
            if res:
                try:
                    os.replace(tmp, ssa_frag_ok)
                except OSError:
                    pass
                _log(f"[12] split frag: {res[0]} -> {res[1]} "
                     f"dialogues (first_frame={first_frame})")
                results.append(("split_frag", 0))
            else:
                _log(f"[12] split frag FAIL", "E")
                results.append(("split_frag", 1))

        # sidecar копируем ВСЕГДА (и после fallback, и после split)
        if ssa_frag_ok:
            sidecar = os.path.join(series_dir, frag_base + ".ssa")
            try:
                shutil.copy2(ssa_frag_ok, sidecar)
            except OSError:
                pass

        # [13] apply-style frag
        # py12: ОТКЛЮЧЁН (Style как от otchet, Vdub2 мелкий).
        # Включить: cfg ssa_apply_style = true.
        sidecar_frag = os.path.join(series_dir, frag_base + ".ssa")
        if cfg.get("ssa_apply_style", False):
            n_style = _apply_style(sidecar_frag)
            if n_style:
                _log(f"[13] apply-style frag: {n_style} Style")
        else:
            _log("[13] apply-style frag SKIP (ssa_apply_style=false)", "S")

        # [14-16] tsanalyze/MediaInfo/GPX frag
        if tsanalyze and os.path.isfile(frag_ts_abs):
            rc, _, _ = _run(
                [tsanalyze, frag_ts_abs],
                out_path=os.path.join(series_dir, "tsanalize",
                                       frag_name + "_tsanalize.txt"),
                log_prefix="tsanalyze frag")
            results.append(("tsanalyze_frag", rc))
        if mediainfo and os.path.isfile(frag_ts_abs):
            rc, _, _ = _run(
                [mediainfo, frag_ts_abs],
                out_path=os.path.join(series_dir, "MediaInfo",
                                       frag_name + "_MediaInfo.txt"),
                log_prefix="mediainfo frag")
            results.append(("mediainfo_frag", rc))
        if exiftool and os.path.isfile(frag_ts_abs) and gpx_fmt:
            rc, _, _ = _run(
                [exiftool, "-ee3", "-p", gpx_fmt, frag_ts_abs],
                out_path=os.path.join(series_dir, "gpx",
                                       frag_name + ".gpx"),
                log_prefix="gpx frag")
            results.append(("gpx_frag", rc))

    # ============ tsanalyze/MediaInfo/GPX full ============
    if tsanalyze and os.path.isfile(full_ts_dst):
        rc, _, _ = _run(
            [tsanalyze, full_ts_dst],
            out_path=os.path.join(series_dir, "tsanalize",
                                   ts_name + "_tsanalize.txt"),
            log_prefix="tsanalyze full")
        results.append(("tsanalyze_full", rc))
    if mediainfo and os.path.isfile(full_ts_dst):
        rc, _, _ = _run(
            [mediainfo, full_ts_dst],
            out_path=os.path.join(series_dir, "MediaInfo",
                                   ts_name + "_MediaInfo.txt"),
            log_prefix="mediainfo full")
        results.append(("mediainfo_full", rc))
    if exiftool and os.path.isfile(full_ts_dst) and gpx_fmt:
        rc, _, _ = _run(
            [exiftool, "-ee3", "-p", gpx_fmt, full_ts_dst],
            out_path=os.path.join(series_dir, "gpx",
                                   ts_name + ".gpx"),
            log_prefix="gpx full")
        results.append(("gpx_full", rc))

    _log(LINE)
    _log("ИТОГО:")
    for name, rc in results:
        _log(f"  [{'OK  ' if rc == 0 else 'FAIL'}] {name} (rc={rc})")
    _log(LINE)
    _close_log()
    return results


# ==============================================================
# CLI
# ==============================================================
def _ensure_dir(p):
    try:
        os.makedirs(p, exist_ok=True)
    except OSError:
        pass


def _load_cfg(path):
    if path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _dump_meta(ts_file, cfg=None):
    if cfg is None:
        cfg = {}
    ffprobe = _find_exec("ffprobe")
    series, series_dir, work_root = _detect_series_dir(ts_file)
    ts_name = os.path.basename(ts_file)
    base = os.path.splitext(ts_name)[0]
    print(LINE)
    print(f"ts:        {ts_file}")
    print(f"series:    {series} -> {series_dir}")
    print(f"work_root: {work_root}")
    print(LINE)
    print(f"[1] ffprobe creation_time:  {_ffprobe_creation_utc(ffprobe, ts_file)}")
    st = _ffprobe(ffprobe, ts_file, "format=start_time")
    print(f"[2] format start_time (min): {st.get('start_time', '?')}")
    v_start = _ffprobe_video_start(ffprobe, ts_file)
    a_start = _ffprobe_audio_start(ffprobe, ts_file)
    print(f"[2a] video start_time:        {v_start}")
    print(f"[2b] audio start_time:        {a_start}")
    stc = _ffprobe(ffprobe, ts_file, "stream_tags=creation_time")
    print(f"[3] stream creation_time:     {stc.get('TAG:creation_time', '?')}")
    fps, has_b = _ffprobe_fps(ffprobe, ts_file, fallback=30.0)
    print(f"[4] fps: {fps}, has_b_frames: {has_b}")
    pts_idr = _ffprobe_pts_first_idr(ffprobe, ts_file)
    print(f"[5] pts_first_idr:         {pts_idr}")
    dt_name = _dt_from_filename_14(ts_file)
    print(f"[6] filename (14-digit):   {dt_name}")
    try:
        mt = datetime.fromtimestamp(os.path.getmtime(ts_file))
        print(f"[7] mtime (NTFS):          {mt}")
    except OSError:
        pass
    full_txt = _full_txt_path(ts_file, series_dir)
    ex = os.path.isfile(full_txt)
    print(f"[8] _full.txt:             {full_txt} (exists={ex})")
    if ex:
        n0 = _first_gps_frame_in_full_txt(full_txt)
        print(f"    N0 (first 0x0300 frame): {n0}")
    full_ssa = os.path.join(series_dir, "ssa", base + ".ssa")
    ex_ssa = os.path.isfile(full_ssa)
    print(f"[9] full .ssa:             {full_ssa} (exists={ex_ssa})")
    if ex_ssa:
        doc = _parse_ssa(full_ssa)
        if doc and doc["dialogues"]:
            d0 = doc["dialogues"][0]
            print(f"    first Dialogue: start={d0['start']:.3f}, "
                  f"end={d0['end']:.3f}, gps_dt={d0.get('gps_dt')}")
    # py6: offset из cfg
    _bd_off = float(cfg.get("msk_base_dt_offset_sec", 0.0))
    _override_iso = cfg.get("msk_base_dt_override") or None
    print("[10] msk_base_dt_offset_sec:  %+.4f" % _bd_off)
    if _override_iso:
        print("     msk_base_dt_override:     %s" % _override_iso)
    bd, src = compute_base_dt_video(
        ts_file, ffprobe, series_dir,
        full_ssa_path=full_ssa if ex_ssa else None,
        full_fps=fps,
        override_iso=_override_iso,
        base_dt_offset_sec=_bd_off)
    print(LINE)
    print(f"=> base_dt_video: {bd}")
    print(f"=> source:        {src}")
    print(LINE)
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="make_disk_py — Make Disk на чистом Python")
    ap.add_argument("ts_file")
    ap.add_argument("--config", default=None)
    ap.add_argument("--log", default=None)
    ap.add_argument("--base-dt", default=None,
                    help="ISO 8601: '2026-10-23T11:31:49.233'")
    ap.add_argument("--dump-meta", action="store_true")
    ap.add_argument("--only-ssa", action="store_true")
    args = ap.parse_args()

    ts_file = os.path.abspath(args.ts_file)
    if not os.path.isfile(ts_file):
        print(f"Файл не найден: {ts_file}", file=sys.stderr)
        return 1

    # py7: конфиг загружается ДО --dump-meta, расширенный поиск
    base = os.path.splitext(os.path.basename(ts_file))[0]
    ts_dir = os.path.dirname(ts_file)
    _, series_dir, work_root = _detect_series_dir(ts_file)

    cfg_path = args.config
    if cfg_path is None:
        cands = [
            os.path.join(ts_dir, base + "_config.json"),
            os.path.join(work_root, base + "_config.json"),
            os.path.join(work_root, "audio_speed_config.json"),
            os.path.join(ts_dir, "audio_speed_config.json"),
        ]
        for c in cands:
            if os.path.isfile(c):
                cfg_path = c
                break
    cfg = _load_cfg(cfg_path)
    if args.dump_meta:
        print("config_candidates:")
        for c in (
            os.path.join(ts_dir, base + "_config.json"),
            os.path.join(work_root, base + "_config.json"),
            os.path.join(work_root, "audio_speed_config.json"),
            os.path.join(ts_dir, "audio_speed_config.json"),
        ):
            print("  %s  [%s]" % (c, "OK" if os.path.isfile(c) else "-"))

    if args.dump_meta:
        print("config_used: %s" % (cfg_path or "(defaults)"))
        return _dump_meta(ts_file, cfg)

    print("config_used: %s" % (cfg_path or "(defaults)"))

    results = make_disk_run(
        ts_file, cfg, log_path=args.log,
        base_dt_override_iso=args.base_dt,
        only_ssa=args.only_ssa)
    n_fail = sum(1 for _, rc in results if rc != 0)
    return 0 if n_fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
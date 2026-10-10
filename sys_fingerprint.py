#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sys_fingerprint.py
Системный fingerprint: MD5, информация о компьютере, диске, скрипте.
Выделено из audio_speed_calc.py для уменьшения размера главного модуля.
"""

import os
import sys
import subprocess
import hashlib
import platform
import datetime
import ctypes


# ============================================================
# Системные функции (перенесено из ts_integrity_check.py)
# ============================================================

def md5_file(filepath):
    """FIPS-совместимый MD5."""
    try:
        h = hashlib.new('md5', usedforsecurity=False)
    except TypeError:
        h = hashlib.md5()
    try:
        with open(filepath, 'rb') as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest().upper()
    except Exception:
        h = hashlib.sha256()
        with open(filepath, 'rb') as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                h.update(chunk)
        return 'SHA256:' + h.hexdigest().upper()[:32]



def file_mtime(filepath):
    mtime = os.path.getmtime(filepath)
    return datetime.datetime.fromtimestamp(mtime)


def get_computer_info():
    info = {}
    info['name'] = platform.node() or '---'
    info['os'] = platform.platform() or '---'
    info['os_version'] = platform.version() or '---'
    info['arch'] = platform.machine() or '---'
    info['processor'] = platform.processor() or '---'
    info['user'] = os.environ.get('USERNAME', os.environ.get('USER', '---'))
    info['python'] = platform.python_version()
    info['cpu_serial'] = '---'
    if sys.platform == 'win32':
        try:
            r = subprocess.run(
                ['wmic', 'cpu', 'get', 'ProcessorId'],
                capture_output=True, text=True, timeout=30)
            if r.returncode == 0 and r.stdout.strip():
                lines = [l.strip() for l in r.stdout.strip().split('\n') if l.strip()]
                if len(lines) > 1:
                    info['cpu_serial'] = lines[1]
        except Exception:
            pass
        if info['cpu_serial'] == '---':
            try:
                r = subprocess.run(
                    ['powershell', '-Command',
                     '(Get-CimInstance Win32_Processor).ProcessorId'],
                    capture_output=True, text=True, timeout=30)
                if r.returncode == 0 and r.stdout.strip():
                    info['cpu_serial'] = r.stdout.strip()
            except Exception:
                pass
    return info


def _get_disk_info_winapi(drive_letter):
    """WinAPI DeviceIoControl: модель и серийник физического диска."""
    GENERIC_READ = 0x80000000
    FILE_SHARE_READ = 1
    FILE_SHARE_WRITE = 2
    OPEN_EXISTING = 3
    IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS = 0x00560000
    IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
    StorageDeviceProperty = 0

    vol_path = '\\\\.\\%s' % drive_letter
    ctypes.windll.kernel32.CreateFileW.restype = ctypes.c_void_p
    handle = ctypes.windll.kernel32.CreateFileW(
        vol_path, GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None, OPEN_EXISTING, 0, None)
    if handle == ctypes.c_void_p(-1).value or handle == -1:
        return None, None

    try:
        # IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS
        out_buf = (ctypes.c_ubyte * 64)()
        bytes_ret = ctypes.c_ulong(0)
        ok = ctypes.windll.kernel32.DeviceIoControl(
            ctypes.c_void_p(handle),
            IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS,
            None, 0, out_buf, 64, ctypes.byref(bytes_ret), None)
        if not ok or bytes_ret.value < 8:
            return None, None

        disk_number = ctypes.c_uint.from_buffer(out_buf).value

        # IOCTL_STORAGE_QUERY_PROPERTY
        phys_path = '\\\\.\\PhysicalDrive%d' % disk_number
        handle2 = ctypes.windll.kernel32.CreateFileW(
            phys_path, GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None, OPEN_EXISTING, 0, None)
        if handle2 == ctypes.c_void_p(-1).value or handle2 == -1:
            return None, None

        try:
            query_buf_size = 1024
            query_buf = (ctypes.c_ubyte * query_buf_size)()
            # STORAGE_PROPERTY_QUERY
            ctypes.memmove(query_buf, ctypes.byref(ctypes.c_uint(StorageDeviceProperty)), 4)
            ctypes.memmove(ctypes.byref(query_buf, 4), ctypes.byref(ctypes.c_uint(0)), 4)

            ok2 = ctypes.windll.kernel32.DeviceIoControl(
                ctypes.c_void_p(handle2),
                IOCTL_STORAGE_QUERY_PROPERTY,
                query_buf, 8, query_buf, query_buf_size,
                ctypes.byref(bytes_ret), None)
            if not ok2:
                return None, None

            # STORAGE_DEVICE_DESCRIPTOR
            desc = ctypes.cast(query_buf, ctypes.POINTER(ctypes.c_ubyte))
            vendor_offset = ctypes.c_uint.from_buffer(query_buf, 12).value
            product_offset = ctypes.c_uint.from_buffer(query_buf, 16).value
            serial_offset = ctypes.c_uint.from_buffer(query_buf, 24).value

            def _read_string(offset):
                if offset == 0 or offset >= query_buf_size:
                    return None
                chars = []
                i = offset
                while i < query_buf_size and query_buf[i] != 0:
                    chars.append(chr(query_buf[i]))
                    i += 1
                return ''.join(chars).strip() if chars else None

            model = None
            if product_offset:
                model = _read_string(product_offset)
            if not model and vendor_offset:
                model = _read_string(vendor_offset)

            serial = _read_string(serial_offset) if serial_offset else None
            # Очистка модели от фрагментов серийника
            if model and serial:
                if serial and model.endswith(serial):
                    model = model[:-len(serial)].strip()
                elif serial and len(serial) > 3 and serial[:8] in model:
                    idx = model.find(serial[:8])
                    if idx >= 0:
                        model = model[:idx].strip()
            return model if model else None, serial if serial else None
        except Exception:
            return None, None
        finally:
            ctypes.windll.kernel32.CloseHandle(handle2)
    except Exception:
        return None, None
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def get_disk_info(path):
    """Информация о диске: ФС, метка, серийник тома, модель и серийник диска."""
    info = {}
    drive_letter = os.path.splitdrive(os.path.abspath(path))[0] or 'C:'
    if not drive_letter.endswith(':'):
        drive_letter += ':'

    info['drive'] = drive_letter
    info['fs_type'] = '---'
    info['volume_label'] = '---'
    info['volume_serial'] = '---'
    info['total_bytes'] = 0
    info['free_bytes'] = 0
    info['disk_model'] = '---'
    info['disk_serial'] = '---'

    if sys.platform == 'win32':
        # GetVolumeInformationW
        fs_buf = ctypes.create_unicode_buffer(256)
        label_buf = ctypes.create_unicode_buffer(256)
        serial = ctypes.c_uint(0)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(drive_letter + '\\'),
            label_buf, 256,
            ctypes.byref(serial),
            None, None, fs_buf, 256)
        if ok:
            info['fs_type'] = fs_buf.value or '---'
            info['volume_label'] = label_buf.value or '---'
            info['volume_serial'] = '%08X' % serial.value

        # GetDiskFreeSpaceExW
        try:
            total = ctypes.c_uint64(0)
            free = ctypes.c_uint64(0)
            ok2 = ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                ctypes.c_wchar_p(drive_letter + '\\'),
                None, ctypes.byref(total), ctypes.byref(free))
            if ok2:
                info['total_bytes'] = total.value
                info['free_bytes'] = free.value
        except Exception:
            pass

        # WinAPI DeviceIoControl
        try:
            model, serial_num = _get_disk_info_winapi(drive_letter)
            if model:
                info['disk_model'] = model
            if serial_num:
                info['disk_serial'] = serial_num
        except Exception:
            pass

        # Fallback: wmic — парсинг по позиции колонки SerialNumber
        if info['disk_model'] == '---' or info['disk_serial'] == '---':
            try:
                r = subprocess.run(
                    ['wmic', 'diskdrive', 'get', 'Model,SerialNumber'],
                    capture_output=True, text=True, timeout=30)
                if r.returncode == 0 and r.stdout.strip():
                    raw_lines = r.stdout.strip().split('\n')
                    if len(raw_lines) >= 2:
                        header = raw_lines[0]
                        # Найти позицию колонки SerialNumber
                        sn_pos = header.find('SerialNumber')
                        if sn_pos >= 0:
                            data_line = raw_lines[1]
                            serial_val = data_line[sn_pos:].strip()
                            model_val = data_line[:sn_pos].strip()
                            if info['disk_model'] == '---' and model_val:
                                info['disk_model'] = model_val
                            if info['disk_serial'] == '---' and serial_val:
                                info['disk_serial'] = serial_val
                        else:
                            # Fallback: split
                            parts = raw_lines[1].split()
                            if len(parts) >= 2:
                                if info['disk_model'] == '---':
                                    info['disk_model'] = parts[0]
                                if info['disk_serial'] == '---':
                                    info['disk_serial'] = parts[-1]
            except Exception:
                pass

        # Fallback: PowerShell Win32_DiskDrive
        if info['disk_model'] == '---' or info['disk_serial'] == '---':
            try:
                r = subprocess.run(
                    ['powershell', '-Command',
                     'Get-CimInstance Win32_DiskDrive | Select-Object -First 1 | '
                     'Format-List Model, SerialNumber'],
                    capture_output=True, text=True, timeout=30)
                if r.returncode == 0 and r.stdout.strip():
                    model_val = None
                    serial_val = None
                    for line in r.stdout.strip().split('\n'):
                        line = line.strip()
                        if line.startswith('Model'):
                            model_val = line.split(':', 1)[1].strip() if ':' in line else None
                        elif line.startswith('SerialNumber'):
                            serial_val = line.split(':', 1)[1].strip() if ':' in line else None
                    if info['disk_model'] == '---' and model_val:
                        info['disk_model'] = model_val
                    if info['disk_serial'] == '---' and serial_val:
                        info['disk_serial'] = serial_val
            except Exception:
                pass

        # Fallback: PowerShell Get-PhysicalDisk
        if info['disk_model'] == '---' or info['disk_serial'] == '---':
            try:
                r = subprocess.run(
                    ['powershell', '-Command',
                     'Get-PhysicalDisk | Select-Object -First 1 | '
                     'Format-List FriendlyName, SerialNumber'],
                    capture_output=True, text=True, timeout=30)
                if r.returncode == 0 and r.stdout.strip():
                    model_val = None
                    serial_val = None
                    for line in r.stdout.strip().split('\n'):
                        line = line.strip()
                        if line.startswith('FriendlyName'):
                            model_val = line.split(':', 1)[1].strip() if ':' in line else None
                        elif line.startswith('SerialNumber'):
                            serial_val = line.split(':', 1)[1].strip() if ':' in line else None
                    if info['disk_model'] == '---' and model_val:
                        info['disk_model'] = model_val
                    if info['disk_serial'] == '---' and serial_val:
                        info['disk_serial'] = serial_val
            except Exception:
                pass
    else:
        try:
            st = os.statvfs(path)
            info['total_bytes'] = st.f_blocks * st.f_frsize
            info['free_bytes'] = st.f_bavail * st.f_frsize
        except Exception:
            pass

    return info


def get_script_info(version, script_path):
    info = {}
    script_path = os.path.abspath(script_path)
    info['name'] = os.path.basename(script_path)
    info['path'] = script_path
    info['size'] = os.path.getsize(script_path)
    info['mtime'] = file_mtime(script_path).strftime('%Y-%m-%d %H:%M:%S')
    info['md5'] = md5_file(script_path)
    info['version'] = version
    return info
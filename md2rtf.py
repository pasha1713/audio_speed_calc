#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""md2rtf.py (v9)

Что делает:
  * Пакет .md → отдельные .rtf/.docx с выравниванием:
    шапка слева 9 см, заголовок по центру, тело по ширине,
    дата и подпись в одной строке.
  * У Жалобы нет номера в имени файла: «Жалоба.rtf».
  * --attach «N:путь» — копирование PDF-приложений.
  * Автоподстановка ТЕКУЩЕЙ ДАТЫ вместо плейсхолдера
    «___» __________ 2026 г. (флаг --date YYYY-MM-DD).
  * Нумерация страниц внизу по центру для документов,
    в которых оценка количества страниц > 2.
    Управление: --page-numbers auto|always|never (default: auto).

Использование:
    python md2rtf.py "Пакет_документов_ЦАФАП.md"
    python md2rtf.py pack.md --docx
    python md2rtf.py pack.md --attach "1:14549525979925.pdf"
    python md2rtf.py pack.md --date 2026-11-15
    python md2rtf.py pack.md --page-numbers always
"""
import argparse
import os
import re
import shutil
import sys
from datetime import datetime

DOC_HEADER_RE = re.compile(
    r'^#\s+ДОКУМЕНТ\s+(\d+)\.\s+(.+?)\s*$',
    re.MULTILINE | re.IGNORECASE)
SEP_RE = re.compile(r'^---\s*$', re.MULTILINE)
BOLD_RE = re.compile(r'\*\*(.+?)\*\*')
DATE_PLACEHOLDER_RE = re.compile(
    r'«___»\s+_+\s+\d{4}\s+г\.')

SHORT_NAMES = {
    1: ('Жалоба', True),
    2: ('Ходатайство_о_приобщении_DVD', False),
    3: ('Ходатайство_об_истребовании', False),
    4: ('Ходатайство_в_форме_определения', False),
}

ATTACH_NAMES = {
    1: 'Постановление',
}

HEADER_INDENT_TWIPS = 5100
SIGN_TAB_POS_TWIPS = 9350
SIGN_TAB_POS_CM = 16.5

FS_BODY = 24
FS_HEADER = 22
FS_TITLE = 28
FS_PAGE_NUM = 20  # 10 pt для номера страницы

_CURRENT_DATE_STR = None
PAGE_NUM_MODE = 'auto'

RU_MONTHS = [
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
]


# ==============================================================
# Дата
# ==============================================================
def format_date_ru(dt):
    return '«%02d» %s %d г.' % (
        dt.day, RU_MONTHS[dt.month - 1], dt.year)


def substitute_date(text):
    if _CURRENT_DATE_STR is None:
        return text
    return DATE_PLACEHOLDER_RE.sub(_CURRENT_DATE_STR, text)


def parse_date_arg(s):
    if not s:
        return None
    for fmt in ('%Y-%m-%d', '%d.%m.%Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# ==============================================================
# Оценка количества страниц
# ==============================================================
def estimate_pages(body, fps=35):
    """Консервативная оценка количества страниц A4.

    fps = строк на страницу (35 — с запасом, при 12 pt Times New
    Roman, полях 2/2/2/2 см, межстрочном 1.15).

    Считаем:
      - все строки body (непустые + пустые);
      - длинные строки (> 75 символов) могут занимать 2+ шрифтовых
        строки — добавляем по 1 за каждые 75 символов сверх 75.
    """
    if not body:
        return 1
    total = 0
    for line in body.split('\n'):
        stripped = line.strip()
        if not stripped:
            total += 1
            continue
        n = len(stripped)
        extra = max(0, (n - 1) // 75)
        total += 1 + extra
    return max(1, (total + fps - 1) // fps)


def need_page_numbers(body):
    if PAGE_NUM_MODE == 'always':
        return True
    if PAGE_NUM_MODE == 'never':
        return False
    # auto: > 2 страниц (то есть 3 и более)
    return estimate_pages(body) > 2


# ==============================================================
# Разбор markdown
# ==============================================================
def split_documents(md_text):
    matches = list(DOC_HEADER_RE.finditer(md_text))
    if not matches:
        return []
    docs = []
    for i, m in enumerate(matches):
        num = int(m.group(1))
        title = BOLD_RE.sub(r'\1', m.group(2).strip())
        start = m.end()
        end = (matches[i + 1].start()
               if i + 1 < len(matches) else len(md_text))
        body = SEP_RE.sub('', md_text[start:end]).strip()
        docs.append((num, title, body))
    return docs


def _is_date_line(line):
    s = line.strip()
    if not s:
        return False
    return ('г.' in s) and ('___' in s or '«' in s)


def _is_signature_line(line):
    return '/ Суменков' in line or '/Суменков' in line


def parse_doc_structure(body):
    lines = body.split('\n')
    first_bold_idx = None
    for i, line in enumerate(lines):
        if line.strip() and BOLD_RE.search(line):
            first_bold_idx = i
            break

    if first_bold_idx is None:
        return [('body', ln) if ln.strip() else ('body_blank', '')
                for ln in lines]

    result = []
    for line in lines[:first_bold_idx]:
        result.append(('header', line))

    j = first_bold_idx
    while j < len(lines):
        line = lines[j]
        if not line.strip():
            result.append(('title_blank', ''))
            j += 1
            continue
        if not BOLD_RE.search(line):
            break
        result.append(('title', line))
        j += 1

    for line in lines[j:]:
        stripped = line.strip()
        if _is_signature_line(line):
            result.append(('signature', line))
        elif _is_date_line(line):
            result.append(('date', line))
        elif not stripped:
            result.append(('body_blank', ''))
        else:
            result.append(('body', line))

    merged = []
    i = 0
    while i < len(result):
        typ, text = result[i]
        if typ == 'date':
            k = i + 1
            while k < len(result) and result[k][0] == 'body_blank':
                k += 1
            if k < len(result) and result[k][0] == 'signature':
                merged.append(
                    ('date_signature', (text, result[k][1])))
                i = k + 1
                continue
        merged.append((typ, text))
        i += 1
    return merged


# ==============================================================
# RTF
# ==============================================================
def rtf_escape(text):
    result = []
    for c in text:
        code = ord(c)
        if code < 128:
            if c in '{}\\':
                result.append('\\' + c)
            else:
                result.append(c)
        else:
            if code > 32767:
                code -= 65536
            result.append('\\u%d?' % code)
    return ''.join(result)


def _rtf_par(align, text, left_indent=0,
             font_size=FS_BODY, bold=False):
    align_map = {'left': '\\ql', 'center': '\\qc',
                 'right': '\\qr', 'justify': '\\qj'}
    parts = ['\\pard' + align_map.get(align, '\\ql')]
    if left_indent:
        parts.append('\\li%d' % left_indent)
    parts.append('\\ri0\\fi0')
    parts.append('\\f0\\fs%d' % font_size)
    parts.append('\\b' if bold else '\\b0')
    parts.append(' ')
    parts.append(rtf_escape(text))
    parts.append('\\b0\\par\n')
    return ''.join(parts)


def _rtf_par_inline(align, text, left_indent=0,
                    font_size=FS_BODY):
    align_map = {'left': '\\ql', 'center': '\\qc',
                 'right': '\\qr', 'justify': '\\qj'}
    parts = ['\\pard' + align_map.get(align, '\\ql')]
    if left_indent:
        parts.append('\\li%d' % left_indent)
    parts.append('\\ri0\\fi0')
    parts.append('\\f0\\fs%d' % font_size)
    parts.append(' ')
    pos = 0
    for m in BOLD_RE.finditer(text):
        if m.start() > pos:
            parts.append(rtf_escape(text[pos:m.start()]))
        parts.append('{\\b ' + rtf_escape(m.group(1)) + '}')
        pos = m.end()
    if pos < len(text):
        parts.append(rtf_escape(text[pos:]))
    parts.append('\\par\n')
    return ''.join(parts)


def _rtf_par_date_signature(date_text, sig_text):
    clean_date = BOLD_RE.sub(r'\1', date_text)
    clean_date = substitute_date(clean_date)
    clean_sig = BOLD_RE.sub(r'\1', sig_text)
    return (
        '\\pard\\ql\\ri0\\fi0\\f0\\fs%d '
        '\\tqr\\tx%d '
        '%s\\tab %s\\par\n'
        % (FS_BODY, SIGN_TAB_POS_TWIPS,
           rtf_escape(clean_date),
           rtf_escape(clean_sig))
    )


def structure_to_rtf(structure):
    out = []
    for typ, text in structure:
        if typ == 'header':
            if text.strip():
                out.append(_rtf_par(
                    'left', text,
                    left_indent=HEADER_INDENT_TWIPS,
                    font_size=FS_HEADER))
            else:
                out.append('\\pard\\par\n')
        elif typ == 'title':
            clean = BOLD_RE.sub(r'\1', text)
            out.append(_rtf_par(
                'center', clean,
                font_size=FS_TITLE, bold=True))
        elif typ == 'title_blank':
            out.append('\\pard\\par\n')
        elif typ == 'date_signature':
            date_text, sig_text = text
            out.append(_rtf_par_date_signature(date_text, sig_text))
        elif typ == 'date':
            out.append(_rtf_par('left', substitute_date(text),
                                font_size=FS_BODY))
        elif typ == 'signature':
            out.append(_rtf_par('right', text, font_size=FS_BODY))
        elif typ == 'body_blank':
            out.append('\\pard\\par\n')
        else:
            out.append(_rtf_par_inline(
                'justify', text, font_size=FS_BODY))
    return ''.join(out)


def _rtf_footer_with_page_number():
    """RTF-колонтитул: номер страницы внизу по центру.

    Используется поле PAGE (совместимо с Word/LibreOffice/WordPad).
    """
    return (
        '{\\footer\\pard\\qc\\f0\\fs%d '
        '{\\field{\\*\\fldinst PAGE}{\\fldrslt 1}}\\par}\n'
        % FS_PAGE_NUM
    )


def wrap_rtf(body_rtf, with_page_numbers=False):
    header = (
        '{\\rtf1\\ansi\\deff0\\uc1\n'
        '{\\fonttbl{\\f0 Times New Roman;}}\n'
    )
    if with_page_numbers:
        header += _rtf_footer_with_page_number()
    header += '\\viewkind4\\uc1\\pard\\f0\\fs%d\n' % FS_BODY
    return header + body_rtf + '}\n'


def write_rtf(path, title, body):
    structure = parse_doc_structure(body)
    with_pn = need_page_numbers(body)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(wrap_rtf(structure_to_rtf(structure),
                         with_page_numbers=with_pn))
    return with_pn


# ==============================================================
# DOCX
# ==============================================================
def _docx_add_inline(p, text):
    pos = 0
    for m in BOLD_RE.finditer(text):
        if m.start() > pos:
            p.add_run(text[pos:m.start()])
        r = p.add_run(m.group(1))
        r.bold = True
        pos = m.end()
    if pos < len(text):
        p.add_run(text[pos:])


def _docx_add_page_footer(doc):
    """Добавляет в footer документа номер страницы по центру."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    section = doc.sections[0]
    footer = section.footer
    footer.is_linked_to_previous = False

    p = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    run = p.add_run()

    # <w:fldChar w:fldCharType="begin"/>
    fld_begin = OxmlElement('w:fldChar')
    fld_begin.set(qn('w:fldCharType'), 'begin')
    run._r.append(fld_begin)

    # <w:instrText xml:space="preserve">PAGE</w:instrText>
    instr = OxmlElement('w:instrText')
    instr.set(qn('xml:space'), 'preserve')
    instr.text = ' PAGE '
    run._r.append(instr)

    # <w:fldChar w:fldCharType="separate"/>
    fld_sep = OxmlElement('w:fldChar')
    fld_sep.set(qn('w:fldCharType'), 'separate')
    run._r.append(fld_sep)

    # <w:t>1</w:t> — заглушка до пересчёта полей
    txt = OxmlElement('w:t')
    txt.text = '1'
    run._r.append(txt)

    # <w:fldChar w:fldCharType="end"/>
    fld_end = OxmlElement('w:fldChar')
    fld_end.set(qn('w:fldCharType'), 'end')
    run._r.append(fld_end)


def try_docx(path, title, body, with_pn):
    try:
        from docx import Document
        from docx.enum.text import (
            WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT)
        from docx.shared import Cm
    except ImportError:
        return False
    try:
        doc = Document()
        structure = parse_doc_structure(body)
        for typ, text in structure:
            if typ == 'header':
                if text.strip():
                    p = doc.add_paragraph()
                    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                    p.paragraph_format.left_indent = Cm(9)
                    p.add_run(text)
                else:
                    doc.add_paragraph()
            elif typ == 'title':
                clean = BOLD_RE.sub(r'\1', text)
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                r = p.add_run(clean)
                r.bold = True
            elif typ == 'title_blank':
                doc.add_paragraph()
            elif typ == 'date_signature':
                date_text, sig_text = text
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                p.paragraph_format.tab_stops.add_tab_stop(
                    Cm(SIGN_TAB_POS_CM), WD_TAB_ALIGNMENT.RIGHT)
                _docx_add_inline(p, substitute_date(date_text))
                p.add_run('\t')
                _docx_add_inline(p, sig_text)
            elif typ == 'date':
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                _docx_add_inline(p, substitute_date(text))
            elif typ == 'signature':
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                _docx_add_inline(p, text)
            elif typ == 'body_blank':
                doc.add_paragraph()
            else:
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                _docx_add_inline(p, text)

        if with_pn:
            _docx_add_page_footer(doc)

        doc.save(path)
        return True
    except Exception as e:
        print(f"  [WARN] docx: {e}", file=sys.stderr)
        return False


# ==============================================================
# Имена и приложения
# ==============================================================
def sanitize_filename(s):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1F]', '_', s)
    s = s.strip('. ').rstrip('.')
    return s[:100] or 'document'


def file_base(num, title):
    item = SHORT_NAMES.get(num)
    if item:
        short, no_prefix = item
        return short if no_prefix else f"{num}_{short}"
    return f"{num}_{sanitize_filename(title)}"


def copy_attachments(specs, out_dir):
    for spec in specs:
        if ':' not in spec:
            print(f"  [SKIP] attach '{spec}': формат "
                  f"'<номер>:<путь>'", file=sys.stderr)
            continue
        num_s, src = spec.split(':', 1)
        try:
            num = int(num_s)
        except ValueError:
            print(f"  [SKIP] attach '{spec}': номер не число",
                  file=sys.stderr)
            continue
        if not os.path.isfile(src):
            print(f"  [FAIL] attach {spec}: файл не найден",
                  file=sys.stderr)
            continue
        ext = os.path.splitext(src)[1].lower()
        name = ATTACH_NAMES.get(num, f'Приложение_{num}')
        dst = os.path.join(out_dir, f"{num}_{name}{ext}")
        try:
            shutil.copy2(src, dst)
            sz = os.path.getsize(dst)
            print(f"  [OK] {os.path.basename(dst)} "
                  f"({sz} байт)", file=sys.stderr)
        except OSError as e:
            print(f"  [FAIL] attach {spec}: {e}", file=sys.stderr)


# ==============================================================
# main
# ==============================================================
def main():
    global _CURRENT_DATE_STR, PAGE_NUM_MODE

    ap = argparse.ArgumentParser(
        description='Пакет .md → .rtf/.docx с выравниванием, '
                    'автодатой, нумерацией страниц, PDF-приложениями')
    ap.add_argument('md_file')
    ap.add_argument('--out-dir', default=None)
    ap.add_argument('--docx', action='store_true')
    ap.add_argument('--attach', action='append', default=[],
                    metavar='N:PATH')
    ap.add_argument('--date', default=None,
                    help='Дата подписания (YYYY-MM-DD). '
                         'По умолчанию — текущая.')
    ap.add_argument('--page-numbers', default='auto',
                    choices=('auto', 'always', 'never'),
                    help='Нумерация страниц: auto (только для >2 стр.), '
                         'always, never. Default: auto.')
    args = ap.parse_args()

    _dt = parse_date_arg(args.date) or datetime.now()
    _CURRENT_DATE_STR = format_date_ru(_dt)
    PAGE_NUM_MODE = args.page_numbers

    if not os.path.isfile(args.md_file):
        print(f"[FAIL] файл не найден: {args.md_file}",
              file=sys.stderr)
        return 1

    with open(args.md_file, 'r', encoding='utf-8') as f:
        md_text = f.read()

    docs = split_documents(md_text)
    if not docs:
        print("[FAIL] не найдено '# ДОКУМЕНТ N. ...'",
              file=sys.stderr)
        return 2

    base = os.path.splitext(os.path.basename(args.md_file))[0]
    out_dir = (args.out_dir or
               os.path.join(
                   os.path.dirname(os.path.abspath(args.md_file)),
                   base + '_docs'))
    os.makedirs(out_dir, exist_ok=True)

    print(f"[INFO] вход:    {args.md_file}", file=sys.stderr)
    print(f"[INFO] выход:   {out_dir}", file=sys.stderr)
    print(f"[INFO] документов: {len(docs)}", file=sys.stderr)
    print(f"[INFO] дата:    {_CURRENT_DATE_STR}", file=sys.stderr)
    print(f"[INFO] page-numbers: {PAGE_NUM_MODE}", file=sys.stderr)
    print()

    for num, title, body in docs:
        fbase = file_base(num, title)
        est = estimate_pages(body)
        with_pn = need_page_numbers(body)
        pn_mark = ('+ page#' if with_pn else '')
        print(f"[{num}] {title}  (est. pages: {est} {pn_mark})",
              file=sys.stderr)

        rtf_path = os.path.join(out_dir, fbase + ".rtf")
        with_pn = write_rtf(rtf_path, title, body)
        print(f"  [OK] {os.path.basename(rtf_path)}"
              + (" (with page numbers)" if with_pn else ""),
              file=sys.stderr)

        if args.docx:
            docx_path = os.path.join(out_dir, fbase + ".docx")
            if try_docx(docx_path, title, body, with_pn):
                print(f"  [OK] {os.path.basename(docx_path)}",
                      file=sys.stderr)
            else:
                print(f"  [SKIP] docx (нет python-docx)",
                      file=sys.stderr)

    if args.attach:
        print()
        print("[ATTACH] копирую приложения:", file=sys.stderr)
        copy_attachments(args.attach, out_dir)

    print()
    print(f"Готово. Папка: {out_dir}")
    try:
        for fn in sorted(os.listdir(out_dir)):
            full = os.path.join(out_dir, fn)
            if os.path.isfile(full):
                print(f"  {fn}  ({os.path.getsize(full)} байт)")
    except OSError:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
"""Local document -> Markdown, normalized character geometry, and PDF preview.

Usage: python document_ocr.py INPUT OUTPUT.md
No network requests are performed. See README.md for the coordinate contract.
"""
from __future__ import annotations

import argparse
import bisect
import csv
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import cv2
import numpy as np
from PIL import Image, ImageOps, ImageSequence
import pymupdf as fitz
from office_sandbox import DEFAULT_IMAGE, SandboxError, build_image, render as render_in_sandbox


class ConversionError(RuntimeError):
    pass


@dataclass
class Word:
    text: str
    bbox: list[float]
    line: tuple
    method: str
    confidence: float | None = None
    characters: list[dict] = field(default_factory=list)
    clipped: bool = False


def run(command: list[str], *, timeout: int = 180, env=None):
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConversionError(f"Cannot run {command[0]}: {exc}") from exc
    if result.returncode:
        message = (result.stderr or result.stdout).decode('utf-8', errors='replace')
        raise ConversionError(f"{Path(command[0]).name} failed ({result.returncode}): {message[-3000:]}")
    return result


def find_tesseract(explicit: str | None = None) -> str:
    candidates = [explicit, os.environ.get('TESSERACT_CMD'), shutil.which('tesseract'),
                  str(Path(__file__).parent / '.tools/ocr/Library/bin/tesseract.exe'),
                  'C:/Program Files/Tesseract-OCR/tesseract.exe']
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise ConversionError('Tesseract not found. Install it with rus+eng data, or pass --tesseract PATH.')


def tesseract_env(executable: str, tessdata: str | None) -> dict:
    env = os.environ.copy()
    # Conda DLL dependencies are next to the executable; no shell activation needed.
    env['PATH'] = str(Path(executable).parent) + os.pathsep + env.get('PATH', '')
    if tessdata:
        env['TESSDATA_PREFIX'] = str(Path(tessdata).resolve())
    elif 'TESSDATA_PREFIX' not in env:
        for candidate in [Path(executable).parent / '../../share/tessdata',
                          Path(executable).parent / '../share/tessdata',
                          Path(executable).parent / 'tessdata']:
            if candidate.is_dir():
                env['TESSDATA_PREFIX'] = str(candidate.resolve())
                break
    return env


def render_office(source: Path, destination: Path, args):
    try:
        report = render_in_sandbox(source, destination, image=args.sandbox_image,
                                   docker=args.docker, timeout=args.timeout)
    except SandboxError as exc:
        raise ConversionError(str(exc)) from exc
    destination.with_name('office-report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 'libreoffice_docker'


def font_path(explicit: str | None) -> Path:
    for candidate in [explicit, 'C:/Windows/Fonts/arial.ttf',
                      '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                      '/System/Library/Fonts/Supplemental/Arial.ttf']:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise ConversionError('A Unicode font is needed for TXT/MD rendering; pass --font PATH.ttf.')


def prepare_pdf(source: Path, destination: Path, args) -> tuple[str, list[dict]]:
    extension = source.suffix.lower()
    if extension == '.pdf':
        shutil.copyfile(source, destination)
        return 'original_pdf', []
    if extension in ('.docx', '.xlsx'):
        renderer = render_office(source, destination, args)
        return renderer, []
    if extension in ('.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.webp'):
        pages = []
        doc = fitz.open()
        with Image.open(source) as original:
            for frame in ImageSequence.Iterator(original):
                orientation = frame.getexif().get(274, 1)
                image = ImageOps.exif_transpose(frame).convert('RGB')
                stream = io.BytesIO()
                image.save(stream, format='PNG')
                page = doc.new_page(width=image.width, height=image.height)
                page.insert_image(page.rect, stream=stream.getvalue())
                pages.append({'original_pixel_size': [frame.width, frame.height],
                              'exif_orientation': orientation,
                              'image_coordinate_space': 'EXIF-oriented pixels'})
        doc.save(destination)
        doc.close()
        return 'image_pixels', pages
    if extension in ('.txt', '.md'):
        text = source.read_text(encoding=args.encoding)
        font_file = font_path(args.font)
        font = fitz.Font(fontfile=str(font_file))
        doc = fitz.open()
        page = None
        y = 0
        for line in text.splitlines() or ['']:
            pieces, piece = [], ''
            for char in line.expandtabs(4):
                if piece and font.text_length(piece + char, fontsize=10) > 511:
                    pieces.append(piece)
                    piece = ''
                piece += char
            pieces.append(piece)
            for piece in pieces:
                if page is None or y > 790:
                    page = doc.new_page(width=595, height=842)
                    page.insert_font(fontname='sourcefont', fontfile=str(font_file))
                    y = 48
                page.insert_text((42, y), piece, fontsize=10, fontname='sourcefont')
                y += 15
        doc.save(destination)
        doc.close()
        return 'text_render', []
    raise ConversionError(f'Unsupported extension: {extension}')


def contains(rect, x, y, margin=0.0):
    return rect[0] - margin <= x <= rect[2] + margin and rect[1] - margin <= y <= rect[3] + margin


def centre(rect):
    return (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2


def native_words(page) -> list[Word]:
    words = [Word(w[4], list(w[:4]), (w[5], w[6]), 'pdf_text')
             for w in page.get_text('words', sort=True)]
    # Real glyph bounds from the PDF, never a proportional subdivision of a word.
    lines = defaultdict(list)
    for word in words:
        lines[word.line].append(word)
    for block_no, block in enumerate(page.get_text('rawdict')['blocks']):
        for line_no, line in enumerate(block.get('lines', [])):
            candidates = lines.get((block_no, line_no), [])
            for span in line['spans']:
                for char in span['chars']:
                    if char['c'].isspace():
                        continue
                    x, y = centre(char['bbox'])
                    for word in candidates:
                        if contains(word.bbox, x, y, 0.1):
                            word.characters.append({'text': char['c'], 'bbox': list(char['bbox'])})
                            break
    return words


def raster_tables(image: Image.Image, scale_x: float, scale_y: float):
    """Detect ruled tables, including merged cells, without guessing word geometry."""
    gray = np.asarray(image.convert('L'))
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                  cv2.THRESH_BINARY_INV, 31, 15)
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN,
                                  cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, gray.shape[1] // 35), 1)))
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN,
                                cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(25, gray.shape[0] // 40))))
    mask = cv2.bitwise_or(horizontal, vertical)
    joined = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    external, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    tables = []
    for contour in external:
        x, y, width, height = cv2.boundingRect(contour)
        if width < gray.shape[1] * .18 or height < 45:
            continue
        region = joined[y:y+height, x:x+width]
        contours, _ = cv2.findContours(region, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        cells = []
        for inner in contours:
            cx, cy, cw, ch = cv2.boundingRect(inner)
            if cw > 12 and ch > 10 and cw * ch < width * height * .9:
                # Hole contours follow the INSIDE edges of the physical cell.
                cells.append([(x+cx)/scale_x, (y+cy)/scale_y,
                              (x+cx+cw)/scale_x, (y+cy+ch)/scale_y])
        if len(cells) < 4:
            continue
        table = {'bbox': [x/scale_x, y/scale_y, (x+width)/scale_x, (y+height)/scale_y],
                 'cells': cells, 'method': 'raster_lines'}
        recover_open_cells(table, horizontal, vertical, scale_x, scale_y)
        tables.append(table)
    # Remove only long structural rules, not punctuation or digit strokes.
    clean = np.asarray(image.convert('RGB')).copy()
    table_mask = np.zeros_like(mask)
    for table in tables:
        b = table['bbox']
        x0, y0, x1, y1 = round(b[0]*scale_x), round(b[1]*scale_y), round(b[2]*scale_x), round(b[3]*scale_y)
        table_mask[y0:y1, x0:x1] = mask[y0:y1, x0:x1]
    clean[cv2.dilate(table_mask, np.ones((2, 2), np.uint8)) > 0] = 255
    return tables, Image.fromarray(clean)


def ocr_words(image: Image.Image, page_size, args, temporary: Path, psm=None) -> list[Word]:
    executable = find_tesseract(args.tesseract)
    image_file = temporary / 'ocr.png'
    base = temporary / 'recognition'
    image.save(image_file)
    mode = (3 if args.psm == 'auto' else args.psm) if psm is None else psm
    run([executable, str(image_file), str(base), '-l', args.lang, '--psm', str(mode),
         '--dpi', str(args.dpi), '-c', 'tessedit_create_tsv=1',
         '-c', 'tessedit_create_boxfile=1'],
        timeout=args.timeout, env=tesseract_env(executable, args.tessdata))
    sx, sy = page_size[0] / image.width, page_size[1] / image.height
    words = []
    with base.with_suffix('.tsv').open(encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle, delimiter='\t', quoting=csv.QUOTE_NONE):
            if row['level'] != '5' or not row['text'].strip():
                continue
            x, y, width, height = (int(row[k]) for k in ['left', 'top', 'width', 'height'])
            words.append(Word(row['text'].strip(), [x*sx, y*sy, (x+width)*sx, (y+height)*sy],
                              tuple(int(row[k]) for k in ['block_num', 'par_num', 'line_num']),
                              'tesseract', float(row['conf']) / 100))
    # makebox returns symbol coordinates with a BOTTOM-left origin.
    # Associate symbols spatially; matching text runs later receive MD offsets.
    if base.with_suffix('.box').is_file():
        for line in base.with_suffix('.box').read_text(encoding='utf-8').splitlines():
            parts = line.rsplit(' ', 5)
            if len(parts) != 6 or not parts[0].strip():
                continue
            char, left, bottom, right, top, _ = parts
            rect = [int(left)*sx, (image.height-int(top))*sy,
                    int(right)*sx, (image.height-int(bottom))*sy]
            x, y = centre(rect)
            for word in words:
                if contains(word.bbox, x, y, .2):
                    word.characters.append({'text': char, 'bbox': rect})
                    break
    return words


def merge_positions(values, tolerance=3):
    groups = []
    for value in sorted(values):
        if groups and value - groups[-1][-1] <= tolerance:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [sum(group) / len(group) for group in groups]


def recover_open_cells(table, horizontal, vertical, sx, sy):
    """Recover cells at open table edges, keeping spans across absent rules."""
    cells = table['cells']
    xs = merge_positions([c[i] for c in cells for i in (0, 2)] + [table['bbox'][0], table['bbox'][2]])
    ys = merge_positions([c[i] for c in cells for i in (1, 3)] + [table['bbox'][1], table['bbox'][3]])
    missing = {(r, c) for r in range(len(ys)-1) for c in range(len(xs)-1)
               if not any(contains(cell, (xs[c]+xs[c+1])/2, (ys[r]+ys[r+1])/2, 1)
                          for cell in cells)}

    def separated(a, b):
        r, c = a
        rr, cc = b
        if r != rr:
            y = round(ys[max(r, rr)]*sy)
            x0, x1 = round(xs[c]*sx)+3, round(xs[c+1]*sx)-3
            strip = horizontal[max(0, y-2):y+3, max(0, x0):x1]
            return strip.size > 0 and np.mean(np.any(strip > 0, axis=0)) > .5
        x = round(xs[max(c, cc)]*sx)
        y0, y1 = round(ys[r]*sy)+3, round(ys[r+1]*sy)-3
        strip = vertical[max(0, y0):y1, max(0, x-2):x+3]
        return strip.size > 0 and np.mean(np.any(strip > 0, axis=1)) > .5

    while missing:
        seed = min(missing)
        missing.remove(seed)
        component, queue = {seed}, [seed]
        while queue:
            r, c = queue.pop()
            for neighbor in [(r-1, c), (r+1, c), (r, c-1), (r, c+1)]:
                if neighbor in missing and not separated((r, c), neighbor):
                    missing.remove(neighbor)
                    component.add(neighbor)
                    queue.append(neighbor)
        rows, cols = [r for r, _ in component], [c for _, c in component]
        r0, r1, c0, c1 = min(rows), max(rows), min(cols), max(cols)
        if len(component) == (r1-r0+1)*(c1-c0+1):
            cells.append([xs[c0], ys[r0], xs[c1+1], ys[r1+1]])
        else:
            # A damaged nonrectangular region is ambiguous; do not merge it
            # into an invented rectangle covering neighboring real cells.
            cells.extend([xs[c], ys[r], xs[c+1], ys[r+1]] for r, c in sorted(component))


def prepare_cell_image(image):
    """Remove isolated scan specks and enlarge small glyphs, retaining punctuation."""
    gray = np.asarray(image.convert('L'))
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    ink = (binary == 0).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink)
    glyphs = [s for s in stats[1:] if s[cv2.CC_STAT_HEIGHT] >= 4 and s[cv2.CC_STAT_AREA] >= 8]
    if not glyphs:
        return image, 1
    height = float(np.median([s[cv2.CC_STAT_HEIGHT] for s in glyphs]))
    cleaned = np.asarray(image.convert('RGB')).copy()
    for label in range(1, count):
        x, y, w, h, area = stats[label]
        if area > 3 or h > 2:
            continue
        # A comma/decimal point stays: it is near a full-height glyph. Only
        # tiny, distant specks are removed; real minus signs are larger.
        distance = min(max(g[0]-(x+w), x-(g[0]+g[2]), g[1]-(y+h), y-(g[1]+g[3]), 0)
                       for g in glyphs)
        if distance > max(6, height*.75):
            ink[labels == label] = 0
    # Keep original antialiasing around glyphs. Hard binarization changes small
    # serif digits and loses decimal commas; only whiten unsupported background.
    support = cv2.dilate(ink, np.ones((3, 3), dtype=np.uint8))
    cleaned[support == 0] = 255
    scale = min(3, max(1, int(np.ceil(24/height))))
    result = Image.fromarray(cleaned)
    if scale > 1:
        result = result.resize((result.width*scale, result.height*scale), Image.Resampling.LANCZOS)
    return result, scale


def table_grid(table, words):
    cells = table['cells']
    xs = merge_positions([c[i] for c in cells for i in (0, 2)] + [table['bbox'][0], table['bbox'][2]])
    ys = merge_positions([c[i] for c in cells for i in (1, 3)] + [table['bbox'][1], table['bbox'][3]])
    if len(xs) < 3 or len(ys) < 3:
        return None
    grid = [[[] for _ in range(len(xs)-1)] for _ in range(len(ys)-1)]
    assigned = set()
    for index, word in enumerate(words):
        x, y = centre(word.bbox)
        if not contains(table['bbox'], x, y):
            continue
        matching = [cell for cell in cells if contains(cell, x, y, .8)]
        if matching:
            cell = min(matching, key=lambda c: (c[2]-c[0])*(c[3]-c[1]))
            col = min(range(len(xs)-1), key=lambda i: abs(xs[i]-cell[0]))
            row = min(range(len(ys)-1), key=lambda i: abs(ys[i]-cell[1]))
        else:
            col = max(0, min(len(xs)-2, bisect.bisect_right(xs, x)-1))
            row = max(0, min(len(ys)-2, bisect.bisect_right(ys, y)-1))
        grid[row][col].append(index)
        assigned.add(index)
    if not assigned:
        return None
    return grid, assigned


def ocr_with_tables(image, clean, tables, page_size, args, temporary):
    mode = (3 if tables else 6) if args.psm == 'auto' else args.psm
    words = ocr_words(clean, page_size, args, temporary, psm=mode)
    if args.psm == 'auto':
        # Automatic layout can discard isolated signatures/footers separated
        # from the body by whitespace. Sparse OCR complements it outside tables;
        # cell OCR below handles table contents with its own preprocessing.
        sparse = ocr_words(clean, page_size, args, temporary, psm=11)
        for word in sparse:
            if word.confidence is None or word.confidence < .6 or not any(c.isalnum() for c in word.text):
                continue
            if any(contains(table['bbox'], *centre(word.bbox)) for table in tables):
                continue
            if any(words_overlap(word.bbox, existing.bbox) for existing in words):
                continue
            word.line = (3000, *word.line)
            words.append(word)
    if not tables or not args.cell_ocr:
        return words
    sx, sy = image.width/page_size[0], image.height/page_size[1]
    for table_index, table in enumerate(tables):
        if table['method'] != 'raster_lines':
            continue
        cells = table['cells']
        replacement = []
        for cell_index, cell in enumerate(cells):
            # Exclude structural borders. Add a white margin so Tesseract does
            # not mistake a tight crop for a clipped word.
            left, top = max(0, round(cell[0]*sx)+2), max(0, round(cell[1]*sy)+2)
            right, bottom = min(image.width, round(cell[2]*sx)-2), min(image.height, round(cell[3]*sy)-2)
            if right <= left or bottom <= top:
                continue
            crop = clean.crop((left, top, right, bottom))
            if np.mean(np.asarray(crop.convert('L')) < 180) < .003:
                continue
            crop, scale = prepare_cell_image(crop)
            pad = 12
            crop = ImageOps.expand(crop, border=pad, fill='white')
            cell_words = ocr_words(crop, [crop.width/(sx*scale), crop.height/(sy*scale)],
                                   args, temporary, psm=6)
            for word in cell_words:
                dx, dy = (left-pad/scale)/sx, (top-pad/scale)/sy
                word.bbox = [word.bbox[0]+dx, word.bbox[1]+dy, word.bbox[2]+dx, word.bbox[3]+dy]
                word.line = (1000+table_index, cell_index, *word.line)
                for char in word.characters:
                    b = char['bbox']
                    char['bbox'] = [b[0]+dx, b[1]+dy, b[2]+dx, b[3]+dy]
                replacement.append(word)
        words = [word for word in words if not contains(table['bbox'], *centre(word.bbox))]
        words.extend(replacement)
    return words


def words_overlap(a, b):
    intersection = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
    smaller = min((a[2]-a[0])*(a[3]-a[1]), (b[2]-b[0])*(b[3]-b[1]))
    return smaller > 0 and intersection / smaller >= .5


class MarkdownWriter:
    def __init__(self):
        self.parts = []
        self.length = 0
        self.characters = []
        self.unmapped_character_count = 0

    def write(self, value):
        self.parts.append(value)
        self.length += len(value)

    def word(self, word: Word, page: int, page_size):
        # Escape Markdown syntax without destroying the mapping to source text.
        char_spans = []
        for char in word.text:
            char_start = self.length
            self.write('\\' + char if char in '\\`*_{}[]<>|#!' else char)
            char_spans.append([char_start, self.length])
        # One Unicode code point per exported record. A ligature can give several
        # code points the same real glyph rectangle; never subdivide that box.
        glyphs = [(text, character['bbox']) for character in word.characters
                  for text in character['text']]
        recognized = ''.join(text for text, _ in glyphs)
        alignment = {}
        for block in SequenceMatcher(None, recognized, word.text, autojunk=False).get_matching_blocks():
            for offset in range(block.size):
                alignment[block.a + offset] = block.b + offset
        mapped = set()
        for index, (text, bounds) in enumerate(glyphs):
            x0, y0 = max(0.0, bounds[0]), max(0.0, bounds[1])
            x1, y1 = min(page_size[0], bounds[2]), min(page_size[1], bounds[3])
            if x1 <= x0 or y1 <= y0:
                continue
            position = alignment.get(index)
            if position is not None:
                mapped.add(position)
            self.characters.append({
                'id': f'p{page}-c{len(self.characters)+1}', 'text': text, 'page': page,
                'markdown_span': char_spans[position] if position is not None else None,
                'bbox_normalized': [x0/page_size[0], y0/page_size[1],
                                    x1/page_size[0], y1/page_size[1]],
            })
        self.unmapped_character_count += sum(not char.isspace() and i not in mapped
                                            for i, char in enumerate(word.text))

    def text(self):
        return ''.join(self.parts)


def write_page(writer: MarkdownWriter, words, tables, page_number, page_size):
    writer.write(f'<!-- page: {page_number} -->\n\n')
    used = set()
    units = []
    for table in tables:
        result = table_grid(table, words)
        if result:
            grid, assigned = result
            if assigned & used:
                continue
            used.update(assigned)
            units.append((table['bbox'][1], table['bbox'][0], 'table', (table, grid)))
    lines = defaultdict(list)
    for index, word in enumerate(words):
        if index not in used:
            lines[word.line].append(index)
    for indices in lines.values():
        units.append((min(words[i].bbox[1] for i in indices),
                      min(words[i].bbox[0] for i in indices), 'line', indices))
    for _, _, kind, data in sorted(units, key=lambda item: (item[0], item[1])):
        if kind == 'line':
            for n, index in enumerate(data):
                if n:
                    writer.write(' ')
                writer.word(words[index], page_number, page_size)
            writer.write('\n\n')
        else:
            table, data = data
            for row, cells in enumerate(data):
                writer.write('| ')
                for col, indices in enumerate(cells):
                    previous_line = None
                    for n, index in enumerate(indices):
                        word = words[index]
                        if n:
                            writer.write('<br>' if word.line != previous_line else ' ')
                        writer.word(word, page_number, page_size)
                        previous_line = word.line
                    writer.write(' | ' if col < len(cells)-1 else ' |\n')
                if row == 0:
                    writer.write('|' + ' --- |' * len(cells) + '\n')
            writer.write('\n')


def convert(source: Path, output: Path, args) -> dict:
    source, output = source.resolve(), output.resolve()
    if not source.is_file():
        raise ConversionError(f'Input file does not exist: {source}')
    if source == output:
        raise ConversionError('Input and output must be different files.')
    sidecar = Path(args.boxes).resolve() if args.boxes else output.with_suffix('.boxes.json')
    assets = output.parent / (output.stem + '.assets')
    if source == sidecar or source.is_relative_to(assets):
        raise ConversionError('Output artifacts would overlap the input.')
    if sidecar == output or sidecar.is_relative_to(assets):
        raise ConversionError('Markdown, sidecar and assets must have distinct paths.')
    output.parent.mkdir(parents=True, exist_ok=True)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)
    writer = MarkdownWriter()
    pages, warnings = [], []
    with tempfile.TemporaryDirectory(prefix='document-ocr-') as temporary:
        temporary = Path(temporary)
        preview = temporary / 'preview.pdf'
        renderer, image_info = prepare_pdf(source, preview, args)
        spreadsheet = None
        if source.suffix.lower() == '.xlsx':
            try:
                from spreadsheet_layout import SpreadsheetLayout
            except ImportError as exc:
                raise ConversionError('XLSX structure requires Docling. Install requirements.txt in your Python environment.') from exc
            report = json.loads((temporary/'office-report.json').read_text(encoding='utf-8'))
            if 'spreadsheet_layout' not in report:
                raise ConversionError('Office image lacks cell geometry; rebuild it with sandbox-build.')
            spreadsheet = SpreadsheetLayout(temporary/'recalculated.xlsx', report['spreadsheet_layout'])
        if renderer in ('libreoffice_docker', 'text_render'):
            warnings.append('Coordinates refer to the fixed preview.pdf rendering; office/text files have no intrinsic page geometry.')
        if source.suffix.lower() == '.xlsx':
            warnings.append('All XLSX formulas were explicitly recalculated in the network-disabled office sandbox. '
                            'See recalculated.xlsx and office-report.json; the source XLSX was not modified.')
        with fitz.open(preview) as doc:
            if doc.needs_pass:
                raise ConversionError('Encrypted PDF: decrypt it locally before conversion.')
            if spreadsheet and len(doc) != len(spreadsheet.sheets):
                raise ConversionError('Spreadsheet sheets and PDF pages do not match; refusing ambiguous coordinates.')
            for index, page in enumerate(doc):
                number = index + 1
                original_rotation = page.rotation
                original_cropbox = list(page.cropbox)
                # The stored preview preserves rotation/crop. All exported geometry
                # uses DISPLAY coordinates (top-left of the visible rotated page).
                size = [page.rect.width, page.rect.height]
                native = native_words(page)
                render_dpi = 72 if renderer == 'image_pixels' else args.dpi
                pix = page.get_pixmap(dpi=render_dpi, alpha=False, colorspace=fitz.csRGB)
                image = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
                sx, sy = image.width/size[0], image.height/size[1]
                tables, clean = raster_tables(image, sx, sy) if args.tables and not spreadsheet else ([], image)
                use_ocr = not spreadsheet and (args.ocr == 'always' or (args.ocr == 'auto' and not native))
                if use_ocr:
                    print(f'Page {number}/{len(doc)}: OCR', file=sys.stderr)
                    words = ocr_with_tables(image, clean, tables, size, args, temporary)
                else:
                    words = native
                    for word in words:
                        word.bbox = list(fitz.Rect(word.bbox) * page.rotation_matrix)
                        for character in word.characters:
                            character['bbox'] = list(fitz.Rect(character['bbox']) * page.rotation_matrix)
                    if args.tables and not original_rotation and not spreadsheet:
                        try:
                            vector = page.find_tables()
                            detected = [{'bbox': list(t.bbox), 'cells': [list(c) for c in t.cells if c],
                                         'method': 'pdf_lines'} for t in vector.tables]
                            if detected:
                                tables = detected
                        except Exception as exc:
                            warnings.append(f'Page {number}: vector table detection failed: {exc}')
                if not words:
                    warnings.append(f'Page {number}: no text recognized; inspect the preview for blank or unsupported content.')
                if args.ocr == 'auto' and native and page.get_images():
                    # Mixed PDFs can have a native title over a scanned body.
                    # OCR image regions, retaining native tokens where available.
                    for image_index, info in enumerate(page.get_image_info()):
                        rect = (fitz.Rect(info['bbox']) * page.rotation_matrix) & page.rect
                        if rect.is_empty:
                            continue
                        pixel_rect = [max(0, round(rect.x0*sx)), max(0, round(rect.y0*sy)),
                                      min(image.width, round(rect.x1*sx)), min(image.height, round(rect.y1*sy))]
                        crop = image.crop(pixel_rect)
                        if crop.width < 20 or crop.height < 20:
                            continue
                        region_psm = 6 if crop.height < crop.width * .3 else (3 if args.psm == 'auto' else args.psm)
                        extra = ocr_words(crop, [crop.width/sx, crop.height/sy], args, temporary, psm=region_psm)
                        for word in extra:
                            dx, dy = pixel_rect[0]/sx, pixel_rect[1]/sy
                            b = word.bbox
                            word.bbox = [b[0]+dx, b[1]+dy, b[2]+dx, b[3]+dy]
                            if any(contains(n.bbox, *centre(word.bbox), 1) for n in words):
                                continue
                            for char in word.characters:
                                b = char['bbox']
                                char['bbox'] = [b[0]+dx, b[1]+dy, b[2]+dx, b[3]+dy]
                            word.line = (2000+image_index, *word.line)
                            words.append(word)
                    words.sort(key=lambda word: (word.bbox[1], word.bbox[0]))
                visible_words = []
                for word in words:
                    bounds = fitz.Rect(word.bbox)
                    visible = bounds & page.rect
                    if visible.is_empty:
                        continue
                    if any(abs(a-b) > .001 for a, b in zip(visible, bounds)):
                        word.clipped = True
                        word.bbox = list(visible)
                        characters = []
                        for char in word.characters:
                            char_bounds = fitz.Rect(char['bbox']) & page.rect
                            if not char_bounds.is_empty:
                                characters.append({**char, 'bbox': list(char_bounds)})
                        word.characters = characters
                    visible_words.append(word)
                words = visible_words
                clipped_count = sum(word.clipped for word in words)
                if clipped_count:
                    warnings.append(f'Page {number}: {clipped_count} text tokens extend outside the visible page; '
                                    'character bounds are clipped. Extracted text may include cropped characters.')
                character_start = len(writer.characters)
                unmapped_start = writer.unmapped_character_count
                if spreadsheet:
                    table_count = spreadsheet.write_page(writer, page, number)
                else:
                    write_page(writer, words, tables, number, size)
                    table_count = sum(bool(table_grid(t, words)) for t in tables)
                unmapped = writer.unmapped_character_count - unmapped_start
                if unmapped:
                    warnings.append(f'Page {number}: {unmapped} source-text characters have no aligned visible glyph box; '
                                    'no coordinates were fabricated for them.')
                low_confidence = sum(w.confidence is not None and w.confidence < .6 for w in words)
                if low_confidence:
                    warnings.append(f'Page {number}: {low_confidence} words have OCR confidence below 0.60.')
                # Reuse the original raster for an exact visual reference.
                page_image = assets / f'page-{number:04d}.png'
                pix.save(page_image)
                info = {'page': number, 'width': size[0], 'height': size[1],
                        'unit': 'image_pixel' if renderer == 'image_pixels' else 'pdf_point',
                        'rotation': original_rotation, 'cropbox_unrotated': original_cropbox,
                        'image': os.path.relpath(page_image, sidecar.parent),
                        'image_width': pix.width, 'image_height': pix.height,
                        'word_count': len(words), 'ocr': any(w.method == 'tesseract' for w in words),
                        'character_count': len(writer.characters) - character_start,
                        'unmapped_character_count': unmapped,
                        'table_count': table_count,
                        }
                if image_info:
                    info.update(image_info[index])
                pages.append(info)
        shutil.copyfile(preview, assets / 'preview.pdf')
        office_report = None
        if (temporary/'office-report.json').is_file():
            office_report = json.loads((temporary/'office-report.json').read_text(encoding='utf-8'))
            # Cell rectangles are transient alignment data. Persist only the
            # requested normalized character geometry, not duplicate cell boxes.
            office_report.pop('spreadsheet_layout', None)
            (assets/'office-report.json').write_text(
                json.dumps(office_report, ensure_ascii=False, indent=2), encoding='utf-8')
        if (temporary/'recalculated.xlsx').is_file():
            shutil.copyfile(temporary/'recalculated.xlsx', assets/'recalculated.xlsx')
    markdown = writer.text()
    manifest = {'schema_version': '2.0',
                'source': {'path': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                           'format': source.suffix.lower().lstrip('.')},
                'markdown': {'path': os.path.relpath(output, sidecar.parent),
                             'sha256': hashlib.sha256(markdown.encode('utf-8')).hexdigest(),
                             'offset_unit': 'unicode_codepoint', 'span_convention': '[start,end)'},
                'coordinate_system': {'origin': 'top-left', 'axes': 'x-right,y-down',
                                      'bbox_format': '[x0,y0,x1,y1]',
                                      'space': 'visible_rotated_page',
                                      'normalized_range': [0, 1]},
                'renderer': renderer,
                'office_processing': None if office_report is None else {
                    'engine': office_report['engine'], 'recalculated': office_report['recalculated'],
                    'formula_count': office_report.get('formula_count', 0),
                    'report': os.path.relpath(assets/'office-report.json', sidecar.parent),
                    'recalculated_workbook': os.path.relpath(assets/'recalculated.xlsx', sidecar.parent)
                    if source.suffix.lower() == '.xlsx' else None,
                    'isolation': office_report['isolation']},
                'preview_pdf': os.path.relpath(assets / 'preview.pdf', sidecar.parent),
                'pages': pages, 'characters': writer.characters, 'warnings': warnings}
    output.write_text(markdown, encoding='utf-8', newline='')
    sidecar.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


def characters_for_span(data: dict, start: int, end: int) -> list[str]:
    if start < 0 or end <= start:
        raise ConversionError('Markdown span must satisfy 0 <= start < end.')
    return [character['id'] for character in data['characters']
            if character['markdown_span'] is not None
            and character['markdown_span'][0] < end and character['markdown_span'][1] > start]


def highlight(manifest_path: Path, character_ids: list[str], output: Path, span=None):
    manifest_path = manifest_path.resolve()
    data = json.loads(manifest_path.read_text(encoding='utf-8'))
    if data.get('schema_version') != '2.0':
        raise ConversionError('Unsupported sidecar schema; convert the document again to create character geometry.')
    if span is not None:
        markdown = (manifest_path.parent / data['markdown']['path']).read_text(encoding='utf-8')
        if hashlib.sha256(markdown.encode('utf-8')).hexdigest() != data['markdown']['sha256']:
            raise ConversionError('Markdown was edited; offsets no longer match the sidecar.')
        if span[1] > len(markdown):
            raise ConversionError('Markdown span is outside the document.')
        character_ids = characters_for_span(data, *span)
        if not character_ids:
            raise ConversionError('No source characters intersect the selected Markdown span.')
    wanted = set(character_ids)
    selected = [character for character in data['characters'] if character['id'] in wanted]
    missing = wanted - {character['id'] for character in selected}
    if missing:
        raise ConversionError('Unknown character IDs: ' + ', '.join(sorted(missing)))
    source = (manifest_path.parent / data['preview_pdf']).resolve()
    output = output.resolve()
    if output in (source, Path(data['source']['path']).resolve(), manifest_path,
                  (manifest_path.parent / data['markdown']['path']).resolve()):
        raise ConversionError('Highlight output must not overwrite an input artifact.')
    with fitz.open(source) as doc:
        for character in selected:
            page = doc[character['page']-1]
            x0, y0, x1, y1 = character['bbox_normalized']
            rect = fitz.Rect(x0 * page.rect.width, y0 * page.rect.height,
                             x1 * page.rect.width, y1 * page.rect.height) * page.derotation_matrix
            annotation = page.add_rect_annot(rect)
            annotation.set_colors(stroke=(1, .15, .1), fill=(1, .9, 0))
            annotation.set_opacity(.3)
            annotation.update()
        output.parent.mkdir(parents=True, exist_ok=True)
        doc.save(output)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--boxes', help='JSON sidecar path; default OUTPUT.boxes.json')
    p.add_argument('--ocr', choices=['auto', 'always', 'never'], default='auto')
    p.add_argument('--lang', default='rus+eng')
    p.add_argument('--dpi', type=int, default=300)
    p.add_argument('--psm', choices=['auto', '3', '4', '6', '7', '11', '12', '13'], default='auto',
                   help='Auto chooses a single text block for plain documents, layout mode for tables')
    p.add_argument('--timeout', type=int, default=180)
    p.add_argument('--tesseract')
    p.add_argument('--tessdata')
    p.add_argument('--sandbox-image', default=DEFAULT_IMAGE, help='Trusted local office sandbox image')
    p.add_argument('--docker', help='Docker executable; only local Linux engines are allowed')
    p.add_argument('--encoding', default='utf-8-sig')
    p.add_argument('--font')
    p.add_argument('--no-tables', dest='tables', action='store_false', default=True)
    p.add_argument('--no-cell-ocr', dest='cell_ocr', action='store_false', default=True,
                   help='Faster scan OCR, without a separate pass for each ruled-table cell')
    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if argv and argv[0] == 'sandbox-build':
            p = argparse.ArgumentParser(description='Build the local office sandbox image (no documents are used).')
            p.add_argument('--sandbox-image', default=DEFAULT_IMAGE)
            p.add_argument('--docker')
            args = p.parse_args(argv[1:])
            build_image(args.sandbox_image, args.docker)
        elif argv and argv[0] == 'highlight':
            p = argparse.ArgumentParser(description='Highlight characters on the stored PDF preview.')
            p.add_argument('boxes', type=Path)
            p.add_argument('output', type=Path)
            selection = p.add_mutually_exclusive_group(required=True)
            selection.add_argument('--characters', nargs='+')
            selection.add_argument('--span', nargs=2, type=int, metavar=('START', 'END'))
            args = p.parse_args(argv[1:])
            highlight(args.boxes, args.characters or [], args.output, args.span)
            print(args.output)
        else:
            p = parser()
            args = p.parse_args(argv)
            if args.dpi < 72 or args.dpi > 600:
                p.error('--dpi must be between 72 and 600')
            if args.timeout <= 0:
                p.error('--timeout must be positive')
            manifest = convert(args.input, args.output, args)
            print(f"{args.output}: {len(manifest['pages'])} pages, {len(manifest['characters'])} characters")
            for warning in manifest['warnings']:
                print('Warning: ' + warning, file=sys.stderr)
        return 0
    except (ConversionError, OSError, ValueError, RuntimeError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

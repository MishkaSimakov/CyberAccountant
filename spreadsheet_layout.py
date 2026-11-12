"""Read XLSX cells with openpyxl; preserve Calc display values and PDF glyphs."""
from __future__ import annotations

import re
from types import SimpleNamespace

from openpyxl import load_workbook
from tabulate import tabulate


class SpreadsheetLayout:
    def __init__(self, workbook, sheets):
        self.sheets = sheets
        self.workbook = load_workbook(workbook, data_only=True, keep_links=False)

    def write_page(self, writer, page, number):
        sheet = self.sheets[number-1]
        worksheet = self.workbook.worksheets[number-1]
        cells = {(c['row'], c['col']): c for c in sheet['cells'] if c['text'].strip()}
        glyphs = [dict(text=char['c'], bbox=list(char['bbox']))
                  for block in page.get_text('rawdict')['blocks']
                  for line in block.get('lines', []) for span in line['spans']
                  for char in span['chars']]
        writer.write(f'<!-- page: {number} -->\n\n')

        def emit(cell):
            text = re.sub(r'\s+', ' ', cell['text']).strip()
            x0, y0, x1, y1 = cell['rect']
            characters = [c for c in glyphs
                          if x0 <= (c['bbox'][0]+c['bbox'][2])/2 < x1
                          and y0 <= (c['bbox'][1]+c['bbox'][3])/2 < y1]
            writer.word(SimpleNamespace(text=text, characters=characters), number,
                        [page.rect.width, page.rect.height])

        rows = {}
        for (row, col), cell in sorted(cells.items()):
            rows.setdefault(row, []).append(cell)
        blocks = []
        for row in rows:
            if not blocks or row != blocks[-1][-1]+1:
                blocks.append([])
            blocks[-1].append(row)
        count = 0
        for block in blocks:
            # Titles and metadata above a table occupy one source cell per row.
            # Emit each once, including titles merged across several columns.
            while block and len(rows[block[0]]) == 1:
                emit(rows[block.pop(0)][0])
                writer.write('\n\n')
            if not block:
                continue
            first_col = min(c['col'] for row in block for c in rows[row])
            last_col = max(c['col'] for row in block for c in rows[row])
            markers = {}
            matrix = []
            for row in block:
                values = []
                for col in range(first_col, last_col+1):
                    source = cells.get((row, col))
                    value = ''
                    if source:
                        value = f'ZQOCR{len(markers)}QZ'
                        markers[value] = source
                    values.append(value)
                matrix.append(values)
            first = [worksheet.cell(block[0]+1, c['col']+1) for c in rows[block[0]]]
            text_header = all(isinstance(c.value, str) and not c.value.isdigit() for c in first)
            has_numbers = any(isinstance(worksheet.cell(r+1, c['col']+1).value, (int, float))
                              for r in block[1:] for c in rows[r])
            header = text_header and (all(c.font.bold for c in first) or has_numbers)
            headers = matrix.pop(0) if header else ['']*(last_col-first_col+1)
            # The library renders Markdown; markers keep identical values tied
            # to their own source cells and exact Markdown offsets.
            rendered = tabulate(matrix, headers=headers, tablefmt='github', disable_numparse=True)
            position = 0
            for match in re.finditer(r'ZQOCR\d+QZ', rendered):
                writer.write(rendered[position:match.start()])
                emit(markers[match.group()])
                position = match.end()
            writer.write(rendered[position:]+'\n\n')
            count += 1
        return count

"""Docling extracts XLSX structure; Calc and PyMuPDF supply real glyph geometry."""
from __future__ import annotations

import re
from types import SimpleNamespace

from docling.document_converter import DocumentConverter, ExcelFormatOption
from docling.datamodel.backend_options import MsExcelBackendOptions
from docling.datamodel.base_models import InputFormat
from docling_core.types.doc import TableItem


class SpreadsheetLayout:
    def __init__(self, workbook, sheets):
        self.sheets = sheets
        self.document = DocumentConverter(
            allowed_formats=[InputFormat.XLSX],
            format_options={InputFormat.XLSX: ExcelFormatOption(
                backend_options=MsExcelBackendOptions(parse_charts=False))},
        ).convert(workbook).document

    def write_page(self, writer, page, number):
        sheet = self.sheets[number-1]
        cells = {(c['row'], c['col']): c for c in sheet['cells']}
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

        count = 0
        for table, _ in self.document.iterate_items():
            if not table.prov or table.prov[0].page_no != number:
                continue
            origin = table.prov[0].bbox
            if not isinstance(table, TableItem):
                source = cells.get((int(origin.t), int(origin.l)))
                if source and getattr(table, 'text', None):
                    emit(source)
                    writer.write('\n\n')
                continue
            entries = []
            for cell in table.data.table_cells:
                key = (int(origin.t)+cell.start_row_offset_idx,
                       int(origin.l)+cell.start_col_offset_idx)
                source = cells.get(key)
                if source and source['text'].strip():
                    entries.append((cell, source))
            # A merged title followed by one-cell metadata rows is prose, not
            # a table header to repeat in every Markdown column.
            rows = [cell.start_row_offset_idx for cell, _ in entries]
            if (any(cell.col_span == table.data.num_cols for cell, _ in entries)
                    and len(rows) == len(set(rows))):
                for _, source in entries:
                    emit(source)
                    writer.write('\n\n')
                continue
            if not entries:
                continue
            clone = table.model_copy(deep=True)
            markers = {}
            for cell in clone.data.table_cells:
                key = (int(origin.t)+cell.start_row_offset_idx,
                       int(origin.l)+cell.start_col_offset_idx)
                source = cells.get(key)
                cell.text = ''
                if source and source['text'].strip():
                    marker = f'ZQOCR{len(markers)}QZ'
                    markers[marker] = source
                    cell.text = marker
            # Docling's serializer handles Markdown tables, spans and escaping.
            # Unique markers retain exact cell offsets even for repeated values.
            rendered = clone.export_to_markdown(doc=self.document)
            position = 0
            for match in re.finditer(r'ZQOCR\d+QZ', rendered):
                writer.write(rendered[position:match.start()])
                emit(markers[match.group()])
                position = match.end()
            writer.write(rendered[position:]+'\n\n')
            count += 1
        return count

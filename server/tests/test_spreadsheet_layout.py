"""Spreadsheet extraction and highlighting with fixed Office-service artifacts."""
import copy
import hashlib
import json
import shutil

import openpyxl
from openpyxl.styles import Font
import pymupdf as fitz
import pytest

from server.app.ocr import converter


@pytest.fixture
def office_artifacts(tmp_path, monkeypatch):
    source = tmp_path / 'payroll.xlsx'
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.merge_cells('A1:D1')
    sheet['A1'] = 'Payroll statement'
    sheet['A2'] = 'Project: 01 | January'
    for col, value in enumerate(['Code', 'Description', 'Amount', 'Basis'], 1):
        sheet.cell(4, col, value).font = Font(bold=True)
    for row in [('0201', 'Salary', 100.25, 'Timesheet'),
                ('0202', 'Additional salary', 100.25, 'Timesheet'),
                ('', 'TOTAL', 200.5, '')]:
        sheet.append(row)
    for row in range(5, 8):
        sheet.cell(row, 3).number_format = '0.00'
    book.save(source)
    preview = tmp_path / 'preview.pdf'
    cells = []
    with fitz.open() as doc:
        page = doc.new_page(width=800, height=500)
        for row in sheet:
            for cell in row:
                if cell.value is None:
                    continue
                r, c = cell.row - 1, cell.column - 1
                text = f'{cell.value:.2f}' if isinstance(cell.value, float) else str(cell.value)
                rect = [c * 190, r * 40, (c + 1) * 190, (r + 1) * 40]
                if cell.coordinate in ('A1', 'A2'):
                    rect[2] = 760
                page.insert_text((rect[0] + 5, rect[1] + 20), text, fontsize=10)
                cells.append({'row': r, 'col': c, 'text': text, 'rect': rect})
        doc.save(preview)
    report = {
        'engine': 'libreoffice', 'recalculated': True,
        'isolation': {'type': 'docker_service', 'transport': 'http'},
        'spreadsheet_layout': [{'name': sheet.title, 'page': 1, 'cells': cells}],
    }

    def render(original, destination, **kwargs):
        shutil.copyfile(preview, destination)
        shutil.copyfile(source, destination.with_name('recalculated.xlsx'))
        return copy.deepcopy(report)

    def unexpected_ocr(*args, **kwargs):
        pytest.fail('Spreadsheets must use workbook cells instead of raster recognition.')

    monkeypatch.setattr(converter, 'render_in_sandbox', render)
    monkeypatch.setattr(converter, 'ocr_words', unexpected_ocr)
    monkeypatch.setattr(converter, 'raster_tables', unexpected_ocr)
    return source, preview, report


@pytest.mark.parametrize('ocr', ['auto', 'always', 'never'])
def test_excel_tables_display_values_and_repeated_amount_highlights(office_artifacts, tmp_path, ocr):
    source, preview, report = office_artifacts
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    output = tmp_path / 'result.md'
    args = converter.parser().parse_args([str(source), str(output), '--ocr', ocr])
    data = converter.convert(source, output, args)
    markdown = output.read_text(encoding='utf-8')
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    assert markdown.count('Payroll statement') == 1
    assert 'Project: 01 \\| January' in markdown
    rows = [line for line in markdown.splitlines() if line.startswith('|')]
    assert len(rows) == 5
    assert 'Code' in rows[0] and 'Amount' in rows[0]
    assert '0201' in rows[2] and '0202' in rows[3]
    assert 'TOTAL' in rows[4] and '200.50' in rows[4]
    assert data['layout_backend'] == 'openpyxl'
    assert data['pages'][0]['table_count'] == 1
    assert not data['pages'][0]['ocr']
    saved_report = json.loads((tmp_path / 'result.assets/office-report.json').read_text(encoding='utf-8'))
    assert 'spreadsheet_layout' not in saved_report
    assert (tmp_path / 'result.assets/recalculated.xlsx').read_bytes() == source.read_bytes()
    for char in data['characters']:
        if char['markdown_span'] is not None:
            start, end = char['markdown_span']
            assert markdown[start:end] in (char['text'], '\\' + char['text'])
        x0, y0, x1, y1 = char['bbox_normalized']
        assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
    for md_row, source_row in [(rows[2], 4), (rows[3], 5)]:
        start = markdown.index(md_row) + md_row.index('100.25')
        ids = converter.characters_for_span(data, start, start + 6)
        characters = [c for c in data['characters'] if c['id'] in ids]
        assert ''.join(c['text'] for c in characters) == '100.25'
        cell = next(c for c in report['spreadsheet_layout'][0]['cells']
                    if c['row'] == source_row and c['col'] == 2)
        for char in characters:
            x0, y0, x1, y1 = char['bbox_normalized']
            assert cell['rect'][0] <= (x0 + x1) / 2 * 800 <= cell['rect'][2]
            assert cell['rect'][1] <= (y0 + y1) / 2 * 500 <= cell['rect'][3]
        highlighted = tmp_path / f'highlight-{source_row}.pdf'
        converter.highlight(output.with_suffix('.boxes.json'), [], highlighted, [start, start + 6])
        with fitz.open(highlighted) as doc:
            page = doc[0]
            annotations = list(page.annots())
            assert len(annotations) == 6
            assert all(cell['rect'][1] <= annotation.rect.y0 < cell['rect'][3] for annotation in annotations)


@pytest.mark.parametrize('missing_layout', [True, False])
def test_missing_geometry_or_page_mismatch_is_rejected(office_artifacts, tmp_path, missing_layout):
    source, preview, report = office_artifacts
    if missing_layout:
        del report['spreadsheet_layout']
        message = 'rebuild the office_sandbox image'
    else:
        report['spreadsheet_layout'].append({'name': 'Extra', 'page': 2, 'cells': []})
        message = 'refusing ambiguous coordinates'
    output = tmp_path / 'result.md'
    args = converter.parser().parse_args([str(source), str(output)])
    with pytest.raises(converter.ConversionError, match=message):
        converter.convert(source, output, args)
    assert not output.exists()

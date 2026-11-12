"""Integration coverage for the running Office service (opt in)."""
import hashlib
import json
import os
import xml.etree.ElementTree as ET
import zipfile

import pytest
from server.app.ocr import converter as document_ocr


@pytest.mark.skipif(os.environ.get('OCR_SANDBOX_TESTS') != '1',
                    reason='Requires the running Office sandbox service')
def test_real_sandbox_recalculates_stale_zero_preserves_source_and_geometry(tmp_path):
    import openpyxl
    source = tmp_path / 'stale.xlsx'
    book = openpyxl.Workbook()
    sheet = book.active
    sheet['A1'], sheet['A2'], sheet['A3'] = 100.25, 49.75, '=SUM(A1:A2)'
    book.calculation.calcMode = 'manual'
    book.save(source)
    with zipfile.ZipFile(source) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    namespace = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    worksheet = ET.fromstring(entries['xl/worksheets/sheet1.xml'])
    cell = worksheet.find(f'.//{namespace}c[@r="A3"]')
    cell.find(namespace + 'v').text = '0'
    entries['xl/worksheets/sheet1.xml'] = ET.tostring(worksheet, encoding='utf-8')
    with zipfile.ZipFile(source, 'w') as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    assert openpyxl.load_workbook(source, data_only=True).active['A3'].value == 0
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    output = tmp_path / 'result.md'
    args = document_ocr.parser().parse_args([str(source), str(output)])
    data = document_ocr.convert(source, output, args)
    assert original_hash == hashlib.sha256(source.read_bytes()).hexdigest()
    assert openpyxl.load_workbook(tmp_path / 'result.assets/recalculated.xlsx', data_only=True).active[
               'A3'].value == 150
    markdown = output.read_text(encoding='utf-8')
    start = markdown.index('150')
    ids = document_ocr.characters_for_span(data, start, start + 3)
    assert ''.join(c['text'] for c in data['characters'] if c['id'] in ids) == '150'
    assert data['office_processing']['recalculated'] is True
    report = json.loads((tmp_path / 'result.assets/office-report.json').read_text(encoding='utf-8'))
    assert report['formula_results'][0]['numeric_value'] == 150
    assert report['formulas_before'][0]['cached_before'] == '0'
    markdown = output.read_text(encoding='utf-8')
    for char in data['characters']:
        if char['markdown_span'] is not None:
            start, end = char['markdown_span']
            assert markdown[start:end] in (char['text'], '\\' + char['text'])
        assert 'bbox' not in char
        assert all(0 <= value <= 1 for value in char['bbox_normalized'])


@pytest.mark.skipif(os.environ.get('OCR_SANDBOX_TESTS') != '1', reason='Requires the running Office sandbox service')
def test_xlsx_structure_merged_title_wrapping_repeated_values_and_geometry(tmp_path, monkeypatch):
    import openpyxl
    from openpyxl.styles import Alignment
    from server.app.ocr.spreadsheet_layout import SpreadsheetLayout
    captured = []
    original_write = SpreadsheetLayout.write_page

    def capture_layout(self, *args):
        captured[:] = self.sheets
        return original_write(self, *args)

    monkeypatch.setattr(SpreadsheetLayout, 'write_page', capture_layout)
    source = tmp_path / 'payroll.xlsx'
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.merge_cells('A1:D1')
    sheet['A1'] = 'Payroll statement'
    sheet['A2'] = 'Project: 01 | Period: January'
    sheet.append([])
    for index, value in enumerate(['Code', 'Description', 'Amount', 'Basis'], 1):
        sheet.cell(4, index, value)
    for row in [('0201', 'Salary', 100.25, 'Timesheet'),
                ('0202', 'Additional salary and a long description', 100.25, 'Timesheet'),
                ('', 'TOTAL', '=SUM(C5:C6)', '')]:
        sheet.append(row)
    for row in sheet:
        for cell in row:
            cell.alignment = Alignment(wrap_text=True)
    for row in range(5, 8):
        sheet.cell(row, 3).number_format = '0.00'
    sheet.column_dimensions['A'].width = 13
    sheet.column_dimensions['B'].width = 21
    sheet.column_dimensions['C'].width = 13
    sheet.column_dimensions['D'].width = 15
    book.save(source)
    output = tmp_path / 'payroll.md'
    args = document_ocr.parser().parse_args([str(source), str(output)])
    data = document_ocr.convert(source, output, args)
    md = output.read_text(encoding='utf-8')
    assert md.count('Payroll statement') == 1
    assert 'Project: 01 \\| Period: January' in md
    rows = [line for line in md.splitlines() if line.startswith('|')]
    assert len(rows) == 5 and all(len(line.split('|')) == 6 for line in rows)
    assert '0201' in rows[2] and '0202' in rows[3]
    assert 'TOTAL' in rows[4] and '200.50' in rows[4]
    report = json.loads((tmp_path / 'payroll.assets/office-report.json').read_text(encoding='utf-8'))
    assert 'spreadsheet_layout' not in report
    source_cells = captured[0]['cells']
    page = data['pages'][0]
    for md_row, source_row in [(rows[2], 4), (rows[3], 5)]:
        start = md.index(md_row) + md_row.index('100.25')
        ids = document_ocr.characters_for_span(data, start, start + 6)
        chars = [c for c in data['characters'] if c['id'] in ids]
        assert ''.join(c['text'] for c in chars) == '100.25'
        rect = next(c['rect'] for c in source_cells if c['row'] == source_row and c['col'] == 2)
        for char in chars:
            x0, y0, x1, y1 = char['bbox_normalized']
            assert rect[0] <= (x0 + x1) / 2 * page['width'] <= rect[2]
            assert rect[1] <= (y0 + y1) / 2 * page['height'] <= rect[3]


@pytest.mark.skipif(os.environ.get('OCR_SANDBOX_TESTS') != '1', reason='Requires the running Office sandbox service')
def test_xlsx_multiple_sheets_empty_sheet_and_offset_cells(tmp_path):
    import openpyxl
    book = openpyxl.Workbook()
    book.active['C5'] = 'First sheet value'
    book.create_sheet('Empty')
    second = book.create_sheet('Second')
    second.merge_cells('A1:B1')
    second['A1'] = 'Merged title'
    second['D7'] = 'Second sheet value'
    source, output = tmp_path / 'sheets.xlsx', tmp_path / 'sheets.md'
    book.save(source)
    args = document_ocr.parser().parse_args([str(source), str(output)])
    data = document_ocr.convert(source, output, args)
    md = output.read_text(encoding='utf-8')
    assert md.count('Merged title') == 1
    for phrase, expected_page in [('First sheet value', 1), ('Second sheet value', 3)]:
        start = md.index(phrase)
        ids = document_ocr.characters_for_span(data, start, start + len(phrase))
        chars = [c for c in data['characters'] if c['id'] in ids]
        assert ''.join(c['text'] for c in chars).replace(' ', '') == phrase.replace(' ', '')
        assert all(c['page'] == expected_page for c in chars)
    assert data['pages'][1]['character_count'] == 0

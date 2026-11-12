"""Integration coverage for the running Office service (opt in)."""
import hashlib
import json
import os
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
    entries['xl/worksheets/sheet1.xml'] = entries['xl/worksheets/sheet1.xml'].replace(b'<f>SUM(A1:A2)</f><v></v>',
                                                                                      b'<f>SUM(A1:A2)</f><v>0</v>')
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

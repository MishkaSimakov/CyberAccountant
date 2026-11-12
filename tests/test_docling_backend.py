"""Optional local integration checks; model weights are installed separately."""
import hashlib
import os
from pathlib import Path
import socket

import pymupdf as fitz
import pytest

from document_ocr import convert, parser, characters_for_span, highlight

pytestmark = pytest.mark.skipif(os.environ.get('OCR_DOCLING_TESTS') != '1',
                               reason='Requires local Docling models and OCR dependencies')


def check_output(data, output):
    md = output.read_text(encoding='utf-8')
    assert data['layout_backend'] == 'docling'
    assert hashlib.sha256(md.encode()).hexdigest() == data['markdown']['sha256']
    assert len({c['id'] for c in data['characters']}) == len(data['characters'])
    for char in data['characters']:
        assert len(char['text']) == 1 and 'bbox' not in char
        x0, y0, x1, y1 = char['bbox_normalized']
        assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
        if char['markdown_span'] is not None:
            a, b = char['markdown_span']
            assert md[a:b] in (char['text'], '\\'+char['text'])
    return md


def test_docling_native_repeated_amounts_offline_and_highlight(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError('Conversion must use local models without network connections')
    monkeypatch.setattr(socket.socket, 'connect', no_network)
    source, output = tmp_path/'native.pdf', tmp_path/'native.md'
    with fitz.open() as doc:
        page = doc.new_page(width=500, height=400)
        page.insert_text((70, 90), 'First amount 12345.67', fontsize=16)
        page.insert_text((70, 150), 'Second amount 12345.67', fontsize=16)
        doc.save(source)
    args = parser().parse_args([str(source), str(output), '--backend', 'docling', '--ocr', 'never'])
    data = convert(source, output, args)
    md = check_output(data, output)
    assert md.count('12345.67') == 2
    start = md.index('12345.67', md.index('12345.67')+1)
    ids = characters_for_span(data, start, start+8)
    chars = [c for c in data['characters'] if c['id'] in ids]
    assert ''.join(c['text'] for c in chars) == '12345.67'
    assert all(c['bbox_normalized'][1]*400 > 125 for c in chars)
    marked = tmp_path/'marked.pdf'
    highlight(output.with_suffix('.boxes.json'), [], marked, [start, start+1])
    with fitz.open(marked) as doc:
        assert len(list(doc[0].annots())) == 1


def test_docling_real_image_amount_recipient_and_character_mapping(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = root/'данные/12_Акт_связь_0426.png'
    if not source.exists():
        pytest.skip('Image fixture is not available')
    output = tmp_path/'image.md'
    args = parser().parse_args([str(source), str(output), '--backend', 'docling'])
    data = convert(source, output, args)
    md = check_output(data, output)
    assert 'Получатель:' in md and 'ФГАОУ ВО «МФТИ»' in md
    start = md.index('650 000,00')
    ids = characters_for_span(data, start, start+10)
    chars = [c for c in data['characters'] if c['id'] in ids]
    assert ''.join(c['text'] for c in chars) == '650000,00'
    assert data['pages'][0]['unmapped_character_count'] == 0


@pytest.mark.skipif(os.environ.get('OCR_SANDBOX_TESTS') != '1', reason='Requires the Office sandbox')
def test_docling_docx_through_sandbox_preserves_table_and_geometry(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = root/'данные/11_Справка_ИТ_03-26.docx'
    if not source.exists():
        pytest.skip('DOCX fixture is not available')
    output = tmp_path/'docx.md'
    args = parser().parse_args([str(source), str(output), '--backend', 'docling', '--ocr', 'never'])
    data = convert(source, output, args)
    md = check_output(data, output)
    assert any('Описание' in line and 'Сумма, руб.' in line for line in md.splitlines() if line.startswith('|'))
    start = md.index('703 244,51')
    ids = characters_for_span(data, start, start+10)
    assert ''.join(c['text'] for c in data['characters'] if c['id'] in ids) == '703244,51'
    assert data['office_processing']['isolation']['network'] == 'none'
    assert data['pages'][0]['unmapped_character_count'] == 0

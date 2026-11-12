import hashlib
import json
from pathlib import Path

import pymupdf as fitz
import pytest
from PIL import Image, ImageDraw, ImageFont

from server.app.ocr.converter import (ConversionError, MarkdownWriter, Word, convert,
                            find_tesseract, highlight, parser, characters_for_span,
                            prepare_cell_image, recover_open_cells, table_grid)


def perform(source, output, *options):
    args = parser().parse_args([str(source), str(output), *options])
    return convert(args.input, args.output, args)


def check_mapping(data, markdown):
    assert hashlib.sha256(markdown.encode()).hexdigest() == data['markdown']['sha256']
    assert data['schema_version'] == '2.0'
    assert 'tokens' not in data
    assert len({c['id'] for c in data['characters']}) == len(data['characters'])
    for char in data['characters']:
        assert set(char) == {'id', 'text', 'page', 'markdown_span', 'bbox_normalized'}
        assert len(char['text']) == 1
        x0, y0, x1, y1 = char['bbox_normalized']
        assert 0 <= x0 < x1 <= 1
        assert 0 <= y0 < y1 <= 1
        if char['markdown_span'] is not None:
            a, b = char['markdown_span']
            assert markdown[a:b] in (char['text'], '\\' + char['text'])
    assert sum(p['character_count'] for p in data['pages']) == len(data['characters'])


def selected_text(data, markdown, text):
    start = markdown.index(text)
    ids = characters_for_span(data, start, start + len(text))
    return [char for char in data['characters'] if char['id'] in ids]


def test_repeated_numbers_escaping_and_unicode_offsets():
    writer = MarkdownWriter()
    writer.write('Привет 😀\n')
    for text in ['100,00', '100,00', 'a|b', '*c*', '\\', '😀']:
        chars = [{'text': c, 'bbox': [10 + i * 5, 20, 15 + i * 5, 30]} for i, c in enumerate(text)]
        writer.word(Word(text, [10, 20, 60, 30], (0, 0), 'pdf_text', characters=chars), 1, [100, 100])
        writer.write(' ')
    md = writer.text()
    for char in writer.characters:
        a, b = char['markdown_span']
        assert md[a:b] in (char['text'], '\\' + char['text'])
    start = md.index('100,00', md.index('100,00') + 1)
    selected = characters_for_span({'characters': writer.characters}, start, start + 6)
    assert selected == [char['id'] for char in writer.characters[6:12]]


def test_ligature_is_split_into_codepoints_without_fabricating_geometry():
    writer = MarkdownWriter()
    writer.word(Word('fi', [10, 20, 30, 40], (0, 0), 'pdf_text',
                     characters=[{'text': 'fi', 'bbox': [10, 20, 30, 40]}]), 1, [100, 100])
    assert [c['text'] for c in writer.characters] == ['f', 'i']
    assert [c['markdown_span'] for c in writer.characters] == [[0, 1], [1, 2]]
    assert writer.characters[0]['bbox_normalized'] == writer.characters[1]['bbox_normalized']


def test_partial_alignment_keeps_real_boxes_and_marks_missing_geometry():
    writer = MarkdownWriter()
    writer.word(Word('abc', [10, 20, 40, 40], (0, 0), 'pdf_text', characters=[
        {'text': 'a', 'bbox': [10, 20, 20, 40]},
        {'text': 'X', 'bbox': [20, 20, 30, 40]},
        {'text': 'c', 'bbox': [30, 20, 40, 40]}]), 1, [100, 100])
    assert [c['markdown_span'] for c in writer.characters] == [[0, 1], None, [2, 3]]
    assert writer.unmapped_character_count == 1


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_native_rotated_cropped_pdf_and_highlight(tmp_path, rotation):
    source = tmp_path / 'source.pdf'
    with fitz.open() as doc:
        page = doc.new_page(width=500, height=400)
        page.insert_text((90, 100), 'Amount 12345.67', fontsize=18)
        page.set_cropbox(fitz.Rect(40, 30, 460, 350))
        page.set_rotation(rotation)
        doc.save(source)
    output = tmp_path / 'result.md'
    data = perform(source, output, '--ocr', 'never')
    check_mapping(data, output.read_text(encoding='utf-8'))
    chars = selected_text(data, output.read_text(encoding='utf-8'), '12345.67')
    assert ''.join(c['text'] for c in chars) == '12345.67'
    char = chars[3]
    highlighted = tmp_path / 'highlighted.pdf'
    highlight(output.with_suffix('.boxes.json'), [char['id']], highlighted)
    with fitz.open(highlighted) as doc:
        page = doc[0]
        annotation = next(page.annots())
        display_rect = annotation.rect * page.rotation_matrix
        assert abs(display_rect.x0 - char['bbox_normalized'][0] * page.rect.width) < 3
        assert abs(display_rect.y0 - char['bbox_normalized'][1] * page.rect.height) < 3
        assert len(list(page.annots())) == 1


def test_highlight_span_rejects_modified_markdown(tmp_path):
    source = tmp_path / 'source.txt'
    source.write_text('Amount 12345.67', encoding='utf-8')
    output = tmp_path / 'result.md'
    data = perform(source, output, '--ocr', 'never')
    check_mapping(data, output.read_text(encoding='utf-8'))
    span = selected_text(data, output.read_text(encoding='utf-8'), '12345.67')[0]['markdown_span']
    highlight(output.with_suffix('.boxes.json'), [], tmp_path / 'marked.pdf', span)
    output.write_text('edited', encoding='utf-8')
    with pytest.raises(ConversionError, match='edited'):
        highlight(output.with_suffix('.boxes.json'), [], tmp_path / 'invalid.pdf', span)


def test_amount_across_words_and_single_digit_highlight(tmp_path):
    source = tmp_path / 'amount.txt'
    source.write_text('650 000,00 and 650 000,00', encoding='utf-8')
    output = tmp_path / 'amount.md'
    data = perform(source, output, '--ocr', 'never')
    markdown = output.read_text(encoding='utf-8')
    check_mapping(data, markdown)
    start = markdown.index('650 000,00')
    chars = selected_text(data, markdown, '650 000,00')
    assert ''.join(c['text'] for c in chars) == '650000,00'
    assert len(chars) == 9
    digit = characters_for_span(data, start + 4, start + 5)
    assert digit == [chars[3]['id']]
    result = tmp_path / 'marked.pdf'
    highlight(output.with_suffix('.boxes.json'), [], result, [start + 4, start + 5])
    with fitz.open(result) as doc:
        assert len(list(doc[0].annots())) == 1


def test_source_is_not_overwritten(tmp_path):
    source = tmp_path / 'file.txt'
    source.write_text('original')
    with pytest.raises(ConversionError):
        perform(source, source)
    assert source.read_text() == 'original'


def test_partially_clipped_native_word_is_explicit(tmp_path):
    source = tmp_path / 'clipped.pdf'
    with fitz.open() as doc:
        page = doc.new_page(width=200, height=150)
        page.insert_text((154, 70), 'ABCDE', fontsize=14)
        doc.save(source)
    output = tmp_path / 'clipped.md'
    data = perform(source, output, '--ocr', 'never')
    check_mapping(data, output.read_text(encoding='utf-8'))
    assert any('clipped' in warning for warning in data['warnings'])
    assert all(c['bbox_normalized'][2] <= 1 for c in data['characters'])


def require_tesseract():
    try:
        find_tesseract()
    except ConversionError:
        pytest.skip('Tesseract is not installed')


def test_open_table_header_keeps_its_vertical_span():
    import numpy as np
    table = {'bbox': [10, 10, 130, 100], 'cells': [
        [10, 10, 50, 40], [50, 10, 90, 40],
        [10, 40, 50, 70], [50, 40, 90, 70],
        [10, 70, 50, 100], [50, 70, 90, 100], [90, 70, 130, 100]]}
    horizontal = np.zeros((111, 141), dtype=np.uint8)
    vertical = np.zeros_like(horizontal)
    for y in [10, 70, 100]:
        horizontal[y, 10:131] = 255
    horizontal[40, 10:91] = 255
    for x in [10, 50, 90]:
        vertical[10:101, x] = 255
    recover_open_cells(table, horizontal, vertical, 1, 1)
    assert [90, 10, 130, 70] in table['cells']
    words = [Word('Итого', [100, 35, 120, 45], (0, 0), 'tesseract')]
    grid, _ = table_grid(table, words)
    assert grid[0][2] == [0] and grid[1][2] == []


def test_cell_cleanup_preserves_decimal_point_and_minus():
    image = Image.new('RGB', (100, 40), 'white')
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 10, 47, 23), fill='black')  # full-height glyph
    draw.rectangle((22, 16, 29, 17), fill='black')  # real minus sign
    draw.point((50, 23), fill='black')  # nearby decimal point
    draw.point((4, 4), fill='black')  # distant scan speck
    clean, scale = prepare_cell_image(image)
    assert scale > 1
    assert clean.crop((3 * scale, 3 * scale, 6 * scale, 6 * scale)).getextrema()[0][0] == 255
    assert clean.crop((22 * scale, 16 * scale, 30 * scale, 18 * scale)).getextrema()[0][0] < 100
    assert clean.crop((49 * scale, 22 * scale, 52 * scale, 25 * scale)).getextrema()[0][0] < 100


def test_real_scanned_report_decimal_headers_and_symbol_geometry(tmp_path):
    require_tesseract()
    source = Path(__file__).resolve().parents[2] / 'данные/01_Карточка_учета_фактических_затрат_этап_3.pdf'
    if not source.is_file():
        pytest.skip('Real report fixture is not available')
    output = tmp_path / 'report.md'
    data = perform(source, output)
    markdown = output.read_text(encoding='utf-8')
    check_mapping(data, markdown)
    assert markdown.count('Главный бухгалтер') == 1
    assert markdown.count('Руководитель проекта') == 1
    footer = selected_text(data, markdown, 'Руководитель проекта')
    assert ''.join(char['text'] for char in footer) == 'Руководительпроекта'
    assert all(char['bbox_normalized'][1] > .6 for char in footer)
    header = next(line for line in markdown.splitlines() if line.startswith('| '))
    cells = [cell.strip() for cell in header.split('|')[1:-1]]
    assert len(cells) == 17 and cells[0] == 'Этап' and cells[-1] == 'Итого'
    total = next(line for line in markdown.splitlines() if line.startswith('| ВСЕГО 3 этап:'))
    cells = [cell.strip().replace(' ', '') for cell in total.split('|')[1:-1]]
    assert len(cells) == 17
    assert cells[2] == '34,68' and cells[3] == '0,00' and cells[13] == '93237,27'
    for value in ['5558474,83', '1671359,96', '1145798,10', '3204744,51', '11580377,40']:
        assert value in markdown.replace(' ', '')
    chars = selected_text(data, markdown, '93 237,27')
    assert ''.join(c['text'] for c in chars) == '93237,27'
    page = data['pages'][0]
    for char in chars:
        x0, y0, x1, y1 = char['bbox_normalized']
        assert 1818 <= x0 * page['image_width'] < x1 * page['image_width'] <= 1991
        assert 861 <= y0 * page['image_height'] < y1 * page['image_height'] <= 887


def test_image_ocr_numeric_word_and_digit_boxes(tmp_path):
    require_tesseract()
    font = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    if not font.is_file():
        pytest.skip('Test font missing')
    image = Image.new('RGB', (950, 160), 'white')
    ImageDraw.Draw(image).text((25, 35), 'Amount 12345.67', font=ImageFont.truetype(str(font), 52), fill='black')
    source = tmp_path / 'scan.png'
    image.save(source)
    output = tmp_path / 'result.md'
    data = perform(source, output, '--lang', 'eng', '--psm', '6')
    check_mapping(data, output.read_text(encoding='utf-8'))
    chars = selected_text(data, output.read_text(encoding='utf-8'), '12345.67')
    assert ''.join(c['text'] for c in chars) == '12345.67'
    assert len(chars) == 8
    assert data['pages'][0]['width'] == 950
    assert chars[0]['bbox_normalized'][0] * 950 > 150


def test_mixed_pdf_keeps_native_and_image_text(tmp_path):
    require_tesseract()
    font = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    if not font.is_file():
        pytest.skip('Test font missing')
    import io
    image = Image.new('RGB', (900, 140), 'white')
    ImageDraw.Draw(image).text((15, 30), 'Scanned 98765.43', font=ImageFont.truetype(str(font), 48), fill='black')
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    source = tmp_path / 'mixed.pdf'
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=350)
        page.insert_text((40, 40), 'Native heading', fontsize=16)
        page.insert_image(fitz.Rect(40, 100, 550, 180), stream=stream.getvalue())
        doc.save(source)
    output = tmp_path / 'mixed.md'
    data = perform(source, output, '--lang', 'eng')
    check_mapping(data, output.read_text(encoding='utf-8'))
    markdown = output.read_text(encoding='utf-8')
    assert ''.join(c['text'] for c in selected_text(data, markdown, 'Native')) == 'Native'
    assert ''.join(c['text'] for c in selected_text(data, markdown, 'Scanned')) == 'Scanned'

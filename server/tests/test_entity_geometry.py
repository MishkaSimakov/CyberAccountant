"""Character geometry follows exact occurrences and refuses stale or ambiguous text."""
import hashlib
import json
from pathlib import Path

import pymupdf as fitz
import pytest

from app import database, storage
from app.entity_geometry import EntityNotFound, locate_entity, source_span
from app.models import File, Report
from app.ocr.converter import convert, parser
from app.services.documents import entity_geometry
from test_documents import api, request, upload


def tagged_occurrence(text, value, occurrence=0, entity_id=7):
    start = -1
    for _ in range(occurrence + 1):
        start = text.index(value, start + 1)
    return text[:start] + f'<ent id="{entity_id}" t="amount">{value}</ent>' + text[start + len(value):]


@pytest.fixture
def repeated_pdf(tmp_path):
    source = tmp_path / 'source.pdf'
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 80), 'First amount: 650000.00')
        page.insert_text((50, 180), 'Second amount: 650000.00')
        doc.save(source)
    output = tmp_path / 'ocr' / 'document.md'
    data = convert(source, output, parser().parse_args([str(source), str(output), '--ocr', 'never']))
    markdown = output.read_text(encoding='utf-8')
    pdf = output.parent / data['preview_pdf']
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    return source, output, markdown, pdf, digest


def test_repeated_amount_maps_only_to_the_selected_occurrence(repeated_pdf):
    _, _, md, pdf, digest = repeated_pdf
    first = locate_entity(tagged_occurrence(md, '650000.00'), 7, pdf, digest)
    second = locate_entity(tagged_occurrence(md, '650000.00', 1), 7, pdf, digest)
    assert first['status'] == second['status'] == 'mapped'
    assert len(first['boxes']) == len(second['boxes']) == 1
    assert max(b['bbox_normalized'][3] for b in first['boxes']) < min(b['bbox_normalized'][1] for b in second['boxes'])
    assert all(b['page'] == 1 for b in second['boxes'])


def test_missing_stale_foreign_and_changed_entity_never_get_arbitrary_boxes(repeated_pdf):
    _, output, md, pdf, digest = repeated_pdf
    tagged = tagged_occurrence(md, '650000.00', 1)
    with pytest.raises(EntityNotFound):
        locate_entity(tagged, 999, pdf, digest)
    assert locate_entity(tagged, 7, pdf, 'f' * 64)['status'] == 'unmapped'
    changed = tagged.replace('t="amount">650000.00</ent>', 't="amount">12345.00</ent>')
    assert locate_entity(changed, 7, pdf, digest)['boxes'] == []
    output.write_text(md + 'Edited OCR', encoding='utf-8')
    assert locate_entity(tagged, 7, pdf, digest)['status'] == 'unmapped'
    output.with_suffix('.boxes.json').unlink()
    assert locate_entity(tagged, 7, pdf, digest)['status'] == 'unmapped'


def test_edit_outside_the_entity_keeps_unique_context_and_correct_occurrence(repeated_pdf):
    _, _, md, pdf, digest = repeated_pdf
    tagged = tagged_occurrence(md, '650000.00', 1)
    expected = locate_entity(tagged, 7, pdf, digest)
    actual = locate_entity('An operator added a note.\n\n' + tagged, 7, pdf, digest)
    assert actual['status'] == 'mapped'
    assert actual['boxes'] == expected['boxes']
    assert source_span('12 12', '12 12 12', 0, 2) is None


def test_missing_character_is_reported_as_partial(repeated_pdf):
    _, output, md, pdf, digest = repeated_pdf
    sidecar = output.with_suffix('.boxes.json')
    data = json.loads(sidecar.read_text(encoding='utf-8'))
    last = md.rindex('650000.00')
    data['characters'] = [c for c in data['characters'] if c['markdown_span'] != [last, last + 1]]
    sidecar.write_text(json.dumps(data), encoding='utf-8')
    result = locate_entity(tagged_occurrence(md, '650000.00', 1), 7, pdf, digest)
    assert result['status'] == 'partial'
    assert len(result['boxes']) == 1
    assert result['message']


def test_legacy_number_typography_requires_context_and_equal_numeric_value():
    original = 'Payroll statement\n\n| Salary amount | Basis |\n| --- | --- |\n| 1,010,000.00 | Calculated working time |'
    reviewed = 'Payroll statement\n\n| Salary amount | Basis |\n| --- | --- |\n| 1 010 000,00 | Calculated working time |'
    start = reviewed.index('1 010')
    result = source_span(reviewed, original, start, start + len('1 010 000,00'))
    assert result is not None
    assert original[result[0]:result[1]] == '1,010,000.00'
    # Removing separators alone would confuse 10.00 with 1,000: prohibit that.
    original = original.replace('1,010,000.00', '1,000')
    reviewed = reviewed.replace('1 010 000,00', '10.00')
    start = reviewed.index('10.00')
    assert source_span(reviewed, original, start, start + 5) is None


def test_unique_account_survives_corrupted_label_and_reordered_blocks(tmp_path):
    source, output = tmp_path / 'account.pdf', tmp_path / 'document.md'
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 80), 'cuer i cySeuer: 109.60')
        page.insert_text((50, 180), 'End of stage: May 2026')
        doc.save(source)
    data = convert(source, output, parser().parse_args([str(source), str(output), '--ocr', 'never']))
    reviewed = 'End of stage: May 2026\n\nSynthetic account and subaccount: 109.60\n'
    result = locate_entity(tagged_occurrence(reviewed, '109.60'), 7,
                           output.parent / data['preview_pdf'], hashlib.sha256(source.read_bytes()).hexdigest())
    assert result['status'] == 'mapped'
    assert len(result['boxes']) == 1
    assert all(b['page'] == 1 and b['bbox_normalized'][3] < .15 for b in result['boxes'])


@pytest.mark.parametrize('reviewed,original,value', [
    ('Account: 109.60', 'Broken: 109.60 and 109.60', '109.60'),
    ('Account: 109.60 and 109.60', 'Broken: 109.60', '109.60'),
    ('Account: 109.60', 'Broken: 2109.60', '109.60'),
    ('Account: 109.60', 'Broken: 109.600', '109.60'),
    ('Account: 109.60', 'Broken: 109.60.10', '109.60'),
    ('Account: 109.60', 'Broken: 109.61', '109.60'),
    ('Account: 0,00', 'Broken: 0,00', '0,00'),
])
def test_unique_value_fallback_refuses_duplicates_fragments_edits_and_short_values(reviewed, original, value):
    start = reviewed.index(value)
    assert source_span(reviewed, original, start, start + len(value)) is None


@pytest.mark.parametrize('second_page', [1, 2])
def test_unicode_offsets_and_merged_entity_boxes_per_page(tmp_path, second_page):
    assets = tmp_path / 'document.assets'
    assets.mkdir()
    pdf = assets / 'preview.pdf'
    pdf.write_bytes(b'pdf')
    md = '😀 Значение: 12\n34'
    (tmp_path / 'document.md').write_text(md, encoding='utf-8', newline='')
    start = md.index('12')
    characters = [dict(markdown_span=[start + i, start + i + 1], page=1 if i < 2 else second_page,
                       bbox_normalized=[.1 + .05 * i, .2 if i < 2 else .3,
                                        .14 + .05 * i, .25 if i < 2 else .35]) for i in (0, 1, 3, 4)]
    data = dict(schema_version='2.0', source={'sha256': 'hash'}, preview_pdf='document.assets/preview.pdf',
                markdown={'path': 'document.md', 'sha256': hashlib.sha256(md.encode()).hexdigest()},
                pages=[{}, {}], characters=characters)
    (tmp_path / 'document.boxes.json').write_text(json.dumps(data), encoding='utf-8')
    result = locate_entity(tagged_occurrence(md, '12\n34'), 7, pdf, 'hash')
    assert result['status'] == 'mapped'
    if second_page == 1:
        assert len(result['boxes']) == 1
        assert result['boxes'][0]['page'] == 1
        assert result['boxes'][0]['bbox_normalized'] == pytest.approx([.1, .2, .34, .35])
    else:
        assert [b['page'] for b in result['boxes']] == [1, 2]
        assert result['boxes'][0]['bbox_normalized'] == pytest.approx([.1, .2, .19, .25])
        assert result['boxes'][1]['bbox_normalized'] == pytest.approx([.25, .3, .34, .35])


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_cropbox_and_rotation_keep_visible_normalized_geometry(tmp_path, rotation):
    source, output = tmp_path / 'rotated.pdf', tmp_path / 'document.md'
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=500)
        page.insert_text((80, 120), 'Value 123.45')
        page.set_cropbox(fitz.Rect(30, 50, 380, 480))
        page.set_rotation(rotation)
        doc.save(source)
    data = convert(source, output, parser().parse_args([str(source), str(output), '--ocr', 'never']))
    result = locate_entity(tagged_occurrence(output.read_text(encoding='utf-8'), '123.45'), 7,
                           output.parent / data['preview_pdf'], hashlib.sha256(source.read_bytes()).hexdigest())
    assert result['status'] == 'mapped'
    assert len(result['boxes']) == 1
    with fitz.open(source) as doc:
        expected = doc[0].search_for('123.45')[0] * doc[0].rotation_matrix
        rect = doc[0].rect
        x0 = min(b['bbox_normalized'][0] for b in result['boxes']) * rect.width
        y0 = min(b['bbox_normalized'][1] for b in result['boxes']) * rect.height
        x1 = result['boxes'][0]['bbox_normalized'][2] * rect.width
        y1 = result['boxes'][0]['bbox_normalized'][3] * rect.height
        assert abs(x0 - expected.x0) < 2
        assert abs(y0 - expected.y0) < 2
        assert abs(x1 - expected.x1) < 2
        assert abs(y1 - expected.y1) < 2


def test_api_reuses_original_geometry_after_an_edited_revision(api, repeated_pdf):
    report_id, revisions = api
    source, output, md, _, _ = repeated_pdf
    status, uploaded = upload(report_id, role='final_report', content=source.read_bytes())
    assert status == 201
    file_id = uploaded['id']
    with database.session_scope() as session:
        file = session.get(File, revisions[0])
        report = session.get(Report, report_id)
        report.stage, report.processing_status = 'verification', 'completed'
        saved = storage.save_ocr(report_id, file_id, 1, output)
        file.markdown_path = saved
        file.pdf_path = str(Path(saved).parent / 'document.assets/preview.pdf')
        file.status = 'completed'
        tagged = tagged_occurrence(md, '650000.00', 1)
        path = storage.artifact_path(f'{report_id}/verification/replaced/{file_id}.md')
        path.parent.mkdir(parents=True)
        path.write_text(tagged, encoding='utf-8')
        session.add(File(report_id=report_id, file_id=file_id, version=2, name=file.name, path=file.path,
                         hash=file.hash, role=file.role, size=file.size, content_type=file.content_type,
                         markdown_path=f'{report_id}/edited/document.md', pdf_path=file.pdf_path, status='completed'))
    endpoint = f'/reports/{report_id}/documents/{file_id}/entities/7/geometry'
    status, result = request('GET', endpoint)
    assert status == 200
    assert result['status'] == 'mapped'
    assert result['document_revision'] == 2
    assert len(result['boxes']) == 1
    assert request('GET', endpoint.replace('/7/', '/999/'))[0] == 404
    assert request('GET', endpoint.replace(f'/documents/{file_id}/', '/documents/99999/'))[0] == 404

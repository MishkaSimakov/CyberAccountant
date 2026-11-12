import json
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

from insurance_constraints import generate, load_documents


def entity(key, value, kind='number', unit='rubles'):
    return f'<entity id="{key}" type="{kind}" unit="{unit}" value="{value}">{value}</entity>'


def calculation(period, amount, *, contract='24/03/15-01', number=None, offset=100):
    return f'''РАСЧЁТ СТРАХОВЫХ ВЗНОСОВ
Документ: {entity(offset, number or 'ВЗН-' + period, 'id', 'string')}
Проект: {entity(offset+1, contract, 'id', 'string')} | Период: {entity(offset+2, period, 'date', 'date')}
| База начислений, руб. | Начислено взносов, руб. | Код статьи | Комментарий |
| --- | --- | --- | --- |
| {entity(offset+3, '99999.99')} | {entity(offset+4, amount)} | {entity(offset+5, '0300', 'id', 'string')} | Сводное начисление |
'''


@pytest.fixture
def documents():
    return {
        'request.md': f'''Проверяемая строка отчета: «3 этап, 2026 г.».
Договор: {entity(1, '24/03/15-01', 'id', 'string')}.
Период подтверждающих документов: {entity(2, '2026-01-01', 'date', 'date')}–{entity(3, '2026-02-28', 'date', 'date')}.
''',
        'card.md': f'''КАРТОЧКА УЧЕТА ФАКТИЧЕСКИХ ЗАТРАТ
контракт № {entity(500, '24/03/15-01', 'id', 'string')}
| Этап | Страховые взносы (0300), всего (гр.3+гр.4) | Зарплата | Резерв |
| --- | --- | --- | --- |
| 1 | 2 | 3 | 4 |
| 3 этап, 2025 г. | {entity(504, '999.00')} | {entity(505, '999.00')} | {entity(506, '0.00')} |
| 3 этап, 2026 г. | {entity(501, '15.00')} | {entity(502, '12.00')} | {entity(503, '3.00')} |
| ВСЕГО 3 этап | {entity(507, '1014.00')} | {entity(508, '1011.00')} | {entity(509, '3.00')} |
''',
        'jan.md': calculation('2026-01', '7.00'),
        'feb.md': calculation('2026-02', '8.00', offset=200),
        'unrelated.md': entity(999, '99999999.99'),
    }


def equations(relations):
    return [r['equation'] for r in relations]


def test_amount_column_scope_target_and_model_format(documents):
    relations, audit = generate(documents)
    assert audit['status'] == 'ok'
    assert audit['expected'] == '15.00'
    assert audit['difference'] == '0.00'
    assert '#501 == #104 + #204' in equations(relations)
    assert '01.01.2026 <= #102 && #102 <= 28.02.2026' in equations(relations)
    assert all(set(r) == {'equation', 'tag'} for r in relations)
    assert all(r['tag'] in {'agg', 'contract', 'period'} for r in relations)


def test_mismatch_does_not_change_generated_relationship(documents):
    before, _ = generate(documents)
    documents['card.md'] = documents['card.md'].replace('value="15.00"', 'value="16.00"')
    after, audit = generate(documents)
    assert before == after
    assert audit['status'] == 'mismatch'
    assert audit['difference'] == '1.00'


def test_missing_month_never_generates_partial_aggregation(documents):
    del documents['feb.md']
    relations, audit = generate(documents)
    assert audit['status'] == 'unverified'
    assert audit['missing_months'] == ['2026-02']
    assert '#501 == #104' not in equations(relations)
    assert audit['expected'] is None


def test_exact_duplicate_not_counted_twice(documents):
    documents['jan-copy.md'] = documents['jan.md']
    relations, audit = generate(documents)
    assert audit['status'] == 'ok'
    assert len(audit['included']) == 2
    assert '#501 == #104 + #204' in equations(relations)
    assert any('Дубликат' in e['reason'] for e in audit['excluded'])


@pytest.mark.parametrize('same_number', [True, False])
def test_conflicting_versions_and_parallel_summaries_are_unverified(documents, same_number):
    documents['another-jan.md'] = calculation('2026-01', '9.00', offset=300,
                                             number=None if same_number else 'OTHER')
    relations, audit = generate(documents)
    assert audit['status'] == 'unverified'
    assert len(audit['errors']) == 1
    assert relations == []


def test_other_contract_and_period_are_excluded(documents):
    documents['foreign.md'] = calculation('2026-01', '1111', contract='OTHER', offset=300)
    documents['march.md'] = calculation('2026-03', '2222', offset=400)
    _, audit = generate(documents)
    assert audit['status'] == 'ok'
    assert {e['reason'] for e in audit['excluded']} >= {'Другой договор', 'Вне периода'}


def test_foreign_contract_does_not_require_parsing_its_period(documents):
    documents['foreign.md'] = calculation('unknown', '1111', contract='OTHER', offset=300)
    _, audit = generate(documents)
    assert audit['status'] == 'ok'
    assert not audit['errors']


def test_external_check_does_not_require_internal_column_numbering(documents):
    documents['card.md'] = documents['card.md'].replace('| 1 | 2 | 3 | 4 |\n', '')
    relations, audit = generate(documents)
    assert audit['status'] == 'ok'
    assert '#501 == #104 + #204' in equations(relations)
    assert 'internal' not in audit


def test_partial_month_cannot_be_prorated_silently(documents):
    _, audit = generate(documents, start='2026-01-15')
    assert audit['status'] == 'unverified'
    assert audit['missing_months'] == ['2026-01']
    assert 'частично' in audit['errors'][0]['reason']


@pytest.mark.parametrize('change', ['currency', 'missing_tag', 'bad_number'])
def test_bad_selected_amount_is_not_replaced_by_base_or_zero(documents, change):
    old = entity(104, '7.00')
    new = {'currency': entity(104, '7.00', unit='USD'), 'missing_tag': '7.00',
           'bad_number': entity(104, 'NaN')}[change]
    documents['jan.md'] = documents['jan.md'].replace(old, new)
    relations, audit = generate(documents)
    assert audit['status'] == 'unverified'
    assert audit['errors']
    assert '#501 == #104 + #204' not in equations(relations)


def test_duplicate_active_ids_are_rejected(documents):
    documents['feb.md'] = documents['feb.md'].replace('id="204"', 'id="104"')
    with pytest.raises(ValueError, match='глобально уникальными'):
        generate(documents)


def test_explicit_scope_without_readme(documents):
    del documents['request.md']
    _, audit = generate(documents, contract='24/03/15-01', start='2026-01-01',
                        end='2026-02-28', row='3 этап, 2026 г.')
    assert audit['status'] == 'ok'


def test_column_order_does_not_change_selected_amounts(documents):
    for name in ('card.md', 'jan.md', 'feb.md'):
        lines = []
        for line in documents[name].splitlines():
            if line.startswith('|'):
                cells = line[1:-1].split('|')
                cells[1], cells[2] = cells[2], cells[1]
                line = '|' + '|'.join(cells) + '|'
            lines.append(line)
        documents[name] = '\n'.join(lines)
    relations, audit = generate(documents)
    assert audit['status'] == 'ok'
    assert '#501 == #104 + #204' in equations(relations)


def test_decimal_cents_are_exact(documents):
    documents['jan.md'] = documents['jan.md'].replace('7.00', '0.10')
    documents['feb.md'] = documents['feb.md'].replace('8.00', '0.20')
    for old, new in [('15.00', '0.30'), ('12.00', '0.10'), ('3.00', '0.20')]:
        documents['card.md'] = documents['card.md'].replace(f'value="{old}"', f'value="{new}"')
    _, audit = generate(documents)
    assert audit['status'] == 'ok'
    assert audit['expected'] == '0.30'


def test_fenced_example_table_is_not_treated_as_document_data(documents):
    example = documents['jan.md'].split('| База', 1)[1]
    documents['jan.md'] += '\n```markdown\n| База' + example + '\n```\n'
    _, audit = generate(documents)
    assert audit['status'] == 'ok'


def test_markdown_tables_without_outer_pipes(documents):
    for name in ('jan.md', 'feb.md', 'card.md'):
        documents[name] = '\n'.join(line[1:-1] if line.startswith('|') else line
                                   for line in documents[name].splitlines())
    relations, audit = generate(documents)
    assert audit['status'] == 'ok'
    assert '#501 == #104 + #204' in equations(relations)


def test_zip_and_folder_input_and_cli(documents, tmp_path):
    folder = tmp_path / 'tagged'
    folder.mkdir()
    archive = tmp_path / 'tagged.zip'
    with ZipFile(archive, 'w') as z:
        for name, text in documents.items():
            (folder / name).write_text(text, encoding='utf-8')
            z.writestr(name, text)
        z.writestr('__MACOSX/._card.md', b'not Markdown')
    assert load_documents(folder) == load_documents(archive) == documents
    script = Path(__file__).resolve().parents[1] / 'insurance_constraints.py'
    output = tmp_path / 'relations.json'
    result = subprocess.run([sys.executable, str(script), str(archive), str(output)], capture_output=True)
    assert result.returncode == 0, result.stderr
    assert '#501 == #104 + #204' in equations(json.loads(output.read_text(encoding='utf-8')))
    assert json.loads(output.with_suffix('.audit.json').read_text(encoding='utf-8'))['status'] == 'ok'
    # The same CLI accepts a directory and exposes incomplete/mismatching data to callers.
    (folder / 'feb.md').unlink()
    result = subprocess.run([sys.executable, str(script), str(folder), str(output)], capture_output=True)
    assert result.returncode == 2
    assert json.loads(output.with_suffix('.audit.json').read_text(encoding='utf-8'))['status'] == 'unverified'
    (folder / 'feb.md').write_text(documents['feb.md'], encoding='utf-8')
    (folder / 'card.md').write_text(documents['card.md'].replace('value="15.00"', 'value="16.00"'), encoding='utf-8')
    result = subprocess.run([sys.executable, str(script), str(folder), str(output)], capture_output=True)
    assert result.returncode == 1

"""Insurance-only verification through the shared corpus, registry and solver."""

import calendar
import re
from datetime import date
from decimal import Decimal

import pytest

from app import storage, verification
from app.generators.insurance import generate_insurance
from app.generators.llm import generate_llm
from app.models import File, Report
from checkier.formula import collect_ids, parse


def tag(kind, value, text=None, unit=None):
    unit = f' u="{unit}"' if unit else ''
    return f'<ent t="{kind}" value="{value}"{unit}>{text if text is not None else value}</ent>'


def calculation(month, amount, *, number=None, contract='24/03/15-01', unit='RUB'):
    year, month_number = map(int, month.split('-'))
    last_day = calendar.monthrange(year, month_number)[1]
    period = (f'<ent t="date" start="{month}-01T00:00:00" '
              f'end="{month}-{last_day:02d}T23:59:59">{month}</ent>')
    return f'''РАСЧЁТ СТРАХОВЫХ ВЗНОСОВ
Документ: {tag('docid', number or 'ВЗН-' + month)}
Проект: {tag('contract', contract)} | Период: {period}

| База начислений, руб. | Начислено взносов, руб. | Код статьи |
| --- | --- | --- |
| {tag('amount', '99999.99', unit='RUB')} | {tag('amount', amount, unit=unit)} | 0300 |
'''


@pytest.fixture
def documents():
    return {
        'card.md': f'''КАРТОЧКА УЧЕТА ФАКТИЧЕСКИХ ЗАТРАТ
Контракт № {tag('contract', '24/03/15-01')}

| Этап | Страховые взносы (0300), всего | Другая статья |
| --- | --- | --- |
| 3 этап, 2025 г. | {tag('amount', '999.00', unit='RUB')} | 0 |
| 3 этап, 2026 г. | {tag('amount', '15.00', unit='RUB')} | {tag('amount', '100.00', unit='RUB')} |
| ВСЕГО 3 этап | {tag('amount', '1014.00', unit='RUB')} | 0 |
''',
        'jan.md': calculation('2026-01', '7.00'),
        'feb.md': calculation('2026-02', '8.00'),
        'other.md': f'Сумма другого документа: {tag("amount", "1234567.89", unit="RUB")}',
    }


@pytest.fixture
def context_factory(tmp_path, monkeypatch):
    monkeypatch.setenv('STORAGE_ROOT', str(tmp_path))
    monkeypatch.setattr(verification, 'GENERATORS', {'insurance': generate_insurance})

    def forbidden(*args, **kwargs):
        pytest.fail('Insurance-only verification must not call an LLM')

    monkeypatch.setattr('app.generators.llm.generate_relations', forbidden)
    monkeypatch.setattr('checkier.stage5_relations.call_llm_json', forbidden)

    def prepare(documents, *, start=date(2026, 1, 1), end=date(2026, 2, 28), style='numeric', row='3 этап, 2026 г.'):
        report = Report(id=1, name='Insurance test', contract_number='24/03/15-01',
                        period_start=start, period_end=end, target_row=row)
        files = [File(file_id=i, version=1, name=name,
                      role='final_report' if name == 'card.md' else 'supporting',
                      entities_path=storage.save_entities(1, i, 1, text))
                 for i, (name, text) in enumerate(documents.items(), 1)]
        context = verification.prepare_verification(report, files)
        if style == 'mnemonic':
            context.config.id_style = style
            context.documents = {name: re.sub(r'id="(\d+)"',
                                              lambda m: f'id="{context.registry.entities[int(m[1])].mnemonic}"', text)
                                 for name, text in context.documents.items()}
            context.scope['target_row'] = [context.registry.entities[n].mnemonic
                                           for n in context.scope['target_row']]
        return context

    return prepare


def run(context):
    relations = verification.generate_requirements(context)
    definitions = [dict(requirement, id=i) for i, requirement in enumerate(relations, 1)]
    return generate_insurance(context), verification.evaluate_requirements(context, definitions)


def test_registered_alongside_existing_llm_generator():
    assert verification.GENERATORS['insurance'] is generate_insurance
    assert verification.GENERATORS['llm'] is generate_llm


@pytest.mark.parametrize('style', ['numeric', 'mnemonic'])
def test_only_algorithm_uses_real_registry_and_single_shared_solver(documents, context_factory, monkeypatch, style):
    context = context_factory(documents, style=style)
    calls = []
    solve = verification.solve_system

    def counted(requirements, registry, **kwargs):
        calls.append(requirements)
        return solve(requirements, registry, **kwargs)

    monkeypatch.setattr(verification, 'solve_system', counted)
    own, results = run(context)
    assert len(calls) == len(own) == 1
    assert own[0]['tag'] == 'agg'
    assert all(r['source'] != 'llm' for r in calls[0])
    keys = collect_ids(parse(own[0]['equation']))
    selected = [e for e in context.registry.entities if (e.num if style == 'numeric' else e.mnemonic) in keys]
    assert sorted(Decimal(e.value) for e in selected) == [Decimal(7), Decimal(8), Decimal(15)]
    assert results['requirement_results'][0]['verdict'] == 'true'
    checked = next(r for r in results['card']['cells'] if r.get('expected') is not None)
    assert checked['status'] == 'ok' and Decimal(checked['expected']) == 15
    assert any(r['status'] == 'unverified' for r in results['card']['cells'])


def test_stated_mismatch_is_found_without_changing_generated_formula(documents, context_factory):
    original, _ = run(context_factory(documents))
    documents['card.md'] = documents['card.md'].replace('value="15.00"', 'value="16.00"')
    own, results = run(context_factory(documents))
    assert original == own
    assert results['requirement_results'][0]['verdict'] == 'false'
    assert results['relations'][0]['status'] == 'false'
    cell = next(r for r in results['card']['cells'] if r.get('expected') is not None)
    assert cell['status'] == 'mismatch'
    assert Decimal(cell['expected']) == 15 and Decimal(cell['diff']) == 1


def test_missing_month_is_unverifiable_not_partial_equality(documents, context_factory):
    del documents['feb.md']
    own, results = run(context_factory(documents))
    assert own[0]['verdict'] == 'unverifiable'
    assert '2026-02' in own[0]['evidence']
    assert 'equation' not in own[0]
    assert not any(r.get('expected') is not None for r in results['card']['cells'])


def test_duplicate_is_counted_once_and_foreign_documents_are_excluded(documents, context_factory):
    documents['jan-copy.md'] = documents['jan.md']
    documents['foreign.md'] = calculation('2026-01', '1111', contract='OTHER')
    documents['march.md'] = calculation('2026-03', '2222')
    own, results = run(context_factory(documents))
    assert len(collect_ids(parse(own[0]['equation']))) == 3
    cell = next(r for r in results['card']['cells'] if r.get('expected') is not None)
    assert Decimal(cell['expected']) == 15 and cell['status'] == 'ok'


@pytest.mark.parametrize('number', [None, 'ANOTHER'])
def test_conflicting_versions_or_monthly_summaries_are_unverifiable(documents, context_factory, number):
    documents['another.md'] = calculation('2026-01', '9.00', number=number)
    own, _ = run(context_factory(documents))
    assert own[0]['verdict'] == 'unverifiable' and 'конфликт' in own[0]['evidence']


@pytest.mark.parametrize('change', ['unknown_value', 'currency', 'missing_amount', 'wrong_article'])
def test_invalid_calculation_never_becomes_a_zero_or_base_amount(documents, context_factory, change):
    text = documents['jan.md']
    text = {
        'unknown_value': text.replace('value="7.00"', 'value=""'),
        'currency': text.replace(tag('amount', '7.00', unit='RUB'), tag('amount', '7.00', unit='USD')),
        'missing_amount': text.replace(tag('amount', '7.00', unit='RUB'), '7.00'),
        'wrong_article': text.replace('| 0300 |', '| 0200 |'),
    }[change]
    documents['jan.md'] = text
    own, _ = run(context_factory(documents))
    assert own[0]['verdict'] == 'unverifiable' and 'equation' not in own[0]


def test_partial_month_and_year_intersection(documents, context_factory):
    own, _ = run(context_factory(documents, start=date(2026, 1, 15)))
    assert own[0]['verdict'] == 'unverifiable'
    own, results = run(context_factory(documents, start=date(2025, 8, 1)))
    assert 'equation' in own[0]  # The requested 2026 row excludes the previous year.
    assert next(r for r in results['card']['cells'] if r.get('expected') is not None)['status'] == 'ok'


def test_other_reports_are_ignored_and_reordered_columns_work(documents, context_factory):
    original = documents['card.md']
    documents['card.md'] = original.replace('0300', '9999')
    own, _ = run(context_factory(documents))
    assert own == []
    documents['card.md'] = original
    for name in ('card.md', 'jan.md', 'feb.md'):
        lines = []
        for line in documents[name].splitlines():
            if line.startswith('|'):
                cells = line[1:-1].split('|')
                cells[1], cells[2] = cells[2], cells[1]
                line = '|' + '|'.join(cells) + '|'
            lines.append(line)
        documents[name] = '\n'.join(lines)
    own, results = run(context_factory(documents))
    assert 'equation' in own[0]
    assert next(r for r in results['card']['cells'] if r.get('expected') is not None)['status'] == 'ok'


def test_normalized_thousands_are_not_multiplied_a_second_time(documents, context_factory):
    documents['jan.md'] = documents['jan.md'].replace(tag('amount', '7.00', unit='RUB'),
                                                      tag('amount', '7.00', text='0.007', unit='RUB_K'))
    _, results = run(context_factory(documents))
    cell = next(r for r in results['card']['cells'] if r.get('expected') is not None)
    assert Decimal(cell['expected']) == 15 and cell['status'] == 'ok'


def test_untagged_insurance_document_cannot_be_silently_ignored(documents, context_factory):
    documents['unreadable.md'] = 'РАСЧЁТ СТРАХОВЫХ ВЗНОСОВ\nРеквизиты не распознаны'
    own, _ = run(context_factory(documents))
    assert own[0]['verdict'] == 'unverifiable' and 'equation' not in own[0]


def test_non_ruble_target_is_unverifiable(documents, context_factory):
    documents['card.md'] = documents['card.md'].replace(tag('amount', '15.00', unit='RUB'),
                                                        tag('amount', '15.00', unit='USD'))
    own, _ = run(context_factory(documents))
    assert own[0]['verdict'] == 'unverifiable' and 'рублях' in own[0]['evidence']


@pytest.mark.parametrize('start, end', [(None, None), (0, 1), (date(2026, 1, 1), None)])
def test_missing_or_invalid_report_period_cannot_generate_empty_sum(documents, context_factory, start, end):
    context = context_factory(documents)
    context.report.period_start, context.report.period_end = start, end
    own = generate_insurance(context)
    assert own[0]['verdict'] == 'unverifiable' and 'equation' not in own[0]


@pytest.mark.parametrize('style', ['numeric', 'mnemonic'])
def test_row_label_keeps_report_year(documents, context_factory, style):
    named, _ = run(context_factory(documents, style=style))
    context = context_factory(documents, row='3 этап, 2026 г.', style=style, start=date(2025, 8, 1))
    assert context.scope['target_label'] == '3 этап, 2026 г.'
    own, results = run(context)
    assert own == named
    assert next(r for r in results['card']['cells'] if r.get('expected') is not None)['status'] == 'ok'


def test_row_label_ignores_multirow_header_and_column_numbers(documents, context_factory):
    documents['card.md'] = documents['card.md'].replace('| 3 этап, 2025 г.',
                                                        '| Уточняющая шапка | Взносы, всего | Другие затраты |\n| 1 | 2 | 3 |\n| 3 этап, 2025 г.',
                                                        1)
    own, _ = run(context_factory(documents, row='3 этап, 2026 г.'))
    assert 'equation' in own[0]


def test_unknown_row_label_fails_instead_of_selecting_all_card_cells(documents, context_factory):
    with pytest.raises(ValueError, match='Не удалось найти проверяемую строку'):
        context_factory(documents, row='Несуществующая строка')


def test_row_label_works_with_multiple_monetary_tables(documents, context_factory):
    documents[
        'card.md'] += f'\n| Другая таблица | Сумма |\n| --- | --- |\n| Расходы | {tag("amount", "1", unit="RUB")} |\n'
    own, results = run(context_factory(documents, row='3 этап, 2026 г.'))
    assert 'equation' in own[0]
    assert next(r for r in results['card']['cells'] if r.get('expected') is not None)['status'] == 'ok'


def test_numeric_text_is_not_interpreted_as_row_position(documents, context_factory):
    documents['card.md'] = documents['card.md'].replace('999.00', '888.00').replace('3 этап, 2026 г.', '99')
    context = context_factory(documents, row='99')
    assert context.scope['target_label'] == '99'
    own, results = run(context)
    assert 'equation' in own[0]
    assert next(r for r in results['card']['cells'] if r.get('expected') is not None)['status'] == 'ok'

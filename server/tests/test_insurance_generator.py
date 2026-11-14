"""Insurance-only verification through the shared corpus, registry and solver."""

import re
from datetime import date
from decimal import Decimal

import pytest

from app import storage, verification
import logging
from dataclasses import asdict

from processing.common.corpus import ReviewedCorpus
from processing.common.documents import DocumentRevision, ReviewedDocument
from processing.common.scope import Scope
from processing.common.errors import ProcessingValidationError
from processing.requirements.generators.insurance import InsuranceGenerator
from processing.requirements.generators.llm import LLMRequirementGenerator
from processing.requirements.generators.scope import ScopeGenerator
from processing.requirements.interface import RequirementInput
from processing.requirements.options import RequirementOptions
from processing.requirements.processor import RequirementsProcessor
from app.models import File, Report
from processing.common.formula import collect_ids, parse
from entity_fixtures import canonical_documents


def tag(kind, value, text=None, unit=None):
    unit = f' u="{unit}"' if unit else ''
    return f'<ent t="{kind}" value="{value}"{unit}>{text if text is not None else value}</ent>'


def calculation(month, amount, *, number=None, contract='24/03/15-01', unit='RUB'):
    return f'''РАСЧЁТ СТРАХОВЫХ ВЗНОСОВ
Документ: {tag('docid', number or 'ВЗН-' + month)}
Проект: {tag('contract', contract)} | Период: {tag('date', month + '-01', month)}

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
def context_factory(tmp_path, monkeypatch, llm_client):
    monkeypatch.setenv('STORAGE_ROOT', str(tmp_path))

    def forbidden(*args, **kwargs):
        pytest.fail('Insurance-only verification must not call an LLM')

    monkeypatch.setattr(llm_client, "call_llm_json", forbidden)

    def prepare(documents, *, start=date(2026, 1, 1), end=date(2026, 2, 28), style='numeric', row='3 этап, 2026 г.'):
        report = Report(id=1, name='Insurance test', contract_number='24/03/15-01',
                        period_start=start, period_end=end, target_row=row)
        canonical = canonical_documents({str(i): ("card" if name == "card.md" else "data", text)
                                         for i, (name, text) in enumerate(documents.items(), 1)})
        files = [File(report_id=1, file_id=i, version=1, name=name,
                      role='final_report' if name == 'card.md' else 'supporting',
                      entities_path=storage.save_entities(1, i, 1, canonical[str(i)][1]))
                 for i, (name, text) in enumerate(documents.items(), 1)]
        context = verification.prepare_verification(report, files)
        if style == 'mnemonic':
            context.config.id_style = style
            context.scope['target_row'] = [
                next(entity.mnemonic for entity in context.registry.entities if entity.num == n)
                for n in context.scope['target_row']]
        return context

    return prepare


def generate_insurance(context):
    report = context.report
    by_key = {key: e.num for e in context.registry.entities for key in (e.num, e.mnemonic)}
    source = RequirementInput(ReviewedCorpus(report.id, tuple(
        ReviewedDocument(DocumentRevision(report.id, int(key), context.mapping[key]["version"]),
                         context.mapping[key]["original"],
                         "final_report" if context.mapping[key]["role"] == "card" else "supporting", text)
        for key, text in context.documents.items()
    )), Scope(report.contract_number, report.period_start, report.period_end, report.target_row,
              tuple(by_key[key] for key in context.scope["target_row"])))
    options = RequirementOptions(id_style=context.config.id_style)
    output = RequirementsProcessor(generators=[InsuranceGenerator(options)]).process(
        source, logger=logging.getLogger(__name__))
    return [asdict(definition) for definition in output.definitions]


def run(context, llm_client):
    relations = generate_insurance(context)
    definitions = [dict(requirement, id=i) for i, requirement in enumerate(relations, 1)]
    return relations, verification.evaluate_requirements(context, definitions)


def test_registered_alongside_existing_llm_generator(llm_client):
    processor = RequirementsProcessor(llm_client)
    assert [type(generator) for generator in processor.generators] == [
        ScopeGenerator, LLMRequirementGenerator, InsuranceGenerator]


@pytest.mark.parametrize('style', ['numeric', 'mnemonic'])
def test_only_algorithm_uses_real_registry_and_single_shared_solver(documents, context_factory, monkeypatch, style,
                                                                    llm_client):
    context = context_factory(documents, style=style)
    calls = []
    solve = verification.solve_system

    def counted(requirements, registry, **kwargs):
        calls.append(requirements)
        return solve(requirements, registry, **kwargs)

    monkeypatch.setattr(verification, 'solve_system', counted)
    own, results = run(context, llm_client)
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


def test_stated_mismatch_is_found_without_changing_generated_formula(documents, context_factory, llm_client):
    original, _ = run(context_factory(documents), llm_client)
    documents['card.md'] = documents['card.md'].replace('value="15.00"', 'value="16.00"')
    own, results = run(context_factory(documents), llm_client)
    assert original == own
    assert results['requirement_results'][0]['verdict'] == 'false'
    assert results['relations'][0]['status'] == 'false'
    cell = next(r for r in results['card']['cells'] if r.get('expected') is not None)
    assert cell['status'] == 'mismatch'
    assert Decimal(cell['expected']) == 15 and Decimal(cell['diff']) == 1


def test_missing_month_is_unverifiable_not_partial_equality(documents, context_factory, llm_client):
    del documents['feb.md']
    own, results = run(context_factory(documents), llm_client)
    assert own[0]['condition'] is None
    assert '2026-02' in own[0]['text']
    assert 'equation' not in own[0]
    assert not any(r.get('expected') is not None for r in results['card']['cells'])


def test_duplicate_is_counted_once_and_foreign_documents_are_excluded(documents, context_factory, llm_client):
    documents['jan-copy.md'] = documents['jan.md']
    documents['foreign.md'] = calculation('2026-01', '1111', contract='OTHER')
    documents['march.md'] = calculation('2026-03', '2222')
    own, results = run(context_factory(documents), llm_client)
    assert len(collect_ids(parse(own[0]['equation']))) == 3
    cell = next(r for r in results['card']['cells'] if r.get('expected') is not None)
    assert Decimal(cell['expected']) == 15 and cell['status'] == 'ok'


@pytest.mark.parametrize('number', [None, 'ANOTHER'])
def test_conflicting_versions_or_monthly_summaries_are_unverifiable(documents, context_factory, number, llm_client):
    documents['another.md'] = calculation('2026-01', '9.00', number=number)
    own, _ = run(context_factory(documents), llm_client)
    assert own[0]['condition'] is None and 'конфликт' in own[0]['text']


@pytest.mark.parametrize('change', ['unknown_value', 'currency', 'missing_amount', 'wrong_article'])
def test_invalid_calculation_never_becomes_a_zero_or_base_amount(documents, context_factory, change, llm_client):
    text = documents['jan.md']
    text = {
        'unknown_value': text.replace('value="7.00"', 'value=""'),
        'currency': text.replace(tag('amount', '7.00', unit='RUB'), tag('amount', '7.00', unit='USD')),
        'missing_amount': text.replace(tag('amount', '7.00', unit='RUB'), '7.00'),
        'wrong_article': text.replace('| 0300 |', '| 0200 |'),
    }[change]
    documents['jan.md'] = text
    own, _ = run(context_factory(documents), llm_client)
    assert own[0]['condition'] is None and 'equation' not in own[0]


def test_partial_month_and_year_intersection(documents, context_factory, llm_client):
    own, _ = run(context_factory(documents, start=date(2026, 1, 15)), llm_client)
    assert own[0]['condition'] is None
    own, results = run(context_factory(documents, start=date(2025, 8, 1)), llm_client)
    assert 'equation' in own[0]  # The requested 2026 row excludes the previous year.
    assert next(r for r in results['card']['cells'] if r.get('expected') is not None)['status'] == 'ok'


def test_other_reports_are_ignored_and_reordered_columns_work(documents, context_factory, llm_client):
    original = documents['card.md']
    documents['card.md'] = original.replace('0300', '9999')
    own, _ = run(context_factory(documents), llm_client)
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
    own, results = run(context_factory(documents), llm_client)
    assert 'equation' in own[0]
    assert next(r for r in results['card']['cells'] if r.get('expected') is not None)['status'] == 'ok'


def test_normalized_thousands_are_not_multiplied_a_second_time(documents, context_factory, llm_client):
    documents['jan.md'] = documents['jan.md'].replace(tag('amount', '7.00', unit='RUB'),
                                                      tag('amount', '7.00', text='0.007', unit='RUB_K'))
    _, results = run(context_factory(documents), llm_client)
    cell = next(r for r in results['card']['cells'] if r.get('expected') is not None)
    assert Decimal(cell['expected']) == 15 and cell['status'] == 'ok'


def test_untagged_insurance_document_cannot_be_silently_ignored(documents, context_factory, llm_client):
    documents['unreadable.md'] = 'РАСЧЁТ СТРАХОВЫХ ВЗНОСОВ\nРеквизиты не распознаны'
    own, _ = run(context_factory(documents), llm_client)
    assert own[0]['condition'] is None and 'equation' not in own[0]


def test_non_ruble_target_is_unverifiable(documents, context_factory, llm_client):
    documents['card.md'] = documents['card.md'].replace(tag('amount', '15.00', unit='RUB'),
                                                        tag('amount', '15.00', unit='USD'))
    own, _ = run(context_factory(documents), llm_client)
    assert own[0]['condition'] is None and 'рублях' in own[0]['text']


@pytest.mark.parametrize('start, end', [(None, None)])
def test_missing_or_invalid_report_period_cannot_generate_empty_sum(documents, context_factory, start, end):
    context = context_factory(documents)
    context.report.period_start, context.report.period_end = start, end
    own = generate_insurance(context)
    assert own[0]['condition'] is None and 'equation' not in own[0]


@pytest.mark.parametrize('style', ['numeric', 'mnemonic'])
def test_row_label_keeps_report_year(documents, context_factory, style, llm_client):
    named, _ = run(context_factory(documents, style=style), llm_client)
    context = context_factory(documents, row='3 этап, 2026 г.', style=style, start=date(2025, 8, 1))
    assert context.scope['target_label'] == '3 этап, 2026 г.'
    own, results = run(context, llm_client)
    assert own == named
    assert next(r for r in results['card']['cells'] if r.get('expected') is not None)['status'] == 'ok'


def test_row_label_ignores_multirow_header_and_column_numbers(documents, context_factory, llm_client):
    documents['card.md'] = documents['card.md'].replace('| 3 этап, 2025 г.',
                                                        '| Уточняющая шапка | Взносы, всего | Другие затраты |\n| 1 | 2 | 3 |\n| 3 этап, 2025 г.',
                                                        1)
    own, _ = run(context_factory(documents, row='3 этап, 2026 г.'), llm_client)
    assert 'equation' in own[0]


def test_unknown_row_label_fails_instead_of_selecting_all_card_cells(documents, context_factory):
    with pytest.raises(ValueError, match='Не удалось найти проверяемую строку'):
        context_factory(documents, row='Несуществующая строка')


def test_row_label_works_with_multiple_monetary_tables(documents, context_factory, llm_client):
    documents[
        'card.md'] += f'\n| Другая таблица | Сумма |\n| --- | --- |\n| Расходы | {tag("amount", "1", unit="RUB")} |\n'
    own, results = run(context_factory(documents, row='3 этап, 2026 г.'), llm_client)
    assert 'equation' in own[0]
    assert next(r for r in results['card']['cells'] if r.get('expected') is not None)['status'] == 'ok'


def test_numeric_text_is_not_interpreted_as_row_position(documents, context_factory, llm_client):
    documents['card.md'] = documents['card.md'].replace('999.00', '888.00').replace('3 этап, 2026 г.', '99')
    context = context_factory(documents, row='99')
    assert context.scope['target_label'] == '99'
    own, results = run(context, llm_client)
    assert 'equation' in own[0]
    assert next(r for r in results['card']['cells'] if r.get('expected') is not None)['status'] == 'ok'


@pytest.mark.parametrize("start,end", [(date(2026, 1, 1), None), (date(2026, 2, 1), date(2026, 1, 1))])
def test_invalid_period_is_rejected_at_stage_boundary(start, end):
    with pytest.raises(ProcessingValidationError):
        Scope(period_start=start, period_end=end)

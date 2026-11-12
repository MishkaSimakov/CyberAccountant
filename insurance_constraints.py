"""Generate article 0300 aggregation from tagged Markdown (ZIP or directory)."""

import argparse
import calendar
import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from unicodedata import normalize
from zipfile import BadZipFile, ZipFile

from bs4 import BeautifulSoup
from markdown_it import MarkdownIt

MARKDOWN = MarkdownIt('commonmark', {'html': True}).enable('table')


def html(text):
    return BeautifulSoup(text, 'html.parser') if isinstance(text, str) else text


def plain(text):
    return ' '.join(normalize('NFC', html(text).get_text(' ')).casefold().replace('ё', 'е').split())


def one(items, label):
    items = list(items)
    if len(items) != 1:
        raise ValueError(f'{label}: ожидалось одно значение, найдено {len(items)}')
    return items[0]


def field(text, label, kind):
    return [e for line in text.splitlines()
            if re.search(rf'(?:^|\|)\s*(?:{label})\s*(?:[:№]|\|)', plain(line))
            for e in html(line).find_all('entity', type=kind)]


def ref(entity):
    key = entity.get('id', '')
    if not re.fullmatch(r'\d+|[a-z]+-[a-z]+', key):
        raise ValueError(f'Некорректный ID сущности: {key!r}')
    return '#' + key if key.isdigit() else key


def table_amount(text, heading, matches_row):
    candidates = []
    for table in html(MARKDOWN.render(text)).find_all('table'):
        rows = [tr.find_all(['th', 'td'], recursive=False) for tr in table.find_all('tr')]
        headers = [plain(c) for c in rows[0]]
        for i, title in enumerate(headers):
            if re.search(heading, title):
                row = one((r for r in rows[1:] if matches_row(r, headers)), 'Строка таблицы')
                candidates.extend(row[i].find_all('entity', type=['number', 'amount']))
    entity = one(candidates, 'Сумма в таблице')
    value = Decimal(entity['value'])
    if entity.get('unit', '').casefold() not in ('rubles', 'rub', 'руб.') or not value.is_finite() or value != value.quantize(Decimal('.01')):
        raise ValueError('Нужна конечная сумма в рублях с точностью до копейки')
    ref(entity)
    return entity, value


def day(value):
    try:
        return date.fromisoformat(value)
    except ValueError:
        return datetime.strptime(value, '%d.%m.%Y').date()


def load_documents(source):
    source = Path(source)
    if source.is_dir():
        return {p.relative_to(source).as_posix(): p.read_text(encoding='utf-8-sig')
                for p in sorted(source.rglob('*.md')) if '__MACOSX' not in p.parts}
    documents = {}
    with ZipFile(source) as archive:
        for entry in sorted(archive.infolist(), key=lambda e: e.filename):
            if entry.filename.endswith('.md') and '__MACOSX/' not in entry.filename:
                name = entry.filename
                if not entry.flag_bits & 0x800:
                    try:
                        name = name.encode('cp437').decode('utf-8')
                    except UnicodeError:
                        pass
                documents[name] = archive.read(entry).decode('utf-8-sig')
    return documents


def generate(documents, *, contract=None, start=None, end=None, row=None):
    """Return (LLM-format relations, audit). Read scope from README or arguments."""
    defaults = set()
    for text in documents.values():
        if 'период подтверждающих документов' in plain(text):
            periods = field(text, 'период подтверждающих документов', 'date')
            label = one((plain(line) for line in text.splitlines() if plain(line).startswith('проверяемая строка')), 'Строка отчёта')
            defaults.add((one(field(text, 'договор', 'id'), 'Договор')['value'],
                          periods[0]['value'], periods[1]['value'], label.split(':', 1)[1].strip(' «».')))
    default = one(defaults, 'Область проверки в README') if defaults else (None,) * 4
    contract, start, end, row = [v or d for v, d in zip((contract, start, end, row), default)]
    if not all((contract, start, end, row)):
        raise ValueError('Нужен README либо --contract, --start, --end, --row')
    stage, year = map(int, one(re.findall(r'(\d+)\s*этап\s*,?\s*(\d{4})', plain(row)), 'Этап и год'))
    start, end = max(day(start), date(year, 1, 1)), min(day(end), date(year, 12, 31))
    if start > end:
        raise ValueError('Период не пересекается с годом проверяемой строки')
    report_name = one((n for n, t in documents.items() if 'карточка учета фактических затрат' in plain(t)), 'Итоговый отчёт')
    report = documents[report_name]
    report_contract = one(field(report, 'контракт|договор', 'id'), 'Договор отчёта')
    if report_contract['value'] != contract:
        raise ValueError('Договор итогового отчёта не совпадает с областью проверки')
    target, actual = table_amount(report, r'страхов.*0300|0300.*страхов',
        lambda r, _: re.fullmatch(rf'{stage}\s*этап\s*,?\s*{year}\s*г?\.?\s*:?', plain(r[0])))
    audit = dict(status='unverified', actual=str(actual), expected=None, difference=None,
                 scope=dict(contract=contract, start=start.isoformat(), end=end.isoformat(), stage=stage, year=year),
                 target=dict(file=report_name, id=target['id']), included=[], excluded=[], errors=[])
    months = {}
    for name, text in sorted(documents.items()):
        try:
            if 'расчет страховых взносов' not in plain(text):
                audit['excluded'].append(dict(file=name, reason='Другой вид документа'))
                continue
            c = one(field(text, 'проект|договор', 'id'), 'Договор')
            if c['value'] != contract:
                audit['excluded'].append(dict(file=name, reason='Другой договор'))
                continue
            p = one(field(text, 'период', 'date'), 'Период')
            first = day(p['value'] + '-01' if re.fullmatch(r'\d{4}-\d{2}', p['value']) else p['value'])
            last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
            if first.day != 1:
                raise ValueError('Месячный период должен начинаться с первого числа')
            if last < start or first > end:
                audit['excluded'].append(dict(file=name, reason='Вне периода'))
                continue
            if first < start or last > end:
                raise ValueError('Месячный расчёт лишь частично входит в отчётный период')
            number = one(field(text, 'документ', 'id'), 'Номер документа')['value']
            if not number.strip():
                raise ValueError('Пустой номер документа')
            entity, amount = table_amount(text, 'начислено взносов', lambda r, h: plain(r[h.index('код статьи')]) == '0300')
            ym = first.strftime('%Y-%m')
            if ym in months:
                if months[ym][1] != amount or months[ym][4] != number:
                    raise ValueError(f'Конфликт сводных расчётов за {ym}')
                audit['excluded'].append(dict(file=name, reason=f'Дубликат {number}'))
                continue
            months[ym] = entity, amount, c, p, number
            audit['included'].append(dict(file=name, document=number, period=ym, amount=str(amount), id=entity['id']))
        except (ValueError, KeyError, IndexError, InvalidOperation) as error:
            audit['errors'].append(dict(file=name, reason=str(error)))
    required = {f'{year}-{m:02d}' for m in range(start.month, end.month + 1)}
    audit['missing_months'] = sorted(required - months.keys())
    relations = []
    if not audit['missing_months'] and not audit['errors']:
        selected = [months[ym] for ym in sorted(months)]
        refs = [ref(target), ref(report_contract)] + [ref(e) for a, _, c, p, _ in selected for e in (a, c, p)]
        if len(refs) != len(set(refs)):
            raise ValueError('ID сущностей должны быть глобально уникальными во всех выбранных документах')
        for _, _, c, p, _ in selected:
            relations.extend([dict(equation=f'{ref(c)} == {json.dumps(contract, ensure_ascii=False)}', tag='contract'),
                              dict(equation=f'{start:%d.%m.%Y} <= {ref(p)} && {ref(p)} <= {end:%d.%m.%Y}', tag='period')])
        relations.append(dict(equation=f'{ref(target)} == ' + ' + '.join(ref(e) for e, *_ in selected), tag='agg'))
        expected = sum((amount for _, amount, *_ in selected), Decimal(0))
        audit.update(status='ok' if actual == expected else 'mismatch', expected=str(expected), difference=str(actual - expected))
    return relations, audit


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('input', type=Path, help='ZIP или папка с размеченными Markdown')
    cli.add_argument('output', type=Path, help='Выходной JSON с ограничениями')
    for option in ('contract', 'start', 'end', 'row'):
        cli.add_argument('--' + option)
    args = vars(cli.parse_args())
    source, output = args.pop('input'), args.pop('output')
    try:
        if source.resolve() == output.resolve():
            raise ValueError('Входной и выходной пути должны различаться')
        relations, audit = generate(load_documents(source), **args)
        output.parent.mkdir(parents=True, exist_ok=True)
        for path, value in ((output, relations), (output.with_suffix('.audit.json'), audit)):
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"{audit['status']}: actual={audit['actual']}, expected={audit['expected']}, difference={audit['difference']}")
        return {'ok': 0, 'mismatch': 1, 'unverified': 2}[audit['status']]
    except (ValueError, OSError, KeyError, IndexError, InvalidOperation, BadZipFile, UnicodeError) as error:
        cli.exit(2, f'Ошибка: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())

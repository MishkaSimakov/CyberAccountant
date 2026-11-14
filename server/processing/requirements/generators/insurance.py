"""Aggregate article 0300 from one consolidated insurance calculation per month."""

import calendar
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from html import unescape

from processing.common.markup import parse_entity_tags

from processing.common.llm import LoggerLike
from processing.common.requirements import AssertionDefinition, FormulaDefinition, RequirementDefinition
from ..interface import RequirementGenerator, RequirementInput
from ..options import RequirementOptions


def _plain(text):
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", text)).casefold().replace("ё", "е").split())


def _tables(text):
    rows, fenced = [], False
    for line in text.splitlines() + [""]:
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        if not fenced and line.strip().startswith("|"):
            cells = re.split(r"(?<!\\)\|", line.strip().strip("|"))
            if not all(re.fullmatch(r"\s*:?-+:?\s*", c) for c in cells):
                rows.append(cells)
        elif rows:
            yield rows
            rows = []


class InsuranceGenerator(RequirementGenerator):
    def __init__(self, options: RequirementOptions = RequirementOptions()):
        self.options = options

    def generate(self, source: RequirementInput, *, logger: LoggerLike) -> list[RequirementDefinition]:
        """Use existing IDs, normalized values and scope; emit only 0300 relations."""
        numeric = self.options.id_style == "numeric"
        entities = source.registry.entities
        by_id = {str(e.num): e for e in entities}
        ref = lambda e: f"#{e.num}" if numeric else e.mnemonic
        target_keys = {str(k).lstrip("#") for k in source.scope.target_row or []}

        def tagged(text):
            return [by_id[tag.attributes["id"]] for tag in parse_entity_tags(text)
                    if tag.attributes.get("id") in by_id]

        def one(items, label):
            if len(items) != 1:
                raise ValueError(f"{label}: требуется одно однозначное значение")
            return items[0]

        def amount(cell):
            e = one([e for e in tagged(cell) if e.type == "amount"], "Сумма взносов")
            if e.unit.upper() not in {"RUB", "RUB_K"}:
                raise ValueError("Не установлена сумма взносов в рублях")
            return e

        def field(text, label, kind):
            return one([e for line in text.splitlines()
                        if re.search(rf"(?:^|\|)\s*(?:{label})\s*(?:[:№]|\|)", _plain(line))
                        for e in tagged(line) if e.type == kind], label)

        targets = []
        for document in source.corpus.documents:
            file, text = str(document.revision.document_id), document.markdown
            if not any(e.file == file and e.role == "card" for e in entities):
                continue
            for rows in _tables(text):
                for column, header in enumerate(rows[0]):
                    if "0300" in _plain(header) and "страхов" in _plain(header):
                        for row in rows[1:]:
                            if column < len(row):
                                targets.extend(e for e in tagged(row[column])
                                               if e.type == "amount" and (
                                                       str(e.num) in target_keys or e.mnemonic in target_keys))
        if not targets:
            return []  # This generator does not apply to other report columns.
        target = targets[0]

        def unverifiable(reason):
            return [AssertionDefinition(text=f"Страховые взносы 0300: {reason}",
                                        ids_related=tuple(e.num for e in targets), condition=None, source="insurance")]

        if len(targets) != 1:
            return unverifiable("неоднозначная итоговая ячейка")
        if target.unit.upper() not in {"RUB", "RUB_K"}:
            return unverifiable("итоговая ячейка должна быть задана в рублях")
        report = source.scope
        contract = report.contract_number
        start, end = report.period_start, report.period_end
        if not contract or start is None or end is None:
            return unverifiable("не заданы договор и отчётный период")
        if not isinstance(start, date) or not isinstance(end, date):
            return unverifiable("некорректный отчётный период")
        years = set(re.findall(r"\b(20\d{2})\b", _plain(source.scope.target_label or "")))
        if len(years) == 1:
            year = int(years.pop())
            start, end = max(start, date(year, 1, 1)), min(end, date(year, 12, 31))
        if start > end:
            return unverifiable("период не пересекается с годом проверяемой строки")
        months, problems = {}, []
        for document in sorted(source.corpus.documents, key=lambda doc: doc.revision.document_id):
            file, text = str(document.revision.document_id), document.markdown
            if document.role != "supporting":
                continue
            if "расчет страховых взносов" not in _plain(text):
                continue
            try:
                if str(field(text, "проект|договор", "contract").value).strip() != contract:
                    continue
                p = field(text, "период", "date")
                first = date.fromordinal(int(p.value))
                last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
                if last < start or first > end:
                    continue
                if first.day != 1 or first < start or last > end:
                    raise ValueError("нужен полный месячный период начисления")
                number = field(text, "документ", "docid").value
                if not number:
                    raise ValueError("не установлен номер расчёта")
                candidates = []
                for rows in _tables(text):
                    headers = [_plain(c) for c in rows[0]]
                    for column, header in enumerate(headers):
                        if "начислено взносов" in header:
                            code = one([i for i, h in enumerate(headers) if h == "код статьи"], "Код статьи")
                            candidates.extend(amount(r[column]) for r in rows[1:] if _plain(r[code]) == "0300")
                e = one(candidates, "Начислено взносов по статье 0300")
                value = Decimal(str(e.value))
                if not value.is_finite():
                    raise ValueError("не определена сумма начисленных взносов")
                ym = first.year, first.month
                if ym in months:
                    if months[ym][:2] != (number, value):
                        raise ValueError(f"конфликт сводных расчётов за {first:%Y-%m}")
                    continue
                months[ym] = number, value, e
            except (ValueError, TypeError, IndexError, InvalidOperation) as error:
                problems.append(f"{file}: {error}")
        required, cursor = set(), start.replace(day=1)
        while cursor <= end:
            required.add((cursor.year, cursor.month))
            cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
        missing = sorted(required - months.keys())
        if missing:
            problems.append("нет расчётов за " + ", ".join(f"{y}-{m:02d}" for y, m in missing))
        if problems:
            return unverifiable("; ".join(problems))
        return [FormulaDefinition(equation=ref(target) + " == " + " + ".join(ref(months[m][2]) for m in sorted(months)),
                                  tag="agg", source="insurance")]

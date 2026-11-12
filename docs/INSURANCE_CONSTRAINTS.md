# Ограничения для статьи 0300 без LLM

`insurance_constraints.py` — отдельный скрипт для сводных месячных расчётов
страховых взносов. Вход: ZIP или папка с UTF-8 Markdown, размеченными тегами
`<entity type="..." value="..." unit="..." id="...">`. Используются ID из входа;
они должны быть глобально уникальными. Это формат предоставленного `tagged.zip`.
Скрипт не выполняет OCR, не вызывает LLM и не меняет этапы основного пайплайна.

Из корня проекта (Python 3.10+):

```sh
python -m pip install -r requirements-constraints.txt
python insurance_constraints.py tagged.zip output/insurance.json
# Или уже распакованная папка:
python insurance_constraints.py tagged output/insurance.json
```

Markdown разбирается через markdown-it-py, теги — через Beautiful Soup.
Договор, период и проверяемая строка берутся из README внутри входных данных.
При необходимости их можно передать явно:

```sh
python insurance_constraints.py tagged.zip output/insurance.json --contract 24/03/15-01 --start 2026-01-01 --end 2026-05-31 --row "3 этап, 2026 г."
```

Скрипт находит документы по заголовку «Расчёт страховых взносов», выбирает строку
`0300` и колонку «Начислено взносов». Проверяет договор и месячный период,
исключает дубли и требует один сводный расчёт на каждый месяц. Денежные значения
считаются через `Decimal`. Период в теге — `YYYY-MM` либо дата первого числа месяца.
Частично пересекающийся месяц не распределяется автоматически. Привязка к этапу
основана на договоре и периоде: неразмеченные распределения между параллельными
этапами этим профилем не поддерживаются.

Результаты:

- `insurance.json` — список ограничений в формате `stage5_relations`: поля
  `equation`, `tag` (`contract`, `period`, `agg`), ссылки `#id` или мнемонические ID.
  Например: `{"equation": "#108 == #191 + #197 + #203 + #209 + #215", "tag": "agg"}`.
- `insurance.audit.json` — статус, суммы, разница, включённые и исключённые файлы,
  ID исходных сумм, пропущенные месяцы и ошибки.

При пропусках или конфликтующих расчётах ограничения не создаются. Внутренние
формулы КФЗ остаются задачей общего генератора. Коды выхода: `0` — совпало,
`1` — расхождение, `2` — недостаточно данных или ошибка входа.

Вызов из Python:

```python
from insurance_constraints import generate, load_documents

relations, audit = generate(load_documents("tagged.zip"))
```

Проверка скрипта:

```sh
python -m pip install pytest
python -m pytest -q tests/test_insurance_constraints.py
```

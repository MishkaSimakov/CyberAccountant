"""The target_row field contains a row label, including numeric text."""

import pytest
from pydantic import ValidationError

from app.schemas.reports import ReportCreate


@pytest.mark.parametrize('value, expected', [
    ('3 этап, 2026 г.', '3 этап, 2026 г.'), ('Итого', 'Итого'),
    ('3', '3'), (' 003 ', '003'), ('0', '0'), ('-1', '-1'), ('1.5', '1.5'),
    (None, None),
])
def test_target_row_preserves_label(value, expected):
    assert ReportCreate(name='КФЗ', target_row=value).target_row == expected


@pytest.mark.parametrize('value', [3, 0, -1, 1.5, True])
def test_target_row_requires_text(value):
    with pytest.raises(ValidationError):
        ReportCreate(name='КФЗ', target_row=value)

"""The existing target_row field accepts a label or a one-based row number."""

import pytest
from pydantic import ValidationError

from app.schemas.reports import ReportCreate


@pytest.mark.parametrize('value, expected', [(3, '3'), ('3', '3'), (' 003 ', '3'),
                                           ('3 этап, 2026 г.', '3 этап, 2026 г.'), (None, None)])
def test_target_row_label_and_number(value, expected):
    assert ReportCreate(name='КФЗ', target_row=value).target_row == expected


@pytest.mark.parametrize('value', [0, -1, '0', '-1', '1.5', 1.5, True])
def test_target_row_number_must_be_a_positive_integer(value):
    with pytest.raises(ValidationError):
        ReportCreate(name='КФЗ', target_row=value)

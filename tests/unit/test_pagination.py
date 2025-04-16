import pytest

from avtdl.core.db import calculate_offset

testcases = {
    'first page': (1, 10, 100, (10, 90)),
    'second page': (2, 10, 100, (10, 80)),
    'last full page': (10, 10, 100, (10, 0)),
    'last page with less rows': (10, 10, 95, (5, 0)),
    'middle page': (3, 10, 50, (10, 20)),
    'no rows (first page)': (1, 10, 0, (0, 0)),
    'less than per_page': (1, 10, 5, (5, 0)),
    'exactly per_page': (1, 5, 5, (5, 0)),
}


@pytest.mark.parametrize('page, per_page, total_rows, expected', testcases.values(), ids=testcases.keys())
def test_offset(page, per_page, total_rows, expected):
    limit, offset = calculate_offset(page, per_page, total_rows)
    assert (limit, offset) == expected

import json
import re
from typing import Dict, Tuple

import pytest

from avtdl.core.yaml import CommentedData, merge_data, yaml_dump, yaml_load


def merge_yaml(base: str, data_json: str) -> CommentedData:
    base = yaml_load(base)
    data = json.loads(data_json)
    merged = merge_data(base, data)
    return merged


testcases: Dict[str, Tuple[str, str, str]] = {
    'comments preserved': (
        """
        # top comment
        key:
        # mapping comment
          - "0"
          - "1" # list comment
          - 2""",
        '{"key": ["0", "1", 3]}',

        """
        # top comment
        key:
        # mapping comment
          - "0"
          - "1"
          - 3""",
    ),
    'parenthesses preserved': (
        """top:
          - "a"
          - 'b'
          - 'c'""",
        '{"top": ["a", "b", "X"]}',

        """top:
          - "a"
          - 'b'
          - X""",
    ),
    'dict update': (
        """top:
          a: 1
          b: 2
          c: 3""",
        '{"top": {"o": 0, "a": 1, "c": 3, "d": 4 }}',

        """top:
          o: 0
          a: 1
          c: 3
          d: 4""",
    ),
}


@pytest.mark.parametrize('base_yaml, json_data, expected', testcases.values(), ids=testcases.keys())
def test_top_level(base_yaml, json_data, expected):
    merged = merge_yaml(base_yaml, json_data)
    result = yaml_dump(merged)
    assert re.sub(r'[\n ]', '', result) == re.sub(r'[\n ]', '', expected)

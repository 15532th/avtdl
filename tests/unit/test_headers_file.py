import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

import pytest

from avtdl.core.monitors import load_headers


def add_to_file(text: str, path: Path, encoding: Optional[str] = None):
    with open(path, 'a+t', encoding=encoding) as fp:
        fp.write(text)


testcases: Dict[str, Tuple[str, str, Optional[Dict[str, str]]]] = {
    'valid_plaintext_file': (
        'headers.txt',
        'key1: value1\nkey2: value2\n',
        {'key1': 'value1', 'key2': 'value2'}
    ),
    'valid_json_file': (
        'headers.json',
        '{"key1": "value1", "key2": "value2"}',
        {'key1': 'value1', 'key2': 'value2'}
    ),
    'empty_plaintext_file': (
        'headers.txt',
        '',
        {}
    ),
    'empty_json_file': (
        'headers.json',
        '',
        None
    ),
    'invalid_json_file': (
        'headers.json',
        '{"key1": "value1", "key2": "value2",}',
        None
    ),
    'plaintext_file_empty_lines_ignored': (
        'headers.txt',
        '\nkey1: value1\n\nkey2: value2\n\n\n',
        {'key1': 'value1', 'key2': 'value2'}
    ),
    'plaintext_file_comments_ignored': (
        'headers.txt',
        'key1: value1\nkey2: value2\n# key3: value3',
        {'key1': 'value1', 'key2': 'value2'}
    ),
    'plaintext_file_only_invalid_value_skipped': (
        'headers.txt',
        'key1: value1\nkey2: value2\nkey3 value3',
        {'key1': 'value1', 'key2': 'value2'}
    ),
    'plaintext_file_no_space_after_semicolon': (
        'headers.txt',
        'key1:value1\nkey2:value2\n',
        {'key1': 'value1', 'key2': 'value2'}
    ),
    'plaintext_file_parentheses_preserved': (
        'headers.txt',
        '"key1": value1\nkey2: "value2"\n',
        {'"key1"': 'value1', 'key2': '"value2"'}
    ),
    'plaintext_file_multiple_semicolons': (
        'headers.txt',
        'key1: value1\nkey2: value: 2\n',
        {'key1': 'value1', 'key2': 'value: 2'}
    ),
}


@pytest.mark.parametrize('name, content, expected', testcases.values(), ids=testcases.keys())
def test_headers_file_parsing(tmp_path, name, content, expected):
    path = tmp_path / name
    add_to_file(content, path)
    result = load_headers(path, logging.getLogger('test_headers'))
    assert result == expected

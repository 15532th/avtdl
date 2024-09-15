import logging
import os
import re
from pathlib import Path
from typing import List

import pytest

from avtdl.avtdl import load_config, parse_config
from avtdl.core.config import config_sancheck
from avtdl.core.loggers import silence_library_loggers


def read_file(path: str) -> str:
    with open(path, 'rt', encoding='utf8') as fp:
        return fp.read()


def text_as_path(directory, text: str) -> Path:
    directory = Path(directory)
    filename = directory / str(hash(text))
    with filename.open('wt', encoding='utf8') as fp:
        fp.write(text)
    return filename


def find_examples(text) -> List[str]:
    """return list with code blocks in yaml format"""
    return re.findall('```yaml([^`]+)```', text)


def configs(file: str) -> List[str]:
    configs_text = read_file(file)
    example_configs = find_examples(configs_text)
    return example_configs


def empty_cookies(tmp_path: Path):
    text = '# Netscape HTTP Cookie File\n\n'
    with open(tmp_path / 'cookies.txt', 'wt', encoding='utf8') as fp:
        fp.write(text)


# fixtures does not exist yet when parametrization happens,
# therefore it is not possible to use return value of a fixture
# for tests parametrization. All preparations are done by regular
# functions instead
examples = [*configs('EXAMPLES.md'), *configs('example.config.yml')]
ids = [f'example_{i}' for i in range(len(examples))]


@pytest.mark.parametrize('config', examples, ids=ids)
def test_config_loading(config: str, tmp_path, caplog):
    """
    Smoke test for configuration examples

    Extracts code blocks containing yaml from examples docs
    and tries parsing and loading each of them as config.

    Value of the cookies_file parameter used in examples
    must always be 'cookies.txt'.
    """

    silence_library_loggers()
    caplog.set_level(logging.WARNING)

    os.chdir(tmp_path)
    empty_cookies(tmp_path)
    path = text_as_path(tmp_path, config)

    conf = load_config(path)
    actors, chains = parse_config(conf)
    config_sancheck(actors, chains)

    assert not caplog.records

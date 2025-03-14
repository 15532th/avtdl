from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from avtdl.core.interfaces import ActorConfig, RuntimeContext, TextRecord
from avtdl.plugins.file.text_file import FileAction, FileActionEntity


def read_file(path: Path, encoding: Optional[str] = None) -> str:
    with open(path, 'r+t', encoding=encoding) as fp:
        return fp.read()


@pytest.fixture()
def params():
    """provides default value of no additional parameters, replaced in parametrized classes"""
    return {}


@pytest.fixture()
def entity(tmp_path, params) -> FileActionEntity:
    entity_params = {'name': 'test_entity', 'path': tmp_path,  'filename': 'data.txt'}
    entity_params.update(params)
    entity = FileActionEntity.model_validate(entity_params)
    return entity


@pytest.fixture()
def actor(entity) -> FileAction:
    ctx = RuntimeContext.create()
    actor = FileAction(ActorConfig(name='to_file'), [entity], ctx)
    return actor


class TestFirstUpdate:
    testcases = {
        'default entity settings':
            (
                {},
                ['Line 1\nLine 2'],
                'Line 1\nLine 2\n'
            ),
        'output format set to text':
            (
                {'output_format': 'text'},
                ['Line 1', 'Line 2'],
                'Line 1\nLine 2\n'
            ),
        'overwrite with append: both records are written':
            (
                {'overwrite': True, 'append': True},
                ['Line 1', 'Line 2'],
                'Line 1\nLine 2\n'
            ),
        'overwrite with no append: entire file is overwritten':
            (
                {'overwrite': True, 'append': False},
                ['Line 1', 'Line 2'],
                'Line 2\n'
            ),
        'no overwrite with append: only first record written':
            (
                {'overwrite': False, 'append': True},
                ['Line 1', 'Line 2'],
                'Line 1\n'
            ),
        'no overwrite with no append: only first record written':
            (
                {'overwrite': False, 'append': False},
                ['Line 1', 'Line 2'],
                'Line 1\n'
            ),
        'format with missing placeholder':
            (
                {'output_template': '{text} : {image}', 'missing': 'N/A'},
                ['Line 1'],
                'Line 1 : N/A\n'
            ),
        'format without missing placeholder':
            (
                {'output_template': '{text} : {image}'},
                ['Line 1'],
                'Line 1 : {image}\n'
            ),
        'prefix and postfix':
            (
                {'prefix': '---', 'postfix': '|||'},
                ['Line 1', 'Line 2'],
                '---Line 1|||---Line 2|||'
            ),
    }

    @staticmethod
    @pytest.mark.asyncio
    @pytest.mark.parametrize('params, data, expected', testcases.values(), ids=testcases.keys())
    async def test_single_update(entity: FileActionEntity, actor: FileAction, params: Dict[str, Any], data: List[str], expected: str):
        records = [TextRecord(text=text) for text in data]
        for record in records:
            actor.handle(entity, record)
        path = (entity.path or Path()) / entity.filename
        output = read_file(path, encoding=entity.encoding)
        assert output == expected

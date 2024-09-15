import asyncio
import os
from pathlib import Path
from typing import Optional

import pytest

from avtdl.core.interfaces import ActorConfig
from avtdl.plugins.file.text_file import FileMonitor, FileMonitorEntity


def add_to_file(text: str, path: Path, encoding: Optional[str] = None):
    with open(path, 'a+t', encoding=encoding) as fp:
        fp.write(text)


@pytest.fixture()
def params():
    """provides default value of no additional parameters, replaced in parametrized classes"""
    return {}


@pytest.fixture()
def entity(tmp_path, params) -> FileMonitorEntity:
    entity_params = {'name': 'test_entity', 'path': tmp_path / 'data.txt'}
    entity_params.update(params)
    entity = FileMonitorEntity.model_validate(entity_params)
    return entity


@pytest.fixture()
def monitor(entity) -> FileMonitor:
    monitor = FileMonitor(ActorConfig(name='text_monitor'), [entity])
    return monitor


@pytest.mark.asyncio
async def test_no_change_on_update(entity, monitor):
    data = 'Line 1\nLine 2\n'
    add_to_file(data, entity.path, entity.encoding)

    _ = await monitor.get_new_records(entity)
    records = await monitor.get_new_records(entity)

    output = [str(record) for record in records]
    assert output == []


class TestFirstUpdate:
    testcases = {
        'default entity settings':
        (
            {},
            'Line 1\nLine 2\n',
            ['Line 1\nLine 2']
        ),
        'split_lines splits on newline by default':
        (
            {'split_lines': True},
            'Line 1\nLine 2\n',
            ['Line 1', 'Line 2']
        ),
        'split_lines with record_start and record_end provided':
        (
            {'split_lines': True, 'record_start': 'Line', 'record_end': '$'},
            '[LOG] Line 1a Line 1b\n[LOG] Unrelated record\n[LOG] Line 2',
            ['Line 1a Line 1b', 'Line 2']
        ),
        'split_lines with record_start including entire record and record_end being "match all" pattern':
        (
            {'split_lines': True, 'record_start': 'Line [a-z0-9]+', 'record_end': ''},
            '[LOG] Line 1a Line 1b\n[LOG] Line 2',
            ['Line 1a', 'Line 1b', 'Line 2']
        ),
        'with quiet_start text is discarded on first update':
        (
            {'quiet_start': True},
            'Line 1\nLine 2\n',
            []
        )
    }

    @staticmethod
    @pytest.mark.asyncio
    @pytest.mark.parametrize('params, data, expected', testcases.values(), ids=testcases.keys())
    async def test_single_update(entity, monitor, params, data, expected):
        add_to_file(data, entity.path, entity.encoding)

        records = await monitor.get_new_records(entity)

        output = [str(record) for record in records]
        assert output == expected


class TestSecondUpdateAppend:
    testcases = {
        'without follow enabled entire file content gets read on second update':
        (
            {'follow': False},
            'Line 1\nLine 2\n', 'Line 3\nLine 4\n',
            ['Line 1\nLine 2\nLine 3\nLine 4']
        ),
        'follow with second update shorter than first':
        (
            {'follow': True},
            'Line 1\nLine 2\nLine 3\n', 'Line 4\n',
            ['Line 4']
        ),
        'follow with first update shorter than second':
        (
            {'follow': True},
            'Line 1\n', 'Line 2\nLine 3\nLine 4\n',
            ['Line 2\nLine 3\nLine 4']
        ),
        'partial record stored in buffer gets merged when continuation appended':
        (
            {'follow': True, 'split_lines': True, 'record_start': r'\[LOG\]', 'record_end': r'\.'},
            '[LOG] Line 1.\n[LOG] Line 2a\n', 'Line 2b\nLine2c.\nUnrelated line.\n[LOG] Line 3.',
            ['[LOG] Line 2a\nLine 2b\nLine2c.', '[LOG] Line 3.']
        )
    }

    @staticmethod
    @pytest.mark.asyncio
    @pytest.mark.parametrize('params, data1, data2, expected', testcases.values(), ids=testcases.keys())
    async def test_append(entity, monitor, params, data1, data2, expected):
        add_to_file(data1, entity.path, entity.encoding)
        _ = await monitor.get_new_records(entity)

        await asyncio.sleep(0.01)

        add_to_file(data2, entity.path, entity.encoding)
        records = await monitor.get_new_records(entity)

        output = [str(record) for record in records]
        assert output == expected


class TestSecondUpdateRotate:
    testcases = {
        'old file content gets cut on rotate':
        (
            {'follow': False},
            'Line 1\nLine 2\n', 'Line 3\nLine 4\n',
            ['Line 3\nLine 4']
        ),
        'follow with second update shorter than first':
        (
            {'follow': True},
            'Line 1\nLine 2\nLine 3\n', 'Line 4\n',
            ['Line 4']
        ),
        'follow with first update shorter than second':
        (
            {'follow': True},
            'Line 1\n', 'Line 2\nLine 3\nLine 4\n',
            ['Line 2\nLine 3\nLine 4']
        ),
        'partial record stored in buffer is discarded on rotate':
        (
            {'follow': True, 'split_lines': True, 'record_start': r'\[LOG\]', 'record_end': r'\.'},
            '[LOG] Line 1.\n[LOG] Line 2a\n', 'Line 2b\nLine2c.\nUnrelated line.\n[LOG] Line 3.',
            ['[LOG] Line 3.']
        ),
        "quiet_start doesn't get carried over to rotated file":
        (
            {'quiet_start': True},
            '', 'Line 3\nLine 4\n',
            ['Line 3\nLine 4']
        )
    }

    @staticmethod
    @pytest.mark.asyncio
    @pytest.mark.parametrize('params, data1, data2, expected', testcases.values(), ids=testcases.keys())
    async def test_rotate(entity, monitor, params, data1, data2, expected):
        add_to_file(data1, entity.path, entity.encoding)
        _ = await monitor.get_new_records(entity)

        os.unlink(entity.path)
        await asyncio.sleep(0.01)

        add_to_file(data2, entity.path, entity.encoding)
        records = await monitor.get_new_records(entity)

        output = [str(record) for record in records]
        assert output == expected


class TestEntityValidators:
    """makes sure constructing entity only raises when expected"""

    @staticmethod
    def test_defaults():
        _ = FileMonitorEntity(name='test', path=Path('dummy'))

    @staticmethod
    def test_valid_regexp():
        _ = FileMonitorEntity(name='test', path=Path('dummy'), record_start='^.*$')

    @staticmethod
    def test_invalid_start_regexp():
        with pytest.raises(ValueError):
            _ = FileMonitorEntity(name='test', path=Path('dummy'), record_start='*invalid regexp*')

    @staticmethod
    def test_invalid_end_regexp():
        with pytest.raises(ValueError):
            _ = FileMonitorEntity(name='test', path=Path('dummy'), record_end='*invalid regexp*')

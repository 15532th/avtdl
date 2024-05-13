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


class TestUpdate:

    @staticmethod
    @pytest.mark.asyncio
    async def test_default(entity, monitor):
        data = 'Line 1\nLine 2\n'
        add_to_file(data, entity.path, entity.encoding)

        records = await monitor.get_new_records(entity)
        output = [str(record) for record in records]

        assert output == ['Line 1\nLine 2']

    @staticmethod
    @pytest.mark.asyncio
    async def test_no_change_on_update(entity, monitor):
        data = 'Line 1\nLine 2\n'
        add_to_file(data, entity.path, entity.encoding)

        _ = await monitor.get_new_records(entity)
        records = await monitor.get_new_records(entity)

        output = [str(record) for record in records]
        assert output == []

    @staticmethod
    @pytest.mark.asyncio
    async def test_change_on_update(entity, monitor):
        data1 = 'Line 1\nLine 2\n'
        add_to_file(data1, entity.path, entity.encoding)
        _ = await monitor.get_new_records(entity)

        await asyncio.sleep(0.01)

        data2 = 'Line 3\nLine 4\n'
        add_to_file(data2, entity.path, entity.encoding)
        records = await monitor.get_new_records(entity)

        output = [str(record) for record in records]
        assert output == ['Line 1\nLine 2\nLine 3\nLine 4']

    @staticmethod
    @pytest.mark.asyncio
    @pytest.mark.parametrize('params', [{'follow': True}])
    async def test_follow(entity, monitor, params):
        data1 = 'Line 1\nLine 2\n'
        add_to_file(data1, entity.path, entity.encoding)
        _ = await monitor.get_new_records(entity)

        await asyncio.sleep(0.01)

        data2 = 'Line 3\nLine 4\n'
        add_to_file(data2, entity.path, entity.encoding)
        records = await monitor.get_new_records(entity)

        output = [str(record) for record in records]
        assert output == ['Line 3\nLine 4']

    @staticmethod
    @pytest.mark.asyncio
    @pytest.mark.parametrize('params', [{'follow': True}])
    async def test_follow_rotate(entity, monitor, params):
        data1 = 'Line 1\nLine 2\n'
        add_to_file(data1, entity.path, entity.encoding)
        _ = await monitor.get_new_records(entity)

        os.unlink(entity.path)
        await asyncio.sleep(0.01)

        data2 = 'Line 3\nLine 4\n'
        add_to_file(data2, entity.path, entity.encoding)
        records = await monitor.get_new_records(entity)

        output = [str(record) for record in records]
        assert output == ['Line 3\nLine 4']


class TestIndividualOptions:
    testcases = [
        (
            {'split_lines': True},
            'Line 1\nLine 2\n',
            ['Line 1', 'Line 2']
        ),
        (
            {'split_lines': True, 'record_start': 'Line', 'record_end': '$'},
            '[LOG] Line 1a Line 1b\n[LOG] Line 2',
            ['Line 1a Line 1b', 'Line 2']
        )
    ]

    @staticmethod
    @pytest.mark.asyncio
    @pytest.mark.parametrize('params, data, expected', testcases)
    async def test_single_option(entity, monitor, params, data, expected):
        add_to_file(data, entity.path, entity.encoding)

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

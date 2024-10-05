import asyncio
from asyncio.subprocess import Process
from typing import Optional

import pytest

from avtdl.core.interfaces import Record, TextRecord
from avtdl.plugins.execute.run_command import Command, CommandConfig, CommandEntity


@pytest.fixture()
def text_record():
    return TextRecord(text='record1')


async def run_command(entity: CommandEntity, record: Record) -> Process:
    config = CommandConfig(name='test')
    actor = Command(config, [entity])

    flag = asyncio.Event()
    completed_process: Optional[Process] = None

    def done(process: Process, entity: CommandEntity, record: Record) -> None:
        nonlocal completed_process
        completed_process = process
        flag.set()

    actor.add_done_callback(done)

    actor.handle(entity, record)
    try:
        await asyncio.wait_for(flag.wait(), timeout=5)
    except asyncio.TimeoutError:
        pytest.fail('subprocess takes too long to complete')

    assert completed_process is not None
    return completed_process


@pytest.mark.asyncio
async def test_echo(capfd):
    text_record = TextRecord(text='record1')
    entity = CommandEntity(name='test', command='echo {text}')

    await run_command(entity, text_record)

    assert capfd.readouterr().out == 'record1\n'


@pytest.mark.asyncio
async def test_exit0(text_record):
    entity = CommandEntity(name='test', command='true')
    process = await run_command(entity, text_record)
    assert process.returncode == 0


@pytest.mark.asyncio
async def test_exit1(text_record):
    entity = CommandEntity(name='test', command='false')
    process = await run_command(entity, text_record)
    assert process.returncode == 1

import asyncio
import datetime
from asyncio.subprocess import Process
from typing import List, Optional

import pytest

from avtdl.core.interfaces import Record, TextRecord
from avtdl.plugins.execute.run_command import Command, CommandConfig, CommandEntity
from avtdl.plugins.rss.generic_rss import GenericRSSRecord


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


class TestCommandArgs:

    @staticmethod
    def args_for(entity: CommandEntity, record: Record) -> List[str]:
        config = CommandConfig(name='test')
        actor = Command(config, [entity])
        args = actor.args_for(entity, record)
        return args

    @staticmethod
    @pytest.fixture()
    def text_record():
        return TextRecord(text='test test')

    @staticmethod
    @pytest.fixture()
    def feed_record():
        record = GenericRSSRecord(
            uid='1',
            url='https://example.com/1.html',
            author='example.com',
            title='#1',
            summary='about #1',
            published=datetime.datetime.now()
        )
        return record


class TestPlaceholders(TestCommandArgs):

    def test_text_placeholder(self, text_record):
        entity = CommandEntity(name='test', command='echo {text}')
        result = self.args_for(entity, text_record)
        assert result == ['echo', 'test test']

    def test_url_placeholder(self, feed_record):
        entity = CommandEntity(name='test', command='yt-dlp --cookies cookies.txt -f 220k/best {url}')
        result = self.args_for(entity, feed_record)
        assert result == ['yt-dlp', '--cookies', 'cookies.txt', '-f', '220k/best', 'https://example.com/1.html']

    def test_other_placeholders(self, feed_record):
        entity = CommandEntity(
            name='test',
            command="yt-dlp --add-header Referer:'https://example.com' {url} --output '[{author}] {title} ({uid}).%(ext)s'"
        )
        result = self.args_for(entity, feed_record)
        assert result == ['yt-dlp', '--add-header', 'Referer:https://example.com',
                          'https://example.com/1.html', '--output', '[example.com] #1 (1).%(ext)s']

    def test_missing_placeholder_unchanged(self, text_record):
        entity = CommandEntity(name='test', command='echo {text} {image}')
        result = self.args_for(entity, text_record)
        assert result == ['echo', 'test test', '{image}']

    def test_static_placeholders(self, text_record):
        entity = CommandEntity(name='test', command='echo {text} {image}',
                               static_placeholders={'{image}': '-o {text}.txt'})
        result = self.args_for(entity, text_record)
        assert result == ['echo', 'test test', '-o', 'test test.txt']


class TestFormatter(TestCommandArgs):

    def test_nested_quotes(self, text_record):
        entity = CommandEntity(name='test', command='''py -c "print('[hi there, {text}]'); exit(1)"''')
        result = self.args_for(entity, text_record)
        assert result == ['py', '-c', "print('[hi there, test test]'); exit(1)"]

    def test_escape_quotes(self, text_record):
        entity = CommandEntity(
            name='test',
            command='powershell "(New-Object -ComObject Wscript.Shell).Popup(\\"{text}\\", 0, \\"Done\\", 0x0)"'
        )
        result = self.args_for(entity, text_record)
        assert result == ['powershell', '(New-Object -ComObject Wscript.Shell).Popup("test test", 0, "Done", 0x0)']

    def test_datetime(self, feed_record):
        entity = CommandEntity(name='test', command="yt-dlp {url} --output '[%Y-%m-%d] {title}.%(ext)s'")
        now = datetime.datetime.now()
        result = self.args_for(entity, feed_record)
        assert result == ['yt-dlp', 'https://example.com/1.html',
                          '--output', f'[{now.year}-{now.month:02}-{now.day:02}] #1.%(ext)s']
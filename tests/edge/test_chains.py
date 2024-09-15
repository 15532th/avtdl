import asyncio
from dataclasses import dataclass
from typing import Dict, List, Tuple, Union

import pytest
import yaml

from avtdl.avtdl import parse_config
from avtdl.core.config import config_sancheck
from avtdl.core.interfaces import Actor, Record, TextRecord
from avtdl.core.loggers import set_logging_format, silence_library_loggers
from avtdl.core.utils import monitor_tasks
from avtdl.plugins.utils.utils import TestAction, TestMonitor


@dataclass
class Sender:
    actor: str
    entity: str
    records: List[Union[str, Record]]

    def __post_init__(self):
        updated_records = []
        for item in self.records:
            if isinstance(item, str):
                record = TextRecord(text=item)
            else:
                record = item
            updated_records.append(record)
        self.records = updated_records


@dataclass
class Receiver:
    actor: str
    entity: str
    expected_history: List[Union[str, Record]]
    records_received: int = 0

    def __post_init__(self):
        updated_expected_history = []
        for item in self.expected_history:
            if isinstance(item, str):
                record = TextRecord(text=item)
            else:
                record = item
            updated_expected_history.append(record)
        self.expected_history = updated_expected_history

    def increment(self):
        self.records_received += 1

    @property
    def done(self) -> bool:
        return self.records_received >= len(self.expected_history)


async def test_run(config: str, senders: List[Sender], receivers: List[Receiver]):
    silence_library_loggers()
    set_logging_format('WARNING')

    conf = yaml.load(config, Loader=yaml.FullLoader)
    actors, chains = parse_config(conf)
    config_sancheck(actors, chains)

    tasks = []
    for runnable in actors.values():
        task = asyncio.create_task(runnable.run(), name=f'{runnable!r}.{hash(runnable)}')
        tasks.append(task)

    send_records(actors, senders)
    try:
        await asyncio.wait_for(monitor_tasks(tasks), 3)
    except (asyncio.CancelledError, asyncio.TimeoutError) as e:
        pass
    check_received(actors, receivers)


def send_records(actors: Dict[str, Actor], senders: List[Sender]):
    for sender in senders:
        actor = actors[sender.actor]
        entity = actor.entities[sender.entity]
        if not isinstance(actor, TestMonitor):
            raise Exception(f'incorrect sender: expected TestMonitor, got {actor.__class__}')
        for record in sender.records:
            assert isinstance(record, Record), f'post_init hook failed for {sender}'
            actor.produce(entity.name, record)


def check_received(actors: Dict[str, Actor], receivers: List[Receiver]):
    for receiver in receivers:
        actor = actors[receiver.actor]
        entity = actor.entities[receiver.entity]
        if not isinstance(actor, TestAction):
            raise Exception(f'incorrect receiver: expected TestAction, got {actor.__class__}')
        assert all(isinstance(record, Record) for record in receiver.expected_history), f'post_init hook failed for {receiver}'

        actual_history = actor.history[entity.name]
        assert actual_history == receiver.expected_history


testcases: Dict[str, Tuple[str, List[Sender], List[Receiver]]] = {
    'single chain': (
        '''
actors:
  utils.producer:
    entities:
      - name: test1
  utils.consumer:
    entities:
      - name: test2
      - name: test3
chains:
  chain1:
    - utils.producer:
      - test1
    - utils.consumer:
      - test2
    - utils.consumer:
      - test3
        ''',
        [Sender('utils.producer', 'test1', ['one', 'two', 'three'])],
        [Receiver('utils.consumer', 'test2', ['one', 'two', 'three']),
         Receiver('utils.consumer', 'test3', [])]
    )
}


@pytest.mark.asyncio
@pytest.mark.parametrize('config, senders, receivers', list(testcases.values()), ids=testcases.keys())
async def test_config_loading(config: str, senders, receivers):
    await test_run(config, senders, receivers)

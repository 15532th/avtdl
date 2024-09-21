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


async def run(config: str, senders: List[Sender], receivers: List[Receiver]):
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
    'test records passthrough through monitors and actors': (
        '''
actors:
  utils.producer:
    entities:
      - name: test1
      - name: test2
  utils.consumer:
    entities:
      - name: test3
        consume_record: false
      - name: test4
      - name: test5
chains:
  chain1:
    - utils.producer:
      - test1
    - utils.producer:
      - test2
    - utils.consumer:
      - test3
    - utils.consumer:
      - test4
    - utils.consumer:
      - test5
        ''',
        [Sender('utils.producer', 'test1', ['one', 'two', 'three'])],
        [
            Receiver('utils.consumer', 'test3', ['one', 'two', 'three']),
            Receiver('utils.consumer', 'test4', ['one', 'two', 'three']),
            Receiver('utils.consumer', 'test5', [])
        ]
    ),
    'test listing multiple entities of the same actor': (
        '''
actors:
  utils.producer:
    entities:
      - name: producer1
      - name: producer2
      - name: producer3
  utils.consumer:
    entities:
      - name: consumer_a
      - name: consumer_b
      - name: consumer_c
chains:
  chain_1abc:
    - utils.producer:
      - producer1
    - utils.consumer:
      - consumer_a
      - consumer_b
      - consumer_c
  chain_23b:
    - utils.producer:
      - producer2
      - producer3
    - utils.consumer:
      - consumer_b
  chain_3c:
    - utils.producer:
      - producer3
    - utils.consumer:
      - consumer_c
        ''',
        [
            Sender('utils.producer', 'producer1', ['record1']),
            Sender('utils.producer', 'producer2', ['record2']),
            Sender('utils.producer', 'producer3', ['record3']),
        ],
        [
            Receiver('utils.consumer', 'consumer_a', ['record1']),
            Receiver('utils.consumer', 'consumer_b', ['record1', 'record2', 'record3']),
            Receiver('utils.consumer', 'consumer_c', ['record1', 'record3']),
        ]
    ),
    'test second monitor entity leaking passthrough records': (
        '''
actors:
  utils.producer:
    entities:
      - name: producer1
      - name: producer2
  utils.consumer:
    entities:
      - name: consumer1
      - name: consumer2
chains:
  chain1:
    - utils.producer:
      - producer1
    - utils.producer:
      - producer2
    - utils.consumer:
      - consumer1
  chain2:
    - utils.producer:
      - producer2
    - utils.consumer:
      - consumer2
        ''',
        [
            Sender('utils.producer', 'producer1', ['record-1']),
            Sender('utils.producer', 'producer2', ['record-2']),
        ],
        [
            Receiver('utils.consumer', 'consumer1', ['record-1', 'record-2']),
            Receiver('utils.consumer', 'consumer2', ['record-1', 'record-2']),
        ]
    ),
    'test same filter entity in multiple chains': (
        '''
actors:
  utils.producer:
    entities:
      - name: producer1
      - name: producer2
      - name: producer3
  utils.consumer:
    entities:
      - name: consumer1
      - name: consumer2
      - name: consumer3
  filter.noop:
    entities:
      - name: noop1
chains:
  chain1:
    - utils.producer:
      - producer1
    - filter.noop:
      - noop1
    - utils.consumer:
      - consumer1
  chain2:
    - utils.producer:
      - producer2
    - filter.noop:
      - noop1
    - utils.consumer:
      - consumer2
  chain3a:
    - filter.noop:
      - noop1
    - utils.consumer:
      - consumer3
  chain3b:
    - utils.producer:
      - producer3
    - utils.consumer:
      - consumer3
        ''',
        [
            Sender('utils.producer', 'producer1', ['record1']),
            Sender('utils.producer', 'producer2', ['record2']),
            Sender('utils.producer', 'producer3', ['record3']),
        ],
        [
            Receiver('utils.consumer', 'consumer1', ['record1', 'record2']),
            Receiver('utils.consumer', 'consumer2', ['record1', 'record2']),
            Receiver('utils.consumer', 'consumer3', ['record1', 'record2', 'record3']),
        ]
    ),

}


@pytest.mark.asyncio
@pytest.mark.parametrize('config, senders, receivers', list(testcases.values()), ids=testcases.keys())
async def test_config_loading(config: str, senders, receivers):
    await run(config, senders, receivers)

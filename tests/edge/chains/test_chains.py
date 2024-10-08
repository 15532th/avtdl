from pathlib import Path
from typing import Dict, List, Tuple, Union

import pytest
import yaml
from pydantic import BaseModel

from avtdl.avtdl import parse_config
from avtdl.core.config import config_sancheck
from avtdl.core.interfaces import Actor, MessageBus, Record, TextRecord
from avtdl.core.loggers import set_logging_format, silence_library_loggers
from avtdl.plugins.utils.utils import Consumer, Producer


class Sender(BaseModel):
    actor: str = 'utils.producer'
    entity: str
    records: List[Union[str, Record]]

    def model_post_init(self, __context):
        updated_records = []
        for item in self.records:
            if isinstance(item, str):
                record = TextRecord(text=item)
            else:
                record = item
            updated_records.append(record)
        self.records = updated_records


class Receiver(BaseModel):
    actor: str = 'utils.consumer'
    entity: str
    expected_history: List[Union[str, Record]]

    def model_post_init(self, __context):
        updated_expected_history = []
        for item in self.expected_history:
            if isinstance(item, str):
                record = TextRecord(text=item)
            else:
                record = item
            updated_expected_history.append(record)
        self.expected_history = updated_expected_history


class _Testcases(BaseModel):
    senders: List[Sender]
    receivers: List[Receiver]

    @classmethod
    def load(cls, data: dict) -> Tuple[List[Sender], List[Receiver]]:
        testcases = cls.model_validate(data)
        return testcases.senders, testcases.receivers


async def run(config: str):
    silence_library_loggers()
    set_logging_format('WARNING')

    # class field value persists between isolated testcases runs, breaking tests
    # clean it before test as a temporary workaround
    MessageBus._subscriptions.clear()

    conf: dict = yaml.load(config, Loader=yaml.FullLoader)
    senders, receivers = _Testcases.load(conf.pop('testcases'))
    actors, chains = parse_config(conf)
    config_sancheck(actors, chains)

    send_records(actors, senders)
    check_received(actors, receivers)


def send_records(actors: Dict[str, Actor], senders: List[Sender]):
    for sender in senders:
        actor = actors[sender.actor]
        entity = actor.entities[sender.entity]
        if not isinstance(actor, Producer):
            raise Exception(f'incorrect sender: expected TestMonitor, got {actor.__class__}')
        for record in sender.records:
            assert isinstance(record, Record), f'post_init hook failed for {sender}'
            actor.produce(entity.name, record)


def check_received(actors: Dict[str, Actor], receivers: List[Receiver]):
    for receiver in receivers:
        actor = actors[receiver.actor]
        entity = actor.entities[receiver.entity]
        if not isinstance(actor, Consumer):
            raise Exception(f'incorrect receiver: expected TestAction, got {actor.__class__}')
        assert all(isinstance(record, Record) for record in receiver.expected_history), f'post_init hook failed for {receiver}'

        actual_history = actor.history[entity.name]
        assert actual_history == receiver.expected_history


def load_testcases() -> Dict[str, str]:
    testcases: Dict[str, str] = {}
    files = Path(__file__).parent.glob('*.yml')
    for file in files:
        with open(file, 'rt', encoding='utf8') as fp:
            testcases[file.name] = fp.read()
    return testcases


testcases = load_testcases()


@pytest.mark.asyncio
@pytest.mark.parametrize('extended_config', list(testcases.values()), ids=testcases.keys())
async def test_chains(extended_config: str):
    await run(extended_config)

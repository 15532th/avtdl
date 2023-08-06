import logging
from typing import List, Optional, Dict, OrderedDict, Callable

from pydantic import RootModel

from core.interfaces import Actor, Record, MessageBus

class ChainConfigSection(RootModel):
    root: OrderedDict[str, List[str]]

    def __iter__(self):
        return iter(self.root.items())


class Chain:
    def __init__(self, name: str, actors: ChainConfigSection):
        self.name = name
        self.bus = MessageBus()

        if len(actors.root) < 2:
            logging.warning(f'[chain {name}]: need at least two actors to create a chain')
            return

        producer_name, producer = actors.root.popitem(last=False)
        for consumer_name, consumer in actors:
            for producer_entity in producer:
                for consumer_entity in consumer:
                    producer_topic = self.bus.outgoing_topic_for(producer_name, producer_entity)
                    consumer_topic = self.bus.incoming_topic_for(consumer_name, consumer_entity)
                    handler = self.get_handler(consumer_topic)
                    self.bus.sub(producer_topic, handler)
            producer_name, producer = consumer_name, consumer

    def get_handler(self, topic) -> Callable[[str, Record], None]:
        def handle(producer_topic: str, record: Record):
            logging.debug(f'Chain {self.name}: forwarding record {record} from {producer_topic} to {topic}')
            self.bus.pub(topic, record)
        return handle

    def __repr__(self):
        return f'Chain("{self.name}", {self.monitors}, {self.filters!r}, {self.actions})'

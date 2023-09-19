import logging
from typing import List, OrderedDict, Callable

from pydantic import RootModel

from core.interfaces import Record, MessageBus


class ChainConfigSection(RootModel):

    root: List[OrderedDict[str, List[str]]]

    def __iter__(self):
        for item in self.root:
            yield item.copy().popitem()

    def __len__(self):
        return self.root.__len__()

    def __getitem__(self, item):
        value = self.root.__getitem__(item)
        if isinstance(value, list):
            return [x.popitem() for x in value]
        return value.popitem()

class Chain:
    def __init__(self, name: str, actors: ChainConfigSection):
        self.name = name
        self.bus = MessageBus()
        self.logger = logging.getLogger('chain')

        if len(actors) < 2:
            self.logger.warning(f'chain {name}: need at least two actors to create a chain')
            return

        producer_name, producer = actors[0]
        for consumer_name, consumer in actors[1:]:
            for producer_entity in producer:
                for consumer_entity in consumer:
                    producer_topic = self.bus.outgoing_topic_for(producer_name, producer_entity)
                    consumer_topic = self.bus.incoming_topic_for(consumer_name, consumer_entity)
                    handler = self.get_handler(consumer_topic)
                    self.bus.sub(producer_topic, handler)
            producer_name, producer = consumer_name, consumer

    def get_handler(self, topic) -> Callable[[str, Record], None]:
        class Handler:
            def __init__(this):
                this.logger = self.logger.getChild('handler')

            def __call__(this, producer_topic: str, record: Record):
                this.logger.debug(f'Chain({self.name}): from {producer_topic} to {topic} forwarding record "{record!r}"')
                self.bus.pub(topic, record)

            def __repr__(this):
                return f'Chain({self.name}).handler({topic})'

        return Handler()

    def __repr__(self):
        return f'Chain("{self.name}", {self.monitors}, {self.filters!r}, {self.actions})'

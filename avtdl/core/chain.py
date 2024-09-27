import logging
from collections import Counter, defaultdict
from typing import Callable, List, OrderedDict

from pydantic import RootModel

from avtdl.core.interfaces import MessageBus, Record


class ChainConfigSection(RootModel):

    root: List[OrderedDict[str, List[str]]]

    def __iter__(self):
        for item in self.root:
            try:
                yield item.copy().popitem()
            except KeyError:
                continue

    def __len__(self):
        return self.root.__len__()

    def __getitem__(self, item):
        value = self.root.__getitem__(item)
        if isinstance(value, list):
            return [x.copy().popitem() for x in value if x]
        return value.copy().popitem()

class Chain:

    def __init__(self, name: str, actors: ChainConfigSection):
        self.name = name
        self.bus = MessageBus()
        self.logger = logging.getLogger('chain')
        self.conf = actors

        if len(actors) < 2:
            self.logger.warning(f'chain {name}: need at least two actors to create a chain')
            return

        self.check_for_duplicated_entities(name, actors)

        producer_name, producer = actors[0]
        for consumer_name, consumer in actors[1:]:
            for producer_entity in producer:
                for consumer_entity in consumer:
                    producer_topic = self.bus.outgoing_topic_for(producer_name, producer_entity, self.name)
                    consumer_topic = self.bus.incoming_topic_for(consumer_name, consumer_entity, self.name)
                    handler = self.get_handler(consumer_topic)
                    self.bus.sub(producer_topic, handler)
            producer_name, producer = consumer_name, consumer

    def check_for_duplicated_entities(self, chain_name, actors: ChainConfigSection) -> None:
        flattened_actors = defaultdict(list)
        for name, entities in actors:
            flattened_actors[name].extend(entities)
        counted_actors = {}
        for name, entities in flattened_actors.items():
            counted_actors[name] = Counter(entities)
        for name, counter in counted_actors.items():
            for entity_name, times in counter.most_common():
                if times > 1:
                    msg = f'Chain {chain_name}: {name}: {entity_name} is used multiple times. It might lead to infinite recursion (WILL lead for filters), causing crash or triggering OOM killer. Remove duplicates from the chain or make each of them a separate entity with different name'
                    raise ValueError(msg)

    def get_handler(self, topic) -> Callable[[str, Record], None]:
        # noinspection PyMethodParameters
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
        actors_names = [name for name, _ in self.conf]
        return f'Chain("{self.name}", {actors_names})'

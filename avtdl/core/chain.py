import logging
from collections import Counter, defaultdict
from typing import Callable, List, OrderedDict

from pydantic import RootModel, field_validator

from avtdl.core.interfaces import Record, RuntimeContext


class CardSection(RootModel):
    """Single element of Chain"""
    root: OrderedDict[str, List[str]]

    def get_item(self) -> tuple[str, list[str]]:
        return self.root.copy().popitem()

    @field_validator('root')
    @classmethod
    def check_card(cls, root):
        if len(root) != 1:
            raise ValueError(f'card must list exactly 1 actor, got {len(root)}')
        actor_name = list(root.keys())[0]
        entities_names = root[actor_name]
        if len(entities_names) < 1:
            raise ValueError(f'{actor_name}: should list at least one entity name')
        return root


class ChainConfigSection(RootModel):

    root: List[CardSection]

    def __iter__(self):
        for card in self.root:
            try:
                yield card.get_item()
            except KeyError:
                continue

    def __len__(self):
        return self.root.__len__()

    def __getitem__(self, item):
        value = self.root.__getitem__(item)
        if isinstance(value, list):
            return [card.get_item() for card in value if card]
        return value.get_item()

    @field_validator('root')
    @classmethod
    def check_chains(cls, values):
        if len(values) < 2:
            raise ValueError('chain must contain at least 2 elements')
        return values


class Chain:

    def __init__(self, name: str, actors: ChainConfigSection, ctx: RuntimeContext):
        self.name = name
        self.bus = ctx.bus
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

    @staticmethod
    def check_for_duplicated_entities(chain_name, actors: ChainConfigSection) -> None:
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

from typing import List, Sequence, Optional

from core.interfaces import Filter, Record, FilterEntity, ActorConfig, Event
from core.config import Plugins

@Plugins.register('filter.noop', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('filter.match', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('filter.exclude', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('filter.event', Plugins.kind.ACTOR_CONFIG)
class EmptyFilterConfig(ActorConfig):
    pass
@Plugins.register('filter.noop', Plugins.kind.ACTOR_ENTITY)
class EmptyFilterEntity(FilterEntity):
    name: str

@Plugins.register('filter.noop', Plugins.kind.ACTOR)
class NoopFilter(Filter):

    def __init__(self, config: EmptyFilterConfig, entities: Sequence[EmptyFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: str, record: Record) -> Optional[Record]:
        return record

@Plugins.register('filter.match', Plugins.kind.ACTOR_ENTITY)
@Plugins.register('filter.exclude', Plugins.kind.ACTOR_ENTITY)
class MatchFilterEntity(FilterEntity):
    name: str
    patterns: List[str]

@Plugins.register('filter.match', Plugins.kind.ACTOR)
class MatchFilter(Filter):

    def __init__(self, config: EmptyFilterConfig, entities: Sequence[MatchFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: str, record: Record) -> Optional[Record]:
        for pattern in self.entities[entity].patterns:
            if str(record).find(pattern) > -1:
                return record
        return None

@Plugins.register('filter.exclude', Plugins.kind.ACTOR)
class ExcludeFilter(Filter):

    def __init__(self, config: EmptyFilterConfig, entities: Sequence[MatchFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: str, record: Record) -> Optional[Record]:
        for pattern in self.entities[entity].patterns:
            if str(record).find(pattern) > -1:
                return None
        return record

@Plugins.register('filter.event', Plugins.kind.ACTOR_ENTITY)
class EventFilterEntity(FilterEntity):
    filter_type: List[str] = ['any']

@Plugins.register('filter.event', Plugins.kind.ACTOR)
class EventFilter(Filter):

    def __init__(self, config: EmptyFilterConfig, entities: Sequence[EmptyFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: str, record: Record) -> Optional[Record]:
        if isinstance(record, Event):
            if record.filter_type == 'any':
                return record
            for filter_type in self.entities[entity].filter_type:
                if record.filter_type == filter_type:
                    return record
        else:
            return None


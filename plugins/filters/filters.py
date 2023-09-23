from typing import List, Sequence, Optional

from core.config import Plugins
from core.interfaces import Filter, Record, FilterEntity, ActorConfig, Event


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

    def match(self, entity: FilterEntity, record: Record) -> Optional[Record]:
        return record

@Plugins.register('filter.match', Plugins.kind.ACTOR_ENTITY)
@Plugins.register('filter.exclude', Plugins.kind.ACTOR_ENTITY)
class MatchFilterEntity(FilterEntity):
    patterns: List[str]

@Plugins.register('filter.match', Plugins.kind.ACTOR)
class MatchFilter(Filter):

    def __init__(self, config: EmptyFilterConfig, entities: Sequence[MatchFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: MatchFilterEntity, record: Record) -> Optional[Record]:
        for pattern in entity.patterns:
            if str(record).find(pattern) > -1:
                return record
        return None

@Plugins.register('filter.exclude', Plugins.kind.ACTOR)
class ExcludeFilter(Filter):

    def __init__(self, config: EmptyFilterConfig, entities: Sequence[MatchFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: MatchFilterEntity, record: Record) -> Optional[Record]:
        for pattern in entity.patterns:
            if str(record).find(pattern) > -1:
                return None
        return record

@Plugins.register('filter.event', Plugins.kind.ACTOR_ENTITY)
class EventFilterEntity(FilterEntity):
    event_types: Optional[List[str]] = None

@Plugins.register('filter.event', Plugins.kind.ACTOR)
class EventFilter(Filter):

    def __init__(self, config: EmptyFilterConfig, entities: Sequence[EmptyFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: EventFilterEntity, record: Record) -> Optional[Record]:
        if isinstance(record, Event):
            event_types = entity.event_types
            if event_types is None:
                return record
            for event_type in event_types:
                if record.event_type == event_type:
                    return record
        return None


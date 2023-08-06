from typing import List, Sequence

from core.interfaces import Filter, Record, FilterEntity, ActorConfig
from core.config import Plugins

@Plugins.register('filter.noop', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('filter.match', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('filter.exclude', Plugins.kind.ACTOR_CONFIG)
class FilterConfig(ActorConfig):
    pass
@Plugins.register('filter.noop', Plugins.kind.ACTOR_ENTITY)
class NoopFilterEntity(FilterEntity):
    name: str

@Plugins.register('filter.noop', Plugins.kind.ACTOR)
class NoopFilter(Filter):

    def __init__(self, config: FilterConfig, entities: Sequence[NoopFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: str, record: Record):
        return record

@Plugins.register('filter.match', Plugins.kind.ACTOR_ENTITY)
@Plugins.register('filter.exclude', Plugins.kind.ACTOR_ENTITY)
class MatchFilterEntity(FilterEntity):
    name: str
    patterns: List[str]

@Plugins.register('filter.match', Plugins.kind.ACTOR)
class MatchFilter(Filter):

    def __init__(self, config: FilterConfig, entities: Sequence[MatchFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: str, record: Record):
        for pattern in self.entities[entity].patterns:
            if str(record).find(pattern) > -1:
                return record
        return None

@Plugins.register('filter.exclude', Plugins.kind.ACTOR)
class ExcludeFilter(Filter):

    def __init__(self, config: FilterConfig, entities: Sequence[MatchFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: str, record: Record):
        for pattern in self.entities[entity].patterns:
            if str(record).find(pattern) > -1:
                return None
        return record

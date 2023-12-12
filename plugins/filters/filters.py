import json
import re
from collections import OrderedDict
from typing import List, Optional, Sequence

from pydantic import Field, field_validator

from core.config import Plugins
from core.interfaces import ActorConfig, Event, Filter, FilterEntity, Record, TextRecord
from core.utils import find_matching_field


@Plugins.register('filter.noop', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('filter.void', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('filter.match', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('filter.exclude', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('filter.event', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('filter.type', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('filter.json', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('filter.format', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('filter.deduplicate', Plugins.kind.ACTOR_CONFIG)
class EmptyFilterConfig(ActorConfig):
    pass

@Plugins.register('filter.noop', Plugins.kind.ACTOR_ENTITY)
@Plugins.register('filter.void', Plugins.kind.ACTOR_ENTITY)
class EmptyFilterEntity(FilterEntity):
    name: str

@Plugins.register('filter.noop', Plugins.kind.ACTOR)
class NoopFilter(Filter):

    def __init__(self, config: EmptyFilterConfig, entities: Sequence[EmptyFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: FilterEntity, record: Record) -> Record:
        return record


@Plugins.register('filter.void', Plugins.kind.ACTOR)
class VoidFilter(Filter):

    def __init__(self, config: EmptyFilterConfig, entities: Sequence[EmptyFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: FilterEntity, record: Record) -> None:
        return None


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
            field = find_matching_field(record, pattern)
            if field is not None:
                self.logger.debug(f'[{entity.name}] found pattern "{pattern}" in the field "{field}" of record "{record!r}", letting through')
                return record
        return None

@Plugins.register('filter.exclude', Plugins.kind.ACTOR)
class ExcludeFilter(Filter):

    def __init__(self, config: EmptyFilterConfig, entities: Sequence[MatchFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: MatchFilterEntity, record: Record) -> Optional[Record]:
        for pattern in entity.patterns:
            field = find_matching_field(record, pattern)
            if field is not None:
                self.logger.debug(f'[{entity.name}] found pattern "{pattern}" in the field "{field}" of record "{record!r}", dropping')
                return None
        return record

@Plugins.register('filter.event', Plugins.kind.ACTOR_ENTITY)
class EventFilterEntity(FilterEntity):
    event_types: Optional[List[str]] = None

@Plugins.register('filter.event', Plugins.kind.ACTOR)
class EventFilter(Filter):

    def __init__(self, config: EmptyFilterConfig, entities: Sequence[EventFilterEntity]):
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


@Plugins.register('filter.type', Plugins.kind.ACTOR_ENTITY)
class TypeFilterEntity(FilterEntity):
    types: List[str]
    exact_match: bool = False

@Plugins.register('filter.type', Plugins.kind.ACTOR)
class TypeFilter(Filter):

    def __init__(self, config: EmptyFilterConfig, entities: Sequence[TypeFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: TypeFilterEntity, record: Record) -> Optional[Record]:
        if entity.exact_match:
            tested_types = [record.__class__.__name__]
        else:
            tested_types = [t.__name__ for t in record.__class__.mro()]

        for tested_type in tested_types:
            for allowed_type in entity.types:
                if allowed_type == tested_type:
                    return record
        return None


@Plugins.register('filter.json', Plugins.kind.ACTOR_ENTITY)
class JsonFilterEntity(FilterEntity):
    prettify: bool = False

@Plugins.register('filter.json', Plugins.kind.ACTOR)
class JsonFilter(Filter):

    def __init__(self, config: EmptyFilterConfig, entities: Sequence[JsonFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: JsonFilterEntity, record: Record) -> TextRecord:
        indent = 4 if entity.prettify else None
        try:
            as_object = json.loads(str(record))
            self.logger.debug(f'text representation of record "{record!r}" is already a valid json')
            as_json = json.dumps(as_object, sort_keys=True, ensure_ascii=False, default=str, indent=indent)
        except json.JSONDecodeError:
            as_json = record.as_json(indent=indent)

        return TextRecord(text=as_json)


@Plugins.register('filter.format', Plugins.kind.ACTOR_ENTITY)
class FormatFilterEntity(FilterEntity):
    fmt: str
    missing: str = ''

    @field_validator('fmt')
    @classmethod
    def valid_fmt(cls, fmt: str) -> str:
       return fmt

@Plugins.register('filter.format', Plugins.kind.ACTOR)
class FormatFilter(Filter):

    def __init__(self, config: EmptyFilterConfig, entities: Sequence[FormatFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: FormatFilterEntity, record: Record) -> TextRecord:
        placeholders = re.findall('({[^{}]+})', entity.fmt)
        text = entity.fmt
        for placeholder in placeholders:
           text = text.replace(placeholder, getattr(record, placeholder[1:-1], entity.missing))
        return TextRecord(text=text)


@Plugins.register('filter.deduplicate', Plugins.kind.ACTOR_ENTITY)
class DeduplicateFilterEntity(FilterEntity):
    field: str = 'hash'
    history_size: int = 10000
    history: OrderedDict = Field(exclude=True, default=OrderedDict())


@Plugins.register('filter.deduplicate', Plugins.kind.ACTOR)
class DeduplicateFilter(Filter):

    def __init__(self, config: EmptyFilterConfig, entities: Sequence[DeduplicateFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: DeduplicateFilterEntity, record: Record) -> Optional[Record]:
        field = getattr(record, entity.field, None)
        if field is None:
            self.logger.debug(f'[{entity.name}] record has no field {entity.field}, letting it through')
            return record
        if callable(field):
            try:
                value = field()
            except TypeError:
                self.logger.debug(f'[{entity.name}] unsupported "field" value {entity.field}. Should be a property or a method that takes no arguments. All records will be dropped on this filter')
                return None
        else:
            value = field

        value = str(value) # support non-hashable fields

        if value in entity.history:
            self.logger.debug(f'[{entity.name}] record with {entity.field}={value} has already been seen, dropping')
            return None

        while len(entity.history) >= entity.history_size:
            entity.history.popitem(last=False)

        entity.history[value] = True
        entity.history.move_to_end(value)
        return record

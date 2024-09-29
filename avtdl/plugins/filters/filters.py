import datetime
import json
from collections import OrderedDict
from typing import List, Optional, Sequence

import dateutil.tz
from pydantic import Field, field_validator

from avtdl.core.config import Plugins
from avtdl.core.interfaces import ActorConfig, Event, Filter, FilterEntity, Record, TextRecord
from avtdl.core.utils import Fmt, find_matching_field


@Plugins.register('filter.noop', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('filter.void', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('filter.match', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('filter.exclude', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('filter.event', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('filter.event.cause', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('filter.type', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('filter.json', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('filter.format', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('filter.format.event', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('filter.deduplicate', Plugins.kind.ACTOR_CONFIG)
class EmptyFilterConfig(ActorConfig):
    pass

@Plugins.register('filter.noop', Plugins.kind.ACTOR_ENTITY)
@Plugins.register('filter.void', Plugins.kind.ACTOR_ENTITY)
@Plugins.register('filter.event.cause', Plugins.kind.ACTOR_ENTITY)
class EmptyFilterEntity(FilterEntity):
    pass

@Plugins.register('filter.noop', Plugins.kind.ACTOR)
class NoopFilter(Filter):
    """
    Pass everything through

    Lets all incoming records pass through unchanged, effectively
    doing nothing with them. As any other filter it has entities,
    so it can be used as a merging point to gather records from
    multiple chains and process them in a single place.
    """

    def __init__(self, config: EmptyFilterConfig, entities: Sequence[EmptyFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: FilterEntity, record: Record) -> Record:
        return record


@Plugins.register('filter.void', Plugins.kind.ACTOR)
class VoidFilter(Filter):
    """
    Drop everything

    Does not produce anything, dropping all incoming records.
    Can be used to stuff multiple chains in one if the need ever arises.
    """

    def __init__(self, config: EmptyFilterConfig, entities: Sequence[EmptyFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: FilterEntity, record: Record) -> None:
        return None


@Plugins.register('filter.match', Plugins.kind.ACTOR_ENTITY)
@Plugins.register('filter.exclude', Plugins.kind.ACTOR_ENTITY)
class MatchFilterEntity(FilterEntity):
    patterns: List[str]
    """list of strings to search for in the record"""
    fields: Optional[List[str]] = None
    """field names to search the patterns in. If not specified, all fields are checked"""

@Plugins.register('filter.match', Plugins.kind.ACTOR)
class MatchFilter(Filter):
    """
    Keep records with specific words

    This filter lets through records that have one of the values
    defined by `patterns` list found in any (or specified) field of the record.
    """

    def __init__(self, config: EmptyFilterConfig, entities: Sequence[MatchFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: MatchFilterEntity, record: Record) -> Optional[Record]:
        for pattern in entity.patterns:
            field = find_matching_field(record, pattern, entity.fields)
            if field is not None:
                self.logger.debug(f'[{entity.name}] found pattern "{pattern}" in the field "{field}" of record "{record!r}", letting through')
                return record
        return None

@Plugins.register('filter.exclude', Plugins.kind.ACTOR)
class ExcludeFilter(Filter):
    """
    Drop records with specific words

    This filter lets through records that have none of the values
    defined by `patterns` list found in any (or specified) field of the record.
    """

    def __init__(self, config: EmptyFilterConfig, entities: Sequence[MatchFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: MatchFilterEntity, record: Record) -> Optional[Record]:
        for pattern in entity.patterns:
            field = find_matching_field(record, pattern, entity.fields)
            if field is not None:
                self.logger.debug(f'[{entity.name}] found pattern "{pattern}" in the field "{field}" of record "{record!r}", dropping')
                return None
        return record


Plugins.register('filter.event', Plugins.kind.ASSOCIATED_RECORD)(Event)

@Plugins.register('filter.event', Plugins.kind.ACTOR_ENTITY)
class EventFilterEntity(FilterEntity):
    event_types: Optional[List[str]] = None
    """list of event types. See descriptions of plugins producing events for possible values"""

@Plugins.register('filter.event', Plugins.kind.ACTOR)
class EventFilter(Filter):
    """
    Filter for records with "Event" type

    Only lets through Events and not normal Records. Can be used to
    set up notifications on events (such as errors) from, for example,
    `execute` plugin within the same chain that uses it, by separating
    them from regular records.
    """

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


@Plugins.register('filter.event.cause', Plugins.kind.ACTOR)
class EventCauseFilter(Filter):
    """
    Filter for extracting original record from Event

    Take an Event and return the record that was being processed
    when it happened. For example, Event sent by `to_file`
    plugin failing to write a TextRecord in a file will produce
    the original TextRecord.

    Regular records (not Events) are passed through unchanged.
    """

    def __init__(self, config: EmptyFilterConfig, entities: Sequence[EmptyFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: EmptyFilterEntity, record: Record) -> Optional[Record]:
        if isinstance(record, Event):
            return record.record
        return record


@Plugins.register('filter.type', Plugins.kind.ACTOR_ENTITY)
class TypeFilterEntity(FilterEntity):
    types: List[str]
    """list of records class names, such as "Record" and "Event" """
    exact_match: bool = False
    """whether match should check for exact record type or look in entire records hierarchy up to Record"""

@Plugins.register('filter.type', Plugins.kind.ACTOR)
class TypeFilter(Filter):
    """
    Filter for records of specific type

    Only lets through records of specified types, such as `Event` or `YoutubeVideoRecord`.
    """

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



Plugins.register('filter.json', Plugins.kind.ASSOCIATED_RECORD)(TextRecord)

@Plugins.register('filter.json', Plugins.kind.ACTOR_ENTITY)
class JsonFilterEntity(FilterEntity):
    prettify: bool = False
    """whether output should be multiline and indented or a single line"""

@Plugins.register('filter.json', Plugins.kind.ACTOR)
class JsonFilter(Filter):
    """
    Format record as JSON

    Takes record and produces a new `TextRecord` rendering fields of the
    original record in JSON format, with option for pretty-print.
    """

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


Plugins.register('filter.format', Plugins.kind.ASSOCIATED_RECORD)(TextRecord)


@Plugins.register('filter.format', Plugins.kind.ACTOR_ENTITY)
class FormatFilterEntity(FilterEntity):
    template: str
    """template string with placeholders that will be filled with corresponding values from current record"""
    missing: Optional[str] = None
    """if specified, will be used to fill template placeholders that do not have corresponding fields in current record"""
    timezone: Optional[str] = None
    """takes timezone name from <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones> (or local time if omitted), converts record fields containing date and time to this timezone"""

    @field_validator('timezone')
    @classmethod
    def check_timezone(cls, timezone: Optional[str]) -> Optional[datetime.timezone]:
        if timezone is None:
            return None
        tz = dateutil.tz.gettz(timezone)
        if tz is None:
            raise ValueError(f'Unknown timezone: {timezone}')
        return tz


@Plugins.register('filter.format', Plugins.kind.ACTOR)
class FormatFilter(Filter):
    """
    Format record as text

    Takes a record and produces a new `TextRecord` by taking `template` string
    and replacing "{placeholder}" with value of `placeholder` field of the
    current record, where `placeholder` is any field the record might have.
    If one of the placeholders is not a field of a specific record, it will be
    replaced with a value defined in `missing` parameter if it is specified,
    otherwise it will be left intact.

    Because output record is essentially a text, timezone offset will be lost
    for all fields containing date and time value. Therefore, the `timezone`
    parameter is provided to allow formatting these fields in desired timezone.
    """

    def __init__(self, config: EmptyFilterConfig, entities: Sequence[FormatFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: FormatFilterEntity, record: Record) -> TextRecord:
        record = record.as_timezone(entity.timezone)
        text = Fmt.format(entity.template, record, entity.missing, tz=entity.timezone)
        return TextRecord(text=text)


Plugins.register('filter.format.event', Plugins.kind.ASSOCIATED_RECORD)(Event)


@Plugins.register('filter.format.event', Plugins.kind.ACTOR_ENTITY)
class FormatEventFilterEntity(FilterEntity):
    type_template: str
    """template string with placeholders that will be filled with corresponding values from current record"""
    text_template: str
    """template string with placeholders that will be filled with corresponding values from current record"""
    missing: Optional[str] = None
    """if specified, will be used to fill template placeholders that do not have corresponding fields in current record"""
    timezone: Optional[str] = None
    """takes timezone name from <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones> (or local time if omitted), converts record fields containing date and time to this timezone"""

    @field_validator('timezone')
    @classmethod
    def check_timezone(cls, timezone: Optional[str]) -> Optional[datetime.timezone]:
        if timezone is None:
            return None
        tz = dateutil.tz.gettz(timezone)
        if tz is None:
            raise ValueError(f'Unknown timezone: {timezone}')
        return tz


@Plugins.register('filter.format.event', Plugins.kind.ACTOR)
class FormatEventFilter(Filter):
    """
    Generate Event from record text

    Takes a record and produces a new `Event` with the `event_type` and
    `text` values evaluated by filling placeholders in `type_template`
    and `text_template`.

    Just like with the `filter.format`, missing placeholders are filled with
    value from the `missing` parameter if it is specified, otherwise they are
    left unchanged.

    Specifying timezone offset for the output text is also
    supported, though original record might be retrieved with `filter.event.cause`.
    """

    def __init__(self, config: EmptyFilterConfig, entities: Sequence[FormatEventFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: FormatEventFilterEntity, record: Record) -> Event:
        record = record.as_timezone(entity.timezone)
        event_type = Fmt.format(entity.type_template, record, entity.missing, tz=entity.timezone)
        text = Fmt.format(entity.text_template, record, entity.missing, tz=entity.timezone)
        return Event(event_type=event_type, text=text, record=record)


@Plugins.register('filter.deduplicate', Plugins.kind.ACTOR_ENTITY)
class DeduplicateFilterEntity(FilterEntity):
    field: str = 'hash'
    """field name to use for comparison"""
    history_size: int = 10000
    """how many old records should be kept in memory"""
    history: OrderedDict = Field(exclude=True, repr=False, default=OrderedDict())
    """internal variable to persist state between updates. Used to keep fields of already seen records"""


@Plugins.register('filter.deduplicate', Plugins.kind.ACTOR)
class DeduplicateFilter(Filter):
    """
    Drop already seen records

    Checks if the `field` field value of the current record has already been
    present in one of the previous records and only let it through otherwise.

    `field` might be either a record field name or one of `hash` or `as_json`
    for sha1 and fulltext comparison. If `field` is not present in the current
    record, it will be passed through as if it's new.

    This filter will work with records of any type, as long as they have defined
    field (all records have `hash` and `as_json`). For example, it is possible
    to ensure no multiple records for a single video will be produced
    in a chain, that gather records from Youtube channel and Youtube RSS monitors,
    by passing them to an entity of this filter with `field` set to `video_id`.

    Note, that history is kept in memory, so it will not be persisted between
    restarts.
    """

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
        self.logger.debug(f'[{entity.name}] record with {entity.field}={value} has not yet been seen, letting through')
        return record

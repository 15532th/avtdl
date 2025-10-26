import datetime
from typing import Dict, Optional, Sequence

from pydantic import field_serializer, field_validator

from avtdl.core.actors import Filter, FilterEntity
from avtdl.core.formatters import Fmt
from avtdl.core.interfaces import Event, OpaqueRecord, Record, TextRecord
from avtdl.core.plugins import Plugins
from avtdl.core.runtime import RuntimeContext
from avtdl.core.utils import Timezone
from avtdl.plugins.filters.filters import EmptyFilterConfig

Plugins.register('filter.format', Plugins.kind.ASSOCIATED_RECORD)(TextRecord)
Plugins.register('filter.format', Plugins.kind.ACTOR_CONFIG)(EmptyFilterConfig)


class BaseFormatFilterEntity(FilterEntity):
    missing: Optional[str] = None
    """if specified, will be used to fill template placeholders that do not have corresponding fields in current record"""
    timezone: Optional[datetime.tzinfo] = None
    """takes timezone name from <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones> (or local time if omitted), converts record fields containing date and time to this timezone"""

    @field_validator('timezone', mode='plain')
    @classmethod
    def check_timezone(cls, timezone: Optional[str]) -> Optional[datetime.tzinfo]:
        if timezone is None:
            return None
        if not isinstance(timezone, str):
            raise ValueError('Input should be a valid string')
        return Timezone.get_tz(timezone)

    @field_serializer('timezone')
    @classmethod
    def serialize_timezone(cls, timezone: Optional[datetime.tzinfo]) -> Optional[str]:
        return Timezone.get_name(timezone)


@Plugins.register('filter.format', Plugins.kind.ACTOR_ENTITY)
class FormatFilterEntity(BaseFormatFilterEntity):
    template: str
    """template string with placeholders that will be filled with corresponding values from current record"""


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

    def __init__(self, config: EmptyFilterConfig, entities: Sequence[FormatFilterEntity], ctx: RuntimeContext):
        super().__init__(config, entities, ctx)

    def match(self, entity: FormatFilterEntity, record: Record) -> TextRecord:
        record = record.as_timezone(entity.timezone)
        text = Fmt.format(entity.template, record, entity.missing, tz=entity.timezone)
        return TextRecord(text=text)


Plugins.register('filter.format.event', Plugins.kind.ASSOCIATED_RECORD)(Event)
Plugins.register('filter.format.event', Plugins.kind.ACTOR_CONFIG)(EmptyFilterConfig)


@Plugins.register('filter.format.event', Plugins.kind.ACTOR_ENTITY)
class FormatEventFilterEntity(BaseFormatFilterEntity):
    type_template: str
    """template string with placeholders used to format "event_type" field of the produced event with corresponding values from current record"""
    text_template: str
    """template string with placeholders used to format "text" field of the produced event with corresponding values from current record"""


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

    def __init__(self, config: EmptyFilterConfig, entities: Sequence[FormatEventFilterEntity], ctx: RuntimeContext):
        super().__init__(config, entities, ctx)

    def match(self, entity: FormatEventFilterEntity, record: Record) -> Event:
        record = record.as_timezone(entity.timezone)
        event_type = Fmt.format(entity.type_template, record, entity.missing, tz=entity.timezone)
        text = Fmt.format(entity.text_template, record, entity.missing, tz=entity.timezone)
        return Event(event_type=event_type, text=text, record=record)


Plugins.register('filter.format.opaque', Plugins.kind.ASSOCIATED_RECORD)(OpaqueRecord)
Plugins.register('filter.format.opaque', Plugins.kind.ACTOR_CONFIG)(EmptyFilterConfig)


@Plugins.register('filter.format.opaque', Plugins.kind.ACTOR_ENTITY)
class FormatOpaqueFilterEntity(BaseFormatFilterEntity):
    fields_templates: Dict[str, str]
    """mapping of fields names and corresponding template string with placeholders"""


@Plugins.register('filter.format.opaque', Plugins.kind.ACTOR)
class FormatOpaqueFilter(Filter):
    """
    Generate record with custom fields

    Takes a record and produces a new `OpaqueRecord` with fields names
    and values defined in the `fields_templates` mapping
    """

    def __init__(self, config: EmptyFilterConfig, entities: Sequence[FormatOpaqueFilterEntity], ctx: RuntimeContext):
        super().__init__(config, entities, ctx)

    def match(self, entity: FormatOpaqueFilterEntity, record: Record) -> OpaqueRecord:
        record = record.as_timezone(entity.timezone)
        fields = {}
        for name, fmt in entity.fields_templates.items():
            fields[name] = Fmt.format(fmt, record, entity.missing, entity.timezone)
        return OpaqueRecord(**fields)

import asyncio
import datetime
from typing import Sequence, Set, Union

from pydantic import field_validator

from avtdl.core.interfaces import Filter, FilterEntity, Record
from avtdl.core.plugins import Plugins
from avtdl.core.utils import monitor_tasks_set
from avtdl.plugins.filters.filters import EmptyFilterConfig
from avtdl.plugins.youtube.youtube_feed import YoutubeVideoRecord
from avtdl.plugins.youtube.youtube_rss import YoutubeFeedRecord

Plugins.register('filter.channel.notify', Plugins.kind.ASSOCIATED_RECORD)(YoutubeVideoRecord)
Plugins.register('filter.channel.notify', Plugins.kind.ASSOCIATED_RECORD)(YoutubeFeedRecord)


@Plugins.register('filter.channel.notify', Plugins.kind.ACTOR_CONFIG)
class ChannelNotifyFilterConfig(EmptyFilterConfig):
    pass


@Plugins.register('filter.channel.notify', Plugins.kind.ACTOR_ENTITY)
class ChannelNotifyFilterEntity(FilterEntity):
    prior: Union[int, datetime.timedelta] = 10
    """output a record this many minutes before the scheduled start of a live broadcast"""
    include_ongoing: bool = False
    """whether currently live streams should be included"""

    @field_validator('prior')
    @classmethod
    def to_timedelta(cls, minutes: Union[int, datetime.timedelta]) -> datetime.timedelta:
        if isinstance(minutes, datetime.timedelta):
            return minutes
        return datetime.timedelta(minutes=minutes)


@Plugins.register('filter.channel.notify', Plugins.kind.ACTOR)
class ChannelNotifyFilter(Filter):
    """
    Hold upcoming stream's records until the start time

    Determines whether a record represents a Youtube livestream/Premiere
    with a scheduled time, and holds it waiting until the time comes
    instead of passing down the chain immediately if needed.

    If the record is not an upcoming Youtube livestream, it gets silently dropped.
    """

    def __init__(self, config: ChannelNotifyFilterConfig, entities: Sequence[ChannelNotifyFilterEntity]):
        self.tasks: Set[asyncio.Task] = set()
        super().__init__(config, entities)

    def match(self, entity: ChannelNotifyFilterEntity, record: Record) -> None:
        if isinstance(record, YoutubeVideoRecord):
            scheduled = record.scheduled
            if entity.include_ongoing and record.is_live:
                scheduled = datetime.datetime.now(datetime.timezone.utc)
        else:
            scheduled = getattr(record, 'scheduled', ...)
        if scheduled is ...:
            self.logger.debug(f'[{entity.name}] record has no "scheduled" field, dropping: {record!r}')
            return
        if scheduled is None:
            self.logger.debug(f'[{entity.name}] "scheduled" field of the record is empty, dropping: {record!r}')
            return
        if not isinstance(scheduled, datetime.datetime):
            self.logger.debug(f'[{entity.name}] "scheduled" field of the record has unexpected value, dropping: {record!r}')
            return

        at = scheduled - entity.prior
        task = asyncio.create_task(self.notify(at, entity, record))
        self.tasks.add(task)

    async def notify(self, at: datetime.datetime, entity: ChannelNotifyFilterEntity, record: Record):
        now = datetime.datetime.now(datetime.timezone.utc)
        delay = (at - now).total_seconds()
        if delay > 0:
            self.logger.debug(f'[{entity.name}] waiting {at - now} before emitting {record!r}')
            await asyncio.sleep(delay)
        else:
            self.logger.debug(f'[{entity.name}] deadline was {now - at} ago, emitting record immediately: {record!r}')
        self.on_record(entity, record)

    async def run(self):
        await monitor_tasks_set(self.tasks, logger=self.logger)

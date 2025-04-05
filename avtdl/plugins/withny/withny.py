import dataclasses
import datetime
from enum import Enum
from typing import Callable, Optional, Sequence, Tuple

from pydantic import Field, PositiveFloat, PositiveInt

from avtdl.core.interfaces import Record
from avtdl.core.monitors import BaseFeedMonitor, BaseFeedMonitorConfig, BaseFeedMonitorEntity
from avtdl.core.plugins import Plugins
from avtdl.core.request import HttpClient
from avtdl.plugins.withny.extractors import WithnyRecord, format_timestamp, parse_live_record, parse_schedule_record, \
    utcnow_with_offset


@Plugins.register('withny', Plugins.kind.ACTOR_CONFIG)
class WithnyMonitorConfig(BaseFeedMonitorConfig):
    pass


class WithnyMonitorMode(str, Enum):
    STREAMS = 'streams'
    SCHEDULES = 'schedules'


@Plugins.register('withny', Plugins.kind.ACTOR_ENTITY)
class WithnyMonitorEntity(BaseFeedMonitorEntity):
    update_interval: PositiveFloat = 300
    """how often the monitored channel will be checked, in seconds"""
    update_ratio: PositiveInt = 12
    """ratio of live to scheduled streams updates"""
    url: str = Field(exclude=True, default='')
    """url is not used, all streams are checked through the same api endpoint"""
    since: Optional[datetime.datetime] = Field(exclude=True, default=None)
    """internal variable, used to store "since" parameter of the last successful update"""
    current_update_ratio: PositiveInt = Field(exclude=True, default=1)
    """internal variable, used to store number of updates of livestreams endpoint since last update of schedules"""


@dataclasses.dataclass
class Context:
    count: int
    page: int
    take: int = 20


@Plugins.register('withny', Plugins.kind.ACTOR)
class WithnyMonitor(BaseFeedMonitor):
    """
    Monitor livestreams on Withny

    Livestreams are checked every update, scheduled streams once every `update_ratio` times.
    Checks all channels at once, so it's recommended to only use a single entity and select
    channels for further processing by using "filter.match" or other filters.
    Does not retrieve playlist_url and therefore does not require authentication.
    """

    async def get_records(self, entity: WithnyMonitorEntity, client: HttpClient) -> Sequence[WithnyRecord]:
        records = await self._get_streams(entity, client)
        if entity.current_update_ratio < entity.update_ratio:
            entity.current_update_ratio += 1
        else:
            entity.current_update_ratio = 1
            upcoming_records, _ = await self._get_schedules(entity, client)
            # deduplicate records, giving the upcoming ones priority
            records = list({record.stream_id: record for record in [*records, *upcoming_records]}.values())
        return records

    async def _parse_data(self, data, parser: Callable) -> Sequence[WithnyRecord]:
        records = []
        for item in data:
            try:
                record = parser(item)
                records.append(record)
            except Exception as e:
                self.logger.exception(f'failed to parse stream record: {e}')
                self.logger.debug(f'raw record data: {item}')
        return records

    async def _get_streams(self, entity: WithnyMonitorEntity, client: HttpClient) -> Sequence[WithnyRecord]:
        url = 'https://www.withny.fun/api/streams/with-rooms'
        data = await self.request_json(url, entity, client)
        if data is None:
            return []
        if not isinstance(data, list):
            self.logger.warning(f'unexpected data from /streams/ api: stream list is not a list')
            self.logger.debug(f'raw /streams/ api response: {data}')
            return []
        records = await self._parse_data(data, parse_live_record)
        return records

    async def _get_schedules(self,
                             entity: WithnyMonitorEntity,
                             client: HttpClient,
                             context: Optional[Context] = None) -> Tuple[Sequence[WithnyRecord], Context]:
        url = 'https://www.withny.fun/api/schedules'
        context = context or Context(count=0, page=1)

        if entity.since is None:
            entity.since = utcnow_with_offset(hours=-3)
        params = {
            'page': context.page,
            'take': context.take,
            'isFavorite': 'false',
            'excludeClosedStream': 'false',
            'later': format_timestamp(entity.since)
        }
        data = await self.request_json(url, entity, client, params=params)
        if data is None or not isinstance(data, dict):
            return [], context

        for name, _type in [('schedules', list), ('count', int)]:
            if name not in data or not isinstance(data[name], _type):
                self.logger.warning(f'unexpected data from /schedules/ api')
                self.logger.debug(f'raw /schedules/ api response: {data}')
                return [], context

        schedules_data = data['schedules']
        records = await self._parse_data(schedules_data, parse_schedule_record)

        context.count = data['count']
        self.logger.debug(f'[{entity.name}] schedules: {len(records)}/{context.count} records on page {context.page}')
        if context.page * context.take > context.count:  # this page is the last one
            return records, context
        if context.page < 2 and not all([self.record_is_new(record, entity) for record in records]):
            self.logger.debug(f'found already seen records on page {context.page}, aborting early')
            return records, context

        context.page += 1
        next_page_records, _ = await self._get_schedules(entity, client, context)
        records = [*records, *next_page_records]

        return records, context

    async def get_new_records(self, entity: WithnyMonitorEntity, client: HttpClient) -> Sequence[Record]:
        new_records = await super().get_new_records(entity, client)
        if new_records:
            # updating "since" invalidates caching headers, so it's only done when response content has changed anyway
            entity.since = utcnow_with_offset(hours=-3)
            self.logger.debug(f'[{entity.name}] got {len(new_records)} new records, "since" set to {entity.since}')
        return new_records

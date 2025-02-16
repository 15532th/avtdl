import dataclasses
import datetime
from enum import Enum
from typing import Callable, Optional, Sequence, Tuple

import aiohttp
from pydantic import Field, PositiveFloat, PositiveInt

from avtdl.core import utils
from avtdl.core.monitors import BaseFeedMonitor, BaseFeedMonitorConfig, BaseFeedMonitorEntity
from avtdl.core.plugins import Plugins
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
    take: int = 40


@Plugins.register('withny', Plugins.kind.ACTOR)
class WithnyMonitor(BaseFeedMonitor):
    """
    Monitor livestreams on Withny
    """

    async def get_records(self, entity: WithnyMonitorEntity, session: aiohttp.ClientSession) -> Sequence[WithnyRecord]:
        records = await self._get_streams(entity, session)
        if entity.current_update_ratio < entity.update_ratio:
            entity.current_update_ratio += 1
        else:
            entity.current_update_ratio = 1
            upcoming_records, _ = await self._get_schedules(entity, session)
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

    async def _get_streams(self, entity: WithnyMonitorEntity, session: aiohttp.ClientSession) -> Sequence[WithnyRecord]:
        url = 'https://www.withny.fun/api/streams/with-rooms'
        data = await self.request_json(url, entity, session)
        if data is None:
            return []
        if not isinstance(data, list):
            self.logger.warning(f'unexpected data from /streams/ api: stream list is not a list')
            self.logger.debug(f'raw /streams/ api response: {data}')
            return []
        records = await self._parse_data(data, parse_live_record)
        return records

    async def _get_schedules(self, entity: WithnyMonitorEntity, session: aiohttp.ClientSession,
                             context: Optional[Context] = None) -> Tuple[Sequence[
        WithnyRecord], Context]:
        url = 'https://www.withny.fun/api/schedules'
        context = context or Context(count=0, page=1)

        since = entity.since or utcnow_with_offset(hours=-3)
        params = {
            'page': context.page,
            'take': context.take,
            'isFavorite': 'false',
            'excludeClosedStream': 'true',
            'later': format_timestamp(since)
        }
        if context.page == 1:
            # utilize 304 handling capabilities and update_interval adjustment
            data = await self.request_json(url, entity, session, params=params)
        else:
            data = await utils.request_json(url, session=session, logger=self.logger, params=params, retry_times=3)
        if data is None:
            return [], context

        entity.since = since

        for name, _type in [('schedules', list), ('count', int)]:
            if not name in data or not isinstance(data[name], _type):
                self.logger.warning(f'unexpected data from /schedules/ api')
                self.logger.debug(f'raw /schedules/ api response: {data}')
                return [], context

        schedules_data = data['schedules']
        records = await self._parse_data(schedules_data, parse_schedule_record)

        context.count = data['count']
        self.logger.debug(f'[{entity.name}] schedules: {len(records)}/{context.count} records on page {context.page}')
        if context.page * context.take > context.count:  # this page is the last one
            return records, context

        context.page += 1
        next_page_records, _ = await self._get_schedules(entity, session, context)
        records = [*records, *next_page_records]
        return records, context

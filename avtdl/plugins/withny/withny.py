import dataclasses
import datetime
from enum import Enum
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

import aiohttp
import dateutil.parser
from pydantic import Field, PositiveFloat, PositiveInt

from avtdl.core import utils
from avtdl.core.interfaces import Record
from avtdl.core.monitors import BaseFeedMonitor, BaseFeedMonitorConfig, BaseFeedMonitorEntity
from avtdl.core.plugins import Plugins


@Plugins.register('withny', Plugins.kind.ASSOCIATED_RECORD)
class WithnyRecord(Record):
    """
    Ongoing or upcoming livestream on Withny
    """
    url: str
    """channel url"""
    stream_id: str
    """unique id of the stream"""
    title: str
    """stream title"""
    description: str
    """stream description"""
    thumbnail_url: Optional[str]
    """url of the stream thumbnail"""
    start: Optional[datetime.datetime]
    """time of the stream start, if started"""
    end: Optional[datetime.datetime] = None
    """time of the stream end, if ended"""
    scheduled: Optional[datetime.datetime] = None
    """scheduled date for upcoming stream"""
    user_id: str
    """unique id of the user hosting this stream"""
    cast_id: str
    """another unique id of the stream host"""
    username: str
    """channel name"""
    name: str
    """visible name of the user"""
    avatar_url: str
    """link to the user's avatar"""

    is_live: bool
    """indicates that livestream is currently live"""
    price: int
    """access price, in points"""

    def __str__(self):
        last_line = ''
        scheduled = self.scheduled
        if scheduled:
            last_line = '\nscheduled to {}'.format(scheduled.strftime('%Y-%m-%d %H:%M'))
        elif self.is_live:
            last_line = '\n[Live]'
        if self.price > 0:
            last_line += f' [{self.price}pt]'
        template = '{}\n{}\npublished by {}'
        return template.format(self.url, self.title, self.name) + last_line

    def __repr__(self):
        template = '{:<8} [{}] {}'
        return template.format(self.name, self.scheduled or self.start, self.title[:60])

    def get_uid(self) -> str:
        return f'{self.stream_id}'

    def discord_embed(self) -> dict:
        embed: Dict[str, Any] = {
            'title': self.title,
            # 'description': self.description,
            'url': self.url,
            'color': None,
            'author': {'name': self.name, 'url': self.url, 'icon_url': self.avatar_url},
            'image': {'url': self.thumbnail_url},
            'fields': []
        }
        footer = ''
        if self.scheduled is not None:
            scheduled = self.scheduled.strftime('%Y-%m-%d %H:%M')
            embed['fields'].append({'name': 'Scheduled:', 'value': scheduled, 'inline': True})
        if self.is_live:
            embed['fields'].append({'name': '[Live]', 'value': '', 'inline': True})
        if self.price > 0:
            embed['fields'].append({'name': f'[{self.price}pt]', 'value': '', 'inline': True})
        embed['footer'] = {'text': footer}
        return embed


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
            except ValueError as e:
                self.logger.warning(f'failed to parse stream record: {e}')
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


@dataclasses.dataclass
class Cast:
    username: str
    name: str
    avatar_url: str
    user_id: str
    cast_id: str

    @classmethod
    def from_data(cls, data: dict) -> 'Cast':
        try:
            info = data['agencySecret']
            return cls(
                username=info['username'],
                name=info['name'],
                avatar_url=data['profileImageUrl'],
                user_id=data['uuid'],
                cast_id=info['uuid']
            )
        except (TypeError, KeyError, ValueError) as e:
            raise ValueError(f'failed to parse cast info: {type(e)} {e}') from e


def parse_live_record(data: dict, cast: Optional[Cast] = None) -> WithnyRecord:
    cast = cast or Cast.from_data(data['cast'])
    started_at = dateutil.parser.parse(data['startedAt']) if data['startedAt'] else None
    ended_at = dateutil.parser.parse(data['closedAt']) if data['closedAt'] else None
    return WithnyRecord(
        url=f'https://www.withny.fun/channels/{cast.username}',
        stream_id=data['uuid'],
        title=data['title'],
        description=data['about'],
        thumbnail_url=data['thumbnailUrl'],
        start=started_at,
        end=ended_at,
        scheduled=None,
        user_id=cast.user_id,
        cast_id=cast.cast_id,
        username=cast.username,
        name=cast.name,
        avatar_url=cast.avatar_url,
        is_live=started_at is not None and ended_at is None,
        price=data['price']
    )


def parse_schedule_record(data: dict) -> WithnyRecord:
    cast = Cast.from_data(data['cast'])
    scheduled = dateutil.parser.parse(data['startAt'])

    stream_record = parse_live_record(data['stream'], cast)
    stream_record.scheduled = scheduled

    return stream_record


def format_timestamp(ts: datetime.datetime) -> str:
    return ts.isoformat(timespec='milliseconds') + 'Z'


def utcnow_with_offset(days: int = 0, hours: int = 0) -> datetime.datetime:
    return datetime.datetime.now() + datetime.timedelta(days=days, hours=hours)

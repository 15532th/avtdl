import dataclasses
import datetime
from enum import Enum
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

import aiohttp
import dateutil.parser
from pydantic import Field, PositiveFloat

from avtdl.core.interfaces import Record
from avtdl.core.monitors import BaseFeedMonitorConfig, PagedFeedMonitor, PagedFeedMonitorEntity
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
    """time of the stream start"""
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
    price: float
    """access price, in points"""

    def __str__(self):
        last_line = ''
        scheduled = self.scheduled
        if scheduled:
            last_line = '\nscheduled to {}'.format(scheduled.strftime('%Y-%m-%d %H:%M'))
        elif self.is_live:
            last_line = '\n[Live]'
        if self.is_member_only:
            last_line += f' [{self.price}pt]'
        template = '{}\n{}\npublished by {}'
        return template.format(self.url, self.title, self.author) + last_line

    def __repr__(self):
        template = '{:<8} [{}] {}'
        return template.format(self.name, self.scheduled or self.start, self.title[:60])

    def get_uid(self) -> str:
        return f'{self.user_id}:{self.scheduled or ""}:{self.start or ""}'

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
            embed['fields'].append({'name': f'[{self.price}]', 'value': '', 'inline': True})
        embed['footer'] = {'text': footer}
        return embed


@Plugins.register('withny', Plugins.kind.ACTOR_CONFIG)
class WithnyMonitorConfig(BaseFeedMonitorConfig):
    pass


class WithnyMonitorMode(str, Enum):
    STREAMS = 'streams'
    SCHEDULES = 'schedules'


@Plugins.register('withny', Plugins.kind.ACTOR_ENTITY)
class WithnyMonitorEntity(PagedFeedMonitorEntity):
    mode: WithnyMonitorMode = WithnyMonitorMode.SCHEDULES
    """mode of operation. Use "streams" to check streams that are currently live, and "schedules" for streams, that were scheduled for future date"""
    update_interval: PositiveFloat = 600
    """how often the monitored channel will be checked, in seconds"""
    url: str = Field(exclude=True, default='')
    """url is not used, all streams are checked through the same api endpoint"""
    since: Optional[datetime.datetime] = Field(exclude=True, default=None)
    """internal variable, used to store "since" parameter of the last successful update"""
    # the following parameters are redefined here in order to hide them from autogenerated help,
    # since in this monitor they should not be relevant for end user
    max_continuation_depth: int = Field(exclude=True, default=50)
    next_page_delay: float = Field(exclude=True, default=1)
    allow_discontinuity: bool = Field(exclude=True, default=False)
    fetch_until_the_end_of_feed_mode: bool = True


@dataclasses.dataclass
class Context:
    count: int
    page: int
    take: int = 40


@Plugins.register('withny', Plugins.kind.ACTOR)
class WithnyMonitor(PagedFeedMonitor):
    """
    Monitor livestreams on Withny
    """

    async def handle_first_page(self, entity: WithnyMonitorEntity, session: aiohttp.ClientSession) -> Tuple[
        Optional[Sequence[WithnyRecord]], Optional[Context]]:
        if entity.mode == WithnyMonitorMode.STREAMS:
            return await self._get_streams(entity, session), None
        elif entity.mode == WithnyMonitorMode.SCHEDULES:
            context = Context(count=0, page=1)
            records, context = await self._get_schedules(entity, session, context)
            return records, context
        else:
            assert False, f'unknown mode "{entity.mode}"'

    async def handle_next_page(self, entity: WithnyMonitorEntity, session: aiohttp.ClientSession,
                               context: Optional[Context]) -> Tuple[
        Optional[Sequence[WithnyRecord]], Optional[Context]]:
        records, context = await self._get_schedules(entity, session, context)
        return records, context

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

    async def _get_streams(self, entity: WithnyMonitorEntity, session: aiohttp.ClientSession) -> Optional[
        Sequence[WithnyRecord]]:
        url = 'https://www.withny.fun/api/streams/with-rooms'
        data = await self.request_json(url, entity, session)
        if data is None:
            return None
        if not isinstance(data, list):
            self.logger.warning(f'unexpected data from /streams/ api: stream list is not a list')
            self.logger.debug(f'raw /streams/ api response: {data}')
            return None
        records = await self._parse_data(data, parse_live_record)
        return records

    async def _get_schedules(self, entity: WithnyMonitorEntity, session: aiohttp.ClientSession,
                             context: Optional[Context]) -> Tuple[
        Optional[Sequence[WithnyRecord]], Optional[Context]]:
        if context is None:
            return [], None

        url = 'https://www.withny.fun/api/schedules'
        since = entity.since or utcnow_with_offset(hours=-3)
        params = {
            'page': context.page,
            'take': context.take,
            'isFavorite': 'false',
            'excludeClosedStream': 'true',
            'later': format_timestamp(since)
        }
        data = await self.request_json(url, entity, session, params=params)
        if data is None:
            return None, context

        entity.since = since

        for name, _type in [('schedules', list), ('count', int)]:
            if not name in data or not isinstance(data[name], _type):
                self.logger.warning(f'unexpected data from /schedules/ api')
                self.logger.debug(f'raw /schedules/ api response: {data}')
                return None, context

        context.count = data['count']
        if context.page * context.take > context.count:  # this page is the last one
            context = None
        else:
            context.page += 1

        schedules_data = data['schedules']
        records = await self._parse_data(schedules_data, parse_schedule_record)
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
    return WithnyRecord(
        url=f'https://www.withny.fun/channels/{cast.username}',
        stream_id=data['uuid'],
        title=data['title'],
        description=data['about'],
        thumbnail_url=data['thumbnailUrl'],
        start=started_at,
        scheduled=None,
        user_id=cast.user_id,
        cast_id=cast.cast_id,
        username=cast.username,
        name=cast.name,
        avatar_url=cast.avatar_url,
        is_live=data['closedAt'] is None,
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

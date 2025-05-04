import dataclasses
import datetime
from typing import Any, Dict, List, Optional

import dateutil.parser

from avtdl.core.interfaces import Record
from avtdl.core.plugins import Plugins
from avtdl.core.utils import utcnow


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
    """scheduled date for the upcoming stream"""
    schedule_id: Optional[str] = None
    """unique id of the stream schedule. Might be absent for live streams"""
    user_id: str
    """unique id of the user hosting this stream"""
    cast_id: Optional[str]
    """unique id of the stream cast. Might be absent"""
    username: str
    """channel name"""
    name: str
    """visible name of the user"""
    avatar_url: str
    """link to the user's avatar"""
    playlist_url: Optional[str] = None
    """link to the underlying hls playlist of the livestream"""

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

    def as_embed(self) -> dict:
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


@dataclasses.dataclass
class Cast:
    username: str
    name: str
    avatar_url: str
    user_id: str
    cast_id: Optional[str]

    @classmethod
    def from_cast_data(cls, data: dict) -> 'Cast':
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

    @classmethod
    def from_owner_data(cls, data: dict) -> 'Cast':
        try:
            return cls(
                username=data['username'],
                name=data['name'],
                avatar_url=data['profileImageUrl'],
                user_id=data['uuid'],
                cast_id=None
            )
        except (TypeError, KeyError, ValueError) as e:
            raise ValueError(f'failed to parse owner info: {type(e)} {e}') from e

def parse_live_record(data: dict, cast: Optional[Cast] = None) -> WithnyRecord:
    if cast is None:
        if 'cast' in data:
            cast = Cast.from_cast_data(data['cast'])
        elif 'owner' in data:
            cast = Cast.from_owner_data(data['owner'])
        else:
            raise ValueError(f'no user info in data')
    started_at = parse_date_fields(data, ['startedAt', 'actualStartedAt'])
    ended_at = parse_date_fields(data, ['closedAt', 'actualClosedAt'])
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
    cast = Cast.from_cast_data(data['cast'])
    scheduled = parse_date_fields(data, ['startAt', 'scheduledStartedAt'])
    schedule_id = data['uuid']

    stream_record = parse_live_record(data['stream'], cast)
    stream_record.scheduled = scheduled
    stream_record.schedule_id = schedule_id

    return stream_record


def format_timestamp(ts: datetime.datetime) -> str:
    ts = ts.astimezone(datetime.timezone.utc)
    text = ts.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    return text


def utcnow_with_offset(days: int = 0, hours: int = 0) -> datetime.datetime:
    return utcnow() + datetime.timedelta(days=days, hours=hours)


def parse_date_fields(data: Dict[str, Any], fields: List[str]) -> Optional[datetime.datetime]:
    """try parsing given fields of the data as datetime, return first successfully parsed or None"""
    for field in fields:
        field_text = data.get(field)
        if field_text is not None:
            return dateutil.parser.parse(field_text)
    return None

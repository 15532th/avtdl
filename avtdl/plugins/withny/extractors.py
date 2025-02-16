import dataclasses
import datetime
from typing import Any, Dict, Optional

import dateutil.parser

from avtdl.core.interfaces import Record
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

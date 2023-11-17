import datetime
from typing import Optional, Sequence

import aiohttp
from pydantic import Field

from core.interfaces import Filter, FilterEntity, Record
from core.monitors import BaseFeedMonitor, BaseFeedMonitorConfig, BaseFeedMonitorEntity
from core.plugins import Plugins
from plugins.filters.filters import EmptyFilterConfig
from plugins.youtube.common import thumbnail_url
from plugins.youtube.feed_info import VideoRendererInfo, handle_page


class YoutubeVideoRecord(VideoRendererInfo, Record):

    video_id: str
    url: str
    title: str
    summary: Optional[str] = Field(repr=False)
    scheduled: Optional[datetime.datetime] = None
    author: Optional[str]
    avatar_url: Optional[str] = None
    channel_link: Optional[str] = None
    channel_id: Optional[str] = None
    published_text: Optional[str]
    length: Optional[str]

    is_upcoming: bool
    is_live: bool
    is_member_only: bool

    def __str__(self):
        scheduled = self.scheduled
        if scheduled:
            scheduled_time = '\nscheduled to {}'.format(scheduled.strftime('%Y-%m-%d %H:%M'))
        else:
            scheduled_time = ''
        template = '{}\n{}\npublished by {}'
        return template.format(self.url, self.title, self.author) + scheduled_time

    def __repr__(self):
        template = '{:<8} [{}] {}'
        return template.format(self.author, self.video_id, self.title[:60])

    def discord_embed(self) -> dict:
        embed = {
            'title': self.title,
            # 'description': ,
            'url': self.url,
            'color': None,
            'author': {'name': self.author, 'url': self.channel_link, 'icon_url': self.avatar_url},
            'image': {'url': thumbnail_url(self.video_id)}
        }
        footer = ''
        if self.published_text:
            footer += self.published_text
        if self.scheduled is not None:
            scheduled = 'live {}'.format(self.scheduled.strftime('%Y-%m-%d %H:%M'))
            footer = footer + f' • {scheduled}' if footer else scheduled
        embed['footer'] = {'text': footer}
        return embed


@Plugins.register('channel', Plugins.kind.ACTOR_CONFIG)
class VideosMonitorConfig(BaseFeedMonitorConfig):
    pass


@Plugins.register('channel', Plugins.kind.ACTOR_ENTITY)
class VideosMonitorEntity(BaseFeedMonitorEntity):
    update_interval: float = 1800


@Plugins.register('channel', Plugins.kind.ACTOR)
class VideosMonitor(BaseFeedMonitor):

    async def get_records(self, entity: BaseFeedMonitorEntity, session: aiohttp.ClientSession) -> Sequence[YoutubeVideoRecord]:
        raw_page = await self.request(entity.url, entity, session)
        if raw_page is None:
            return []
        raw_page_text = await raw_page.text()
        video_info = handle_page(raw_page_text)
        records = [YoutubeVideoRecord.model_validate(info.model_dump()) for info in video_info]
        if not records:
            self.logger.warning(f'[{entity.name}] parsing page "{entity.url}" yielded no videos, check url and cookies')
        records = records[::-1] # records are ordered from old to new on page, reorder in chronological order
        return records

    def get_record_id(self, record: YoutubeVideoRecord) -> str:
        return record.video_id


@Plugins.register('filter.channel', Plugins.kind.ACTOR_CONFIG)
class ChannelFilterConfig(EmptyFilterConfig):
    pass

@Plugins.register('filter.channel', Plugins.kind.ACTOR_ENTITY)
class ChannelFilterEntity(FilterEntity):
    upcoming: bool = True
    live: bool = False
    member_only: bool = False


@Plugins.register('filter.channel', Plugins.kind.ACTOR)
class ChannelFilter(Filter):

    def __init__(self, config: ChannelFilterConfig, entities: Sequence[ChannelFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: ChannelFilterEntity, record: YoutubeVideoRecord) -> Optional[YoutubeVideoRecord]:
        if not isinstance(record, YoutubeVideoRecord):
            self.logger.debug(f'[{entity.name}] record dropped due to unsupported type, expected YoutubeVideoRecord, got {type(record)}')
            return None
        if entity.upcoming and not record.is_upcoming:
            return None
        if entity.live and not record.is_live:
            return None
        if entity.member_only and not record.is_member_only:
            return None
        return record
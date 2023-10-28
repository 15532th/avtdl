import datetime
from typing import Optional, Sequence

import aiohttp
from pydantic import Field

from core.interfaces import Record
from core.monitors import BaseFeedMonitor, BaseFeedMonitorConfig, BaseFeedMonitorEntity
from core.plugins import Plugins
from plugins.youtube.feed_info import VideoRendererInfo, handle_page


class YoutubeVideoRecord(VideoRendererInfo, Record):

    video_id: str
    url: str
    title: str
    summary: Optional[str] = Field(repr=False)
    scheduled: Optional[datetime.datetime] = None
    author: Optional[str]
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
            scheduled_time = '\nscheduled to {}'.format(self.format_date(scheduled))
        else:
            scheduled_time = ''
        template = '{}\n{}\npublished by {} at {}'
        return template.format(self.url, self.title, self.author, self.published_text) + scheduled_time

    def __repr__(self):
        template = '{} {:<8} [{}] {}'
        return template.format(self.published_text, self.author, self.video_id, self.title[:60])

    @staticmethod
    def format_date(date: datetime.datetime) -> str:
        if isinstance(date, str):
            date = datetime.datetime.fromisoformat(date)
        return date.strftime('%Y-%m-%d %H:%M')


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
        return records

    def get_record_id(self, record: YoutubeVideoRecord) -> str:
        return record.video_id

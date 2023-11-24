import datetime
from json import JSONDecodeError
from typing import Optional, Sequence

import aiohttp
from pydantic import Field, ValidationError

from core.interfaces import Filter, FilterEntity, Record
from core.monitors import BaseFeedMonitor, BaseFeedMonitorConfig, BaseFeedMonitorEntity
from core.plugins import Plugins
from plugins.filters.filters import EmptyFilterConfig
from plugins.youtube.common import thumbnail_url
from plugins.youtube.feed_info import VideoRendererInfo, handle_page


class YoutubeVideoRecord(VideoRendererInfo, Record):
    """Youtube video or livestream listed among others on Youtube page

    Produced by parsing channels main page, videos and streams tab,
    as well as playlists, and, with login cookies, subscriptions feed.

    Due to small differences in presentation before-mentioned sources
    have, same video might have slightly different appearance when
    parsed from different url.
    """

    video_id: str
    """Short string identifying video on Youtube. Part of video url"""
    url: str
    """Link to video, uses "https://www.youtube.com/watch?v=<video_id>" format"""
    title: str
    """Title of the video at time of parsing"""
    summary: Optional[str] = Field(repr=False)
    """Snippet of video description. Not always available"""
    scheduled: Optional[datetime.datetime] = None
    """Scheduled date for upcoming stream or premiere"""
    author: Optional[str]
    """Author name"""
    avatar_url: Optional[str] = None
    """Link to avatar of the channel. Not always available"""
    channel_link: Optional[str] = None
    """Link to the channel of the video"""
    channel_id: Optional[str] = None
    """Channel ID in old format"""
    published_text: Optional[str]
    """Localized text saying how long ago the video was uploaded"""
    length: Optional[str]
    """Duration of the video"""

    is_upcoming: bool
    """Indicates that video is an upcoming livestream or premiere"""
    is_live: bool
    """Indicates that the video is a livestream or premiere that is currently live"""
    is_member_only: bool
    """Indicated that the video is limited to members of the channel"""

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
            scheduled = self.scheduled.strftime('%Y-%m-%d %H:%M')
            embed['fields'] = [{'name': 'Scheduled:', 'value': scheduled, 'inline': True}]
        embed['footer'] = {'text': footer}
        return embed


@Plugins.register('channel', Plugins.kind.ACTOR_CONFIG)
class VideosMonitorConfig(BaseFeedMonitorConfig):
    pass


@Plugins.register('channel', Plugins.kind.ACTOR_ENTITY)
class VideosMonitorEntity(BaseFeedMonitorEntity):
    update_interval: float = 1800
    """How often the monitored url will be checked, in seconds"""


@Plugins.register('channel', Plugins.kind.ACTOR)
class VideosMonitor(BaseFeedMonitor):
    """
    Youtube channel monitor

    Monitors Youtube url listing videos, such as channels main page,
    videos and streams tab of a channel, as well as playlists, and,
    with login cookies, subscriptions feed or even the main page.

    Examples of supported url:
    https://www.youtube.com/@ChannelName
    https://www.youtube.com/@ChannelName/videos
    https://www.youtube.com/@ChannelName/streams
    https://www.youtube.com/channel/UCK0V3b23uJyU4N8eR_BR0QA/
    https://www.youtube.com/playlist?list=PLWGY3fcU-ZeQmBfoJ6SmT8v2zV8NEhrB2
    https://www.youtube.com/feed/subscriptions (providing cookies is necessarily)

    Unlike RSS monitor, with login cookies it can see videos and streams
    with limited access (such as member-only).

    While monitoring a single channel is less efficient, both
    bandwidth- and computational-wise, using this monitor with
    subscriptions feed url on a dedicated account is a recommended way
    to monitor a high amount (hundreds) of channels, as it only requires
    loading a single page to check all of them for updates.
    """

    async def get_records(self, entity: BaseFeedMonitorEntity, session: aiohttp.ClientSession) -> Sequence[YoutubeVideoRecord]:
        raw_page = await self.request(entity.url, entity, session)
        if raw_page is None:
            return []
        raw_page_text = await raw_page.text()
        try:
            video_info = handle_page(raw_page_text)
            records = [YoutubeVideoRecord.model_validate(info.model_dump()) for info in video_info]
        except (ValueError, JSONDecodeError, ValidationError) as e:
            self.logger.warning(f'[{entity.name}] failed to parse page from "{entity.url}"')
            self.logger.debug(f'[{entity.name}] {type(e)}: {e}')
            self.logger.debug(f'[{entity.name}] raw page:\n{raw_page_text}')
            return []
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
    """To pass filter record should be upcoming livestream or scheduled premiere"""
    live: bool = False
    """To pass filter record should be ongoing livestream"""
    member_only: bool = False
    """To pass filter record should be marked as member-only"""


@Plugins.register('filter.channel', Plugins.kind.ACTOR)
class ChannelFilter(Filter):
    """Filter that only lets YoutubeVideoRecord through if it has certain properties

    If multiple settings are set to "true", they all should match. Use multiple
    entities if picking records with one of properties is required.

    All records from other sources pass through without filtering.
    """

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
import datetime
import json
from json import JSONDecodeError
from typing import Any, List, Optional, Sequence, Tuple

import aiohttp
from pydantic import Field, ValidationError

from avtdl.core import utils
from avtdl.core.interfaces import Filter, FilterEntity, Record
from avtdl.core.monitors import PagedFeedMonitor, PagedFeedMonitorConfig, PagedFeedMonitorEntity
from avtdl.core.plugins import Plugins
from avtdl.plugins.filters.filters import EmptyFilterConfig
from avtdl.plugins.youtube.common import get_innertube_context, handle_consent, prepare_next_page_request, thumbnail_url
from avtdl.plugins.youtube.feed_info import AuthorInfo, VideoRendererInfo, get_video_renderers, parse_owner_info, parse_video_renderer


@Plugins.register('channel', Plugins.kind.ASSOCIATED_RECORD)
@Plugins.register('filter.channel', Plugins.kind.ASSOCIATED_RECORD)
class YoutubeVideoRecord(VideoRendererInfo, Record):
    """
    Youtube video or livestream listed among others on Youtube page

    Produced by parsing a channels main page, videos and streams tab,
    as well as playlists, and, with login cookies, subscriptions feed.
    """

    video_id: str
    """short string identifying video on Youtube. Part of video url"""
    url: str
    """link to the video, uses `https://www.youtube.com/watch?v=<video_id>` format"""
    title: str
    """title of the video at the time of parsing"""
    summary: Optional[str] = Field(repr=False)
    """snippet of the video description. Not always available"""
    scheduled: Optional[datetime.datetime] = None
    """scheduled date for upcoming stream or premiere"""
    author: Optional[str]
    """channel name"""
    avatar_url: Optional[str] = None
    """link to the avatar of the channel. Not always available"""
    channel_link: Optional[str] = None
    """link to the channel uploading the video"""
    channel_id: Optional[str] = None
    """channel ID in old format (such as `UCK0V3b23uJyU4N8eR_BR0QA`)"""
    published_text: Optional[str]
    """localized text saying how long ago the video was uploaded"""
    length: Optional[str]
    """text showing the video duration (hh:mm:ss)"""

    is_upcoming: bool
    """indicates that video is an upcoming livestream or premiere"""
    is_live: bool
    """indicates that the video is a livestream or premiere that is currently live"""
    is_member_only: bool
    """indicated that the video is limited to members of the channel. Note that the video status might be changed at any time"""

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
        return template.format(self.author or 'Unknown author', self.video_id, self.title[:60])

    def discord_embed(self) -> dict:
        embed = {
            'title': self.title,
            # 'description': ,
            'url': self.url,
            'color': None,
            'author': {'name': self.author, 'url': self.channel_link, 'icon_url': self.avatar_url},
            'image': {'url': thumbnail_url(self.video_id)},
            'fields': []
        }
        footer = ''
        if self.published_text:
            footer += self.published_text
        if self.length:
            embed['fields'].append({'name': f'[{self.length}]', 'value': '', 'inline': True})
        if self.scheduled is not None:
            scheduled = self.scheduled.strftime('%Y-%m-%d %H:%M')
            embed['fields'].append({'name': 'Scheduled:', 'value': scheduled, 'inline': True})
        embed['footer'] = {'text': footer}
        return embed


@Plugins.register('channel', Plugins.kind.ACTOR_CONFIG)
class VideosMonitorConfig(PagedFeedMonitorConfig):
    pass


@Plugins.register('channel', Plugins.kind.ACTOR_ENTITY)
class VideosMonitorEntity(PagedFeedMonitorEntity):
    update_interval: float = 1800


@Plugins.register('channel', Plugins.kind.ACTOR)
class VideosMonitor(PagedFeedMonitor):
    """
    Youtube channel monitor

    Monitors Youtube url listing videos, such as channels main page,
    videos and streams tab of a channel, as well as playlists, and,
    with login cookies, subscriptions feed or the main page.

    Due to small differences in presentation in aforementioned
    sources, same video might have slightly different appearance when
    parsed from different urls. For example, video parsed from main
    page or subscriptions feed will not have full description text.

    Examples of supported url:

    - `https://www.youtube.com/@ChannelName`
    - `https://www.youtube.com/@ChannelName/videos`
    - `https://www.youtube.com/@ChannelName/streams`
    - `https://www.youtube.com/channel/UCK0V3b23uJyU4N8eR_BR0QA/`
    - `https://www.youtube.com/playlist?list=PLWGY3fcU-ZeQmBfoJ6SmT8v2zV8NEhrB2`
    - `https://www.youtube.com/feed/subscriptions` (providing cookies is necessarily)

    Unlike `rss` monitor, with login cookies it can see videos and streams
    with limited access (such as member-only).

    While monitoring a single channel is less efficient, using this monitor with
    subscriptions feed url on a dedicated account is a recommended way
    to monitor a high amount (hundreds) of channels, as it only requires
    loading a single page to check all of them for updates.
    """

    async def handle_first_page(self, entity: PagedFeedMonitorEntity, session: aiohttp.ClientSession) -> Tuple[Optional[Sequence[Record]], Optional[Any]]:
        raw_page_text = await self.request(entity.url, entity, session)
        if raw_page_text is None:
            return None, None
        raw_page_text = await handle_consent(raw_page_text, entity.url, session, self.logger)
        video_renderers, continuation_token, page = get_video_renderers(raw_page_text)
        owner_info = parse_owner_info(page)
        current_page_records = self._parse_entries(owner_info, video_renderers, entity)
        innertube_context = get_innertube_context(raw_page_text)
        return current_page_records, (innertube_context, owner_info, continuation_token)

    async def handle_next_page(self, entity: PagedFeedMonitorEntity, session: aiohttp.ClientSession, context: Optional[Any]) -> Tuple[Optional[Sequence[Record]], Optional[Any]]:
        innertube_context, owner_info, continuation_token = context  # type: ignore
        if continuation_token is None:
            self.logger.debug(f'[{entity.name}] no continuation for next page, done loading')
            return [], None

        url, headers, post_body = prepare_next_page_request(innertube_context, continuation_token, cookies=session.cookie_jar)
        raw_page = await utils.request(url, session, self.logger, method='POST', headers=headers,
                                       data=json.dumps(post_body), retry_times=3, retry_multiplier=2,
                                       retry_delay=5)
        if raw_page is None:
            self.logger.debug(f'[{entity.name}] failed to load next page, aborting')
            return None, None
        video_renderers, continuation_token, page = get_video_renderers(raw_page, anchor='')
        context = (innertube_context, owner_info, continuation_token) if continuation_token else None
        current_page_records = self._parse_entries(owner_info, video_renderers, entity)
        return current_page_records, context

    def _parse_entries(self, owner_info: Optional[AuthorInfo], video_renderers: List[dict], entity: PagedFeedMonitorEntity) -> List[YoutubeVideoRecord]:
        records: List[YoutubeVideoRecord] = []
        for item in video_renderers:
            try:
                info = parse_video_renderer(item, owner_info, raise_on_error=True)
                record = YoutubeVideoRecord.model_validate(info.model_dump())
                records.append(record)
            except (ValueError, JSONDecodeError, ValidationError) as e:
                self.logger.warning(f'[{entity.name}] failed to parse video renderer on "{entity.url}": {type(e)}: {e}')
                self.logger.debug(f'[{entity.name}] raw video renderer:\n{item}')
                continue
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
    """to pass the filter a record should be either upcoming livestream or scheduled premiere"""
    live: bool = False
    """to pass the filter a record should be an ongoing livestream"""
    member_only: bool = False
    """to pass the filter a record should be marked as member-only"""


@Plugins.register('filter.channel', Plugins.kind.ACTOR)
class ChannelFilter(Filter):
    """
    Pick `YoutubeVideoRecord` with specified properties

    Filter that only lets `YoutubeVideoRecord` through if it has certain properties.
    All records from other sources pass through without filtering.

    If multiple settings are set to `true`, they all should match. Use multiple
    entities if picking records with any of multiple properties is required.
    """

    def __init__(self, config: ChannelFilterConfig, entities: Sequence[ChannelFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: ChannelFilterEntity, record: YoutubeVideoRecord) -> Optional[YoutubeVideoRecord]:
        if not isinstance(record, YoutubeVideoRecord):
            self.logger.debug(f'[{entity.name}] record is not a YoutubeVideoRecord, letting it through: {record!r}')
            return record
        if entity.upcoming and not record.is_upcoming:
            return None
        if entity.live and not record.is_live:
            return None
        if entity.member_only and not record.is_member_only:
            return None
        return record

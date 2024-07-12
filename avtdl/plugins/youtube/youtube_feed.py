import datetime
import json
from json import JSONDecodeError
from typing import Any, Dict, List, Optional, Sequence, Tuple

import aiohttp
from pydantic import Field, ValidationError

from avtdl.core import utils
from avtdl.core.interfaces import Filter, FilterEntity, Record
from avtdl.core.monitors import PagedFeedMonitor, PagedFeedMonitorConfig, PagedFeedMonitorEntity
from avtdl.core.plugins import Plugins
from avtdl.plugins.filters.filters import EmptyFilterConfig
from avtdl.plugins.youtube.common import NextPageContext, get_innertube_context, get_session_index, handle_consent, \
    prepare_next_page_request
from avtdl.plugins.youtube.feed_info import AuthorInfo, VideoRendererInfo, get_video_renderers, parse_owner_info, \
    parse_video_renderer


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
    thumbnail_url: Optional[str] = None
    """link to the video thumbnail"""
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
        last_line = ''
        scheduled = self.scheduled
        if scheduled:
            last_line = '\nscheduled to {}'.format(scheduled.strftime('%Y-%m-%d %H:%M'))
        elif self.is_live:
            last_line = '\n[Live]'
        if self.is_member_only:
            last_line += ' [Member only]'
        template = '{}\n{}\npublished by {}'
        return template.format(self.url, self.title, self.author) + last_line

    def __repr__(self):
        template = '{:<8} [{}] {}'
        return template.format(self.author or 'Unknown author', self.video_id, self.title[:60])

    def get_uid(self) -> str:
        return self.video_id

    def discord_embed(self) -> dict:
        embed: Dict[str, Any] = {
            'title': self.title,
            # 'description': ,
            'url': self.url,
            'color': None,
            'author': {'name': self.author, 'url': self.channel_link, 'icon_url': self.avatar_url},
            'image': {'url': self.thumbnail_url},
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
        if self.is_live:
            embed['fields'].append({'name': '[Live]', 'value': '', 'inline': True})
        if self.is_member_only:
            embed['fields'].append({'name': '[Member only]', 'value': '', 'inline': True})
        embed['footer'] = {'text': footer}
        return embed


@Plugins.register('channel', Plugins.kind.ACTOR_CONFIG)
class VideosMonitorConfig(PagedFeedMonitorConfig):
    pass


@Plugins.register('channel', Plugins.kind.ACTOR_ENTITY)
class VideosMonitorEntity(PagedFeedMonitorEntity):
    update_interval: float = 1800


class FeedPageContext(NextPageContext):
    owner_info: Optional[AuthorInfo]


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

    When main page of a channel (https://www.youtube.com/@ChannelName) is viewed
    in logged in state, it might contain "For you" block, which content might
    vary with subsequent updates. As a result, monitoring this url might occasionally
    produce records with old videos that got showed in this block. If monitoring
    without a cookies file is not an option, use a combination of "Videos" and "Streams"
    tabs instead.
    """

    async def handle_first_page(self, entity: PagedFeedMonitorEntity, session: aiohttp.ClientSession) -> Tuple[Optional[Sequence[Record]], Optional[FeedPageContext]]:
        raw_page_text = await self.request(entity.url, entity, session)
        if raw_page_text is None:
            return None, None
        raw_page_text = await handle_consent(raw_page_text, entity.url, session, self.logger)
        video_renderers, continuation_token, page = get_video_renderers(raw_page_text)
        if not video_renderers:
            self.logger.debug(f'[{entity.name}] found no videos on first page of {entity.url}')
        owner_info = parse_owner_info(page)
        current_page_records = self._parse_entries(owner_info, video_renderers, entity)
        innertube_context = get_innertube_context(raw_page_text)
        session_index = get_session_index(page)
        context = FeedPageContext(innertube_context=innertube_context, session_index=session_index, continuation_token=continuation_token, owner_info=owner_info)
        return current_page_records, context

    async def handle_next_page(self, entity: PagedFeedMonitorEntity, session: aiohttp.ClientSession, context: Optional[FeedPageContext]) -> Tuple[Optional[Sequence[Record]], Optional[FeedPageContext]]:
        if context is None or context.continuation_token is None:
            self.logger.debug(f'[{entity.name}] no continuation for next page, done loading')
            return [], None

        url, headers, post_body = prepare_next_page_request(context.innertube_context, context.continuation_token, cookies=session.cookie_jar)
        raw_page = await utils.request(url, session, self.logger, method='POST', headers=headers,
                                       data=json.dumps(post_body), retry_times=3, retry_multiplier=2,
                                       retry_delay=5)
        if raw_page is None:
            self.logger.debug(f'[{entity.name}] failed to load next page, aborting')
            return None, None
        video_renderers, continuation_token, page = get_video_renderers(raw_page, anchor='')

        if not video_renderers:
            self.logger.debug(f'[{entity.name}] found no videos when parsing continuation of {entity.url}')
        current_page_records = self._parse_entries(context.owner_info, video_renderers, entity)

        if continuation_token is not None:
            context.continuation_token = continuation_token
        else:
            context = None
        return current_page_records, context

    def _parse_entries(self, owner_info: Optional[AuthorInfo], video_renderers: List[dict], entity: PagedFeedMonitorEntity) -> List[YoutubeVideoRecord]:
        records: List[YoutubeVideoRecord] = []
        for item in video_renderers:
            try:
                info = parse_video_renderer(item, owner_info, raise_on_error=True)
                record = YoutubeVideoRecord.model_validate(info.model_dump())
                records.append(record)
            except (AttributeError, ValueError, JSONDecodeError, ValidationError) as e:
                self.logger.warning(f'[{entity.name}] failed to parse video renderer on "{entity.url}": {type(e)}: {e}')
                self.logger.debug(f'[{entity.name}] raw video renderer:\n{item}')
                continue
        records = records[::-1] # records are ordered from old to new on page, reorder in chronological order
        return records

    def record_got_updated(self, record: YoutubeVideoRecord, entity: VideosMonitorEntity) -> bool:
        excluded_fields = {'published_text'}
        return self.db.record_has_changed(record, entity.name, excluded_fields)


@Plugins.register('filter.channel', Plugins.kind.ACTOR_CONFIG)
class ChannelFilterConfig(EmptyFilterConfig):
    pass

@Plugins.register('filter.channel', Plugins.kind.ACTOR_ENTITY)
class ChannelFilterEntity(FilterEntity):
    upcoming: bool = False
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

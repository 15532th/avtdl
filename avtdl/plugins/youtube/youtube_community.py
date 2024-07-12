import json
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import aiohttp

from avtdl.core import utils
from avtdl.core.interfaces import MAX_REPR_LEN, Record
from avtdl.core.monitors import PagedFeedMonitor, PagedFeedMonitorConfig, PagedFeedMonitorEntity
from avtdl.core.plugins import Plugins
from avtdl.plugins.youtube.common import NextPageContext, get_continuation_token, get_initial_data, get_innertube_context, get_session_index, handle_consent, prepare_next_page_request, thumbnail_url, \
    video_url
from avtdl.plugins.youtube.community_info import CommunityPostInfo, SharedCommunityPostInfo, get_posts_renderers, get_renderers, get_shared_posts_renderers


@Plugins.register('community', Plugins.kind.ASSOCIATED_RECORD)
class CommunityPostRecord(Record, CommunityPostInfo):
    """Youtube community post content"""
    channel_id: str
    """channel ID in old format"""
    post_id: str
    """unique id of the post"""
    author: str
    """author's channel name"""
    avatar_url: Optional[str] = None
    """link to the avatar of the channel"""
    vote_count: str
    """current number of upvotes"""
    sponsor_only: bool
    """indicates whether the post is member-only"""
    published_text: str
    """localized text saying how long ago the video was uploaded"""
    full_text: str
    """post contents as plaintext"""
    attachments: List[str]
    """list of links to attached images or video thumbnails"""
    video_id: Optional[str] = None
    """if the post links to youtube video will contain video id, otherwise absent"""
    original_post: Optional['CommunityPostRecord'] = None
    """for reposts contains original post content, otherwise absent"""

    def __repr__(self) -> str:
        text = self.full_text.replace('\n', ' • ')[:MAX_REPR_LEN]
        return f'{self.post_id} [{self.author}] {text}'

    def __str__(self) -> str:
        channel_post_url = f'https://www.youtube.com/channel/{self.channel_id}/community?lb={self.post_id}'
        sponsor_only = '(Member only)' if self.sponsor_only else ''

        header = f'[{self.author}, {self.published_text} {sponsor_only}] {self.vote_count}'
        body = self.full_text
        attachments = '\n'.join(self.attachments)
        video = ''
        if self.video_id and self.full_text.find(self.video_id) == -1:
            video = video_url(self.video_id)
        original_post = str(self.original_post) if self.original_post else ''
        return '\n'.join((channel_post_url, header, body, video, attachments, original_post))

    def get_uid(self) -> str:
        return  f'{self.channel_id}:{self.post_id}'

    def discord_embed(self) -> List[dict]:
        channel_url = f'https://www.youtube.com/channel/{self.channel_id}'
        post_url = f'https://www.youtube.com/post/{self.post_id}'

        attachments = '\n'.join(self.attachments) if len(self.attachments) > 4 else ''
        video = ''
        if self.video_id and self.full_text.find(self.video_id) == -1:
            video = video_url(self.video_id)
        original_post = str(self.original_post) if self.original_post else ''
        text = '\n'.join([self.full_text, attachments, video, original_post])

        embed: Dict[str, Any] = {
            'title': self.post_id,
            'description': text,
            'url': post_url,
            'color': None,
            'author': {'name': self.author, 'url': channel_url, 'icon_url': self.avatar_url},
            'footer': {'text': self.published_text}
        }
        if self.sponsor_only:
            embed['fields'] = [{'name': 'Member only', 'value': ''}]
        if self.video_id:
            embed['image'] = {'url': thumbnail_url(self.video_id)}
        if self.attachments:
            images: List[dict] = [{'url': post_url, 'image': {'url': attachment}} for attachment in self.attachments]
            if embed.get('image') is None:
                embed['image'] = images.pop(0)['image']
            embeds = [embed, *images]
        else:
            embeds = [embed]
        return embeds

@Plugins.register('community', Plugins.kind.ASSOCIATED_RECORD)
class SharedCommunityPostRecord(Record, SharedCommunityPostInfo):
    """Youtube community post that is itself a repost of another post"""
    channel_id: str
    """channel ID in old format"""
    post_id: str
    """unique id of the post"""
    author: str
    """author's channel name"""
    avatar_url: Optional[str] = None
    """link to the avatar of the channel"""
    published_text: str
    """localized text saying how long ago the video was uploaded"""
    full_text: str
    """post content"""
    original_post: Optional['CommunityPostRecord'] = None
    """not present in shared post"""

    def __repr__(self) -> str:
        text = self.full_text.replace('\n', ' • ')[:MAX_REPR_LEN]
        return f'{self.post_id} [{self.author}] {text}'

    def __str__(self) -> str:
        channel_post_url = f'https://www.youtube.com/channel/{self.channel_id}/community?lb={self.post_id}'

        header = f'[{self.author}, {self.published_text} ]'
        body = self.full_text
        original_post = str(self.original_post) if self.original_post else ''
        return '\n'.join((channel_post_url, header, body, original_post))

    def get_uid(self) -> str:
        return  f'{self.channel_id}:{self.post_id}'

    def discord_embed(self) -> List[dict]:
        channel_url = f'https://www.youtube.com/channel/{self.channel_id}'
        post_url = f'https://www.youtube.com/post/{self.post_id}'

        original_post = str(self.original_post) if self.original_post else ''
        text = '\n'.join([self.full_text, '', original_post])

        embed = {
            'title': self.post_id,
            'description': text,
            'url': post_url,
            'color': None,
            'author': {'name': self.author, 'url': channel_url, 'icon_url': self.avatar_url},
            'footer': {'text': self.published_text}
        }
        embeds = [embed]
        return embeds


@Plugins.register('community', Plugins.kind.ACTOR_CONFIG)
class CommunityPostsMonitorConfig(PagedFeedMonitorConfig):
    pass


@Plugins.register('community', Plugins.kind.ACTOR_ENTITY)
class CommunityPostsMonitorEntity(PagedFeedMonitorEntity):
    url: str
    """url of the community page of the channel"""
    update_interval: float = 1800
    """how often the community page will be checked for new posts"""

@Plugins.register('community', Plugins.kind.ACTOR)
class CommunityPostsMonitor(PagedFeedMonitor):
    """
    Youtube community page monitor

    Monitors posts on community page of a channel, supports
    member-only posts if login cookies are provided. Some features,
    such as polls, are not supported.

    Examples of supported url:

    - `https://www.youtube.com/@ChannelName/community`
    - `https://www.youtube.com/channel/UCK0V3b23uJyU4N8eR_BR0QA/community`
    """

    async def handle_first_page(self, entity: PagedFeedMonitorEntity, session: aiohttp.ClientSession) -> Tuple[Optional[Sequence[Record]], Optional[NextPageContext]]:
        raw_page_text = await self.request(entity.url, entity, session)
        if raw_page_text is None:
            return None, None
        raw_page_text = await handle_consent(raw_page_text, entity.url, session, self.logger)
        try:
            initial_page = get_initial_data(raw_page_text)
        except Exception as e:
            self.logger.exception(f'[{entity.name}] failed to get initial data from {entity.url}: {e}')
            return None, None
        records = self._parse_entries(initial_page)

        continuation_token = get_continuation_token(initial_page)
        innertube_context = get_innertube_context(raw_page_text)
        session_index = get_session_index(initial_page)
        ctx = NextPageContext(innertube_context=innertube_context, session_index=session_index, continuation_token=continuation_token)

        return records, ctx

    async def handle_next_page(self, entity: PagedFeedMonitorEntity, session: aiohttp.ClientSession, context: Optional[NextPageContext]) -> Tuple[Optional[Sequence[Record]], Optional[NextPageContext]]:
        if context is None or context.continuation_token is None:
            self.logger.debug(f'[{entity.name}] no continuation for next page, done loading')
            return [], None

        url, headers, post_body = prepare_next_page_request(context.innertube_context, context.continuation_token, session.cookie_jar, context.session_index)
        current_page = await utils.request_json(url, session, self.logger, method='POST', headers=headers,
                                                data=json.dumps(post_body), retry_times=3, retry_multiplier=2,
                                                retry_delay=5)
        if current_page is None:
            self.logger.debug(f'[{entity.name}] failed to load next page, aborting')
            return None, None
        current_page_records = self._parse_entries(current_page) or []
        context.continuation_token = get_continuation_token(current_page)
        if context.continuation_token is None:
            context = None
        return current_page_records, context

    def _parse_entries(self, page: dict) -> List[Union[CommunityPostRecord, SharedCommunityPostRecord]]:
        renderers = get_renderers(page)
        records: List[Union[CommunityPostRecord, SharedCommunityPostRecord]] = []
        post_renderers = get_posts_renderers(renderers)
        for item in post_renderers:
            try:
                info = CommunityPostInfo.from_post_renderer(item)
                record = CommunityPostRecord(**info.model_dump())
                records.append(record)
            except Exception as e:
                self.logger.debug(f'error parsing post renderer {item}: {e}')
                continue
        shared_post_renderers = get_shared_posts_renderers(renderers)
        for item in shared_post_renderers:
            try:
                info = SharedCommunityPostInfo.from_post_renderer(item)
                record = SharedCommunityPostRecord(**info.model_dump())
                records.append(record)
            except Exception as e:
                self.logger.debug(f'error parsing shared post renderer {item}: {e}')
                continue
        return records


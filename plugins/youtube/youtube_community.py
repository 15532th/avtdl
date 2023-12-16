import json
from typing import Any, List, Optional, Sequence, Tuple

import aiohttp
from pydantic import ValidationError

from core import utils
from core.interfaces import MAX_REPR_LEN, Record
from core.monitors import PagedFeedMonitor, PagedFeedMonitorConfig, PagedFeedMonitorEntity
from core.plugins import Plugins
from plugins.youtube.common import get_continuation_token, get_initial_data, get_innertube_context, handle_consent, prepare_next_page_request, thumbnail_url, \
    video_url
from plugins.youtube.community_info import CommunityPostInfo, get_posts_renderers


class CommunityPostRecord(Record, CommunityPostInfo):
    channel_id: str
    post_id: str
    author: str
    avatar_url: Optional[str] = None
    vote_count: str
    sponsor_only: bool
    published_text: str
    full_text: str
    attachments: List[str]
    video_id: Optional[str] = None
    original_post: Optional['CommunityPostRecord'] = None

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

    def discord_embed(self) -> List[dict]:
        channel_url = f'https://www.youtube.com/channel/{self.channel_id}'
        post_url = f'https://www.youtube.com/post/{self.post_id}'

        attachments = '\n'.join(self.attachments) if len(self.attachments) > 4 else ''
        video = ''
        if self.video_id and self.full_text.find(self.video_id) == -1:
            video = video_url(self.video_id)
        original_post = str(self.original_post) if self.original_post else ''
        text = '\n'.join([self.full_text, attachments, video, original_post])

        embed = {
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
            images = [{'url': post_url, 'image': {'url': attachment}} for attachment in self.attachments]
            if embed.get('image') is None:
                embed['image'] = images.pop(0)['image']
            embeds = [embed, *images]
        else:
            embeds = [embed]
        return embeds

@Plugins.register('community', Plugins.kind.ACTOR_CONFIG)
class CommunityPostsMonitorConfig(PagedFeedMonitorConfig):
    pass


@Plugins.register('community', Plugins.kind.ACTOR_ENTITY)
class CommunityPostsMonitorEntity(PagedFeedMonitorEntity):
    update_interval: float = 1800

@Plugins.register('community', Plugins.kind.ACTOR)
class CommunityPostsMonitor(PagedFeedMonitor):

    def get_record_id(self, record: CommunityPostRecord) -> str:
        return f'{record.channel_id}:{record.post_id}'

    async def handle_first_page(self, entity: PagedFeedMonitorEntity, session: aiohttp.ClientSession) -> Tuple[Optional[Sequence[Record]], Optional[Any]]:
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

        return records, (innertube_context, continuation_token)

    async def handle_next_page(self, entity: PagedFeedMonitorEntity, session: aiohttp.ClientSession, context: Optional[Any]) -> Tuple[Optional[Sequence[Record]], Optional[Any]]:
        innertube_context, continuation_token = context  # type: ignore
        if continuation_token is None:
            self.logger.debug(f'[{entity.name}] no continuation for next page, done loading')
            return [], None

        url, headers, post_body = prepare_next_page_request(innertube_context, continuation_token, cookies=session.cookie_jar)
        current_page = await utils.request_json(url, session, self.logger, method='POST', headers=headers,
                                             data=json.dumps(post_body), retry_times=3, retry_multiplier=2,
                                             retry_delay=5)
        if current_page is None:
            self.logger.debug(f'[{entity.name}] failed to load next page, aborting')
            return None, None
        current_page_records = self._parse_entries(current_page) or []
        continuation_token = get_continuation_token(current_page)
        context = (innertube_context, continuation_token) if continuation_token else None
        return current_page_records, context

    def _parse_entries(self, page: dict) -> List[CommunityPostRecord]:
        post_renderers = get_posts_renderers(page)
        records: List[CommunityPostRecord] = []
        for item in post_renderers:
            try:
                info = CommunityPostInfo.from_post_renderer(item)
                record = CommunityPostRecord(**info.model_dump())
                records.append(record)
            except ValidationError as e:
                self.logger.debug(f'error parsing item {item}: {e}')
                continue
        return records


import datetime
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import dateutil.parser
from pydantic import BaseModel, Field, PositiveFloat

from avtdl.core.config import Plugins
from avtdl.core.interfaces import MAX_REPR_LEN, Record
from avtdl.core.monitors import PagedFeedMonitor, \
    PagedFeedMonitorConfig, PagedFeedMonitorEntity
from avtdl.core.request import HttpClient, RequestDetails
from avtdl.core.runtime import RuntimeContext
from avtdl.core.utils import JSONType, find_one, try_parse_date, with_prefix


@Plugins.register('nicochannel', Plugins.kind.ASSOCIATED_RECORD)
class NicochannelVideoRecord(Record):
    """
    Nicochannel.jp video, upcoming or ongoing livestream
    """

    video_id: str
    """short string identifying the video. Part of the video url"""
    url: str
    """video url"""
    title: str
    """title of the video at the time of parsing"""
    summary: Optional[str] = Field(repr=False, default=None)
    """livestream description. Not always available"""
    published: datetime.datetime
    """publication date"""
    scheduled: Optional[datetime.datetime] = None
    """scheduled date for upcoming stream or premiere"""
    thumbnail_url: Optional[str] = None
    """link to the video thumbnail"""
    length: Optional[int]
    """video duration in seconds"""

    author: Optional[str]
    """channel name"""
    avatar_url: Optional[str] = None
    """link to the avatar of the channel. Not always available"""
    fanclub_url: Optional[str] = None
    """link to the channel uploading the video"""
    fanclub_id: int
    """internal fanclub id"""

    is_upcoming: bool
    """indicates that video is an upcoming livestream"""
    is_live: bool
    """indicates that the video is a livestream that is currently live"""

    def __str__(self):
        last_line = ''
        scheduled = self.scheduled
        if scheduled:
            last_line = '\nscheduled to {}'.format(scheduled.strftime('%Y-%m-%d %H:%M'))
        elif self.is_live:
            last_line = '\n[Live]'
        template = '{}\n{}\npublished by {}'
        return template.format(self.url, self.title, self.author) + last_line

    def __repr__(self):
        template = '{:<8} [{}] {}'
        return template.format(self.author or 'Unknown author', self.video_id, self.title[:60])

    def get_uid(self) -> str:
        return self.video_id

    def as_embed(self) -> dict:
        embed: Dict[str, Any] = {
            'title': self.title,
            # 'description': ,
            'url': self.url,
            'color': None,
            'author': {'name': self.author, 'url': self.fanclub_url, 'icon_url': self.avatar_url},
            'image': {'url': self.thumbnail_url},
            'fields': [],
            'timestamp': self.published.isoformat(),
        }
        footer = ''
        if self.length:
            embed['fields'].append({'name': f'[{self.length}]', 'value': '', 'inline': True})
        if self.scheduled is not None:
            scheduled = self.scheduled.strftime('%Y-%m-%d %H:%M')
            embed['fields'].append({'name': 'Scheduled:', 'value': scheduled, 'inline': True})
        if self.is_live:
            embed['fields'].append({'name': '[Live]', 'value': '', 'inline': True})
        embed['footer'] = {'text': footer}
        return embed


@Plugins.register('nicochannel.news', Plugins.kind.ASSOCIATED_RECORD)
class NicochannelPostRecord(Record):
    """Article from the NEWS tab"""
    post_id: str
    """unique id of the post"""
    url: str
    """post direct url"""
    title: str
    """post title"""
    full_text: str
    """post content"""
    thumbnail_url: Optional[str] = None
    """link to the post image"""
    published: datetime.datetime
    """publication date"""

    post_category: Optional[str]
    """post category label"""
    post_status: Optional[str]
    """post status label"""
    post_authorization: Optional[str]
    """post authorization label"""

    author: Optional[str]
    """channel name"""
    avatar_url: Optional[str] = None
    """link to the avatar of the channel. Not always available"""
    fanclub_url: Optional[str] = None
    """link to the channel uploading the video"""
    fanclub_id: int
    """internal fanclub id"""

    def __repr__(self) -> str:
        text = self.full_text.replace('\n', ' • ')[:MAX_REPR_LEN]
        return f'{self.post_id} [{self.author}] {text}'

    def __str__(self) -> str:
        header = f'[{self.author}, {self.published}'
        return '\n'.join((self.url, header, self.full_text, self.thumbnail_url or ''))

    def get_uid(self) -> str:
        return self.post_id

    def as_embed(self) -> List[dict]:
        embed: Dict[str, Any] = {
            'title': self.post_id,
            'description': self.full_text,
            'url': self.url,
            'color': None,
            'author': {'name': self.author, 'url': self.fanclub_url, 'icon_url': self.avatar_url},
            'timestamp': self.published.isoformat(),
            'fields': []
        }
        if self.post_authorization:
            embed['fields'].append({'name': 'access:', 'value': self.post_authorization, 'inline': True})
        if self.post_status:
            embed['fields'].append({'name': 'status:', 'value': self.post_status, 'inline': True})
        if self.post_category:
            embed['fields'].append({'name': 'category:', 'value': self.post_category, 'inline': True})
        embed['image'] = {'url': self.thumbnail_url}
        return [embed]


@Plugins.register('nicochannel', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('nicochannel.news', Plugins.kind.ACTOR_CONFIG)
class NicochannelMonitorConfig(PagedFeedMonitorConfig):
    pass


@Plugins.register('nicochannel', Plugins.kind.ACTOR_ENTITY)
@Plugins.register('nicochannel.news', Plugins.kind.ACTOR_ENTITY)
class NicochannelMonitorEntity(PagedFeedMonitorEntity):
    url: str
    """url of a nicochannel.jp user or a fanclub domain"""
    update_interval: PositiveFloat = 900
    """how often the monitored channel will be checked, in seconds"""
    adjust_update_interval: bool = Field(exclude=True, default=True)
    """api endpoints doesn't use caching headers"""
    current_context: Optional['LivePageContext'] = Field(exclude=True, default=None)
    """internal variable used to pass parameter to super().get_records()"""


@dataclass
class PageContext:
    page: int
    per_page: int
    max_page: Optional[int] = None


@dataclass
class LivePageContext(PageContext):
    live_type: int = 1


@Plugins.register('nicochannel.news', Plugins.kind.ACTOR)
class NicochannelNewsMonitor(PagedFeedMonitor):
    """
    Monitor Nicochannel fanclub NEWS tab

    Monitors fanclub for new posts on the NEWS tab. Example of supported url:

    - `https://nicochannel.jp/creatorname`

    """

    def __init__(self, conf: NicochannelMonitorConfig, entities: Sequence[NicochannelMonitorEntity],
                 ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)
        self.entities: Mapping[str, NicochannelMonitorEntity]  # type: ignore
        self.site_cache: Dict[str, FanclubInfo] = {}

    async def get_info(self, entity: NicochannelMonitorEntity, client: HttpClient) -> Optional['FanclubInfo']:
        if entity.name in self.site_cache:
            return self.site_cache[entity.name]
        endpoint = await self.fetch_info(entity, client)
        if endpoint is not None:
            self.site_cache[entity.name] = endpoint
        return endpoint

    async def fetch_info(self, entity: NicochannelMonitorEntity, client: HttpClient) -> Optional['FanclubInfo']:
        r = NicochannelUrl.fanclub_id(entity.url)
        data = await self.request_json_endpoint(entity, client, r)
        if data is None:
            self.logger.warning(f'[{entity.name}] failed to fetch fanclub id')
            return None
        fanclub_id = parse_fanclub_id(data)
        if fanclub_id is None:
            self.logger.warning(f'[{entity.name}] failed to parse fanclub id')
            self.logger.debug(f'[{entity.name}] raw response: {data}')
            return None
        endpoint = NicochannelUrl(fanclub_id)
        r = endpoint.fanclub_info()
        data = await self.request_json_endpoint(entity, client, r)
        try:
            info = FanclubInfo.from_data(entity.url, fanclub_id, data)
            self.logger.debug(f'[{entity.name}] successfully fetched info for fanclub {fanclub_id} ({entity.url})')
            return info
        except Exception as e:
            self.logger.warning(f'[{entity.name}] failed to parse fanclub info: {e}')
            self.logger.debug(f'[{entity.name}] raw response: {data}', exc_info=True)
            return None

    async def handle_first_page(self, entity: NicochannelMonitorEntity,
                                client: HttpClient
                                ) -> Tuple[Optional[Sequence[NicochannelPostRecord]], Optional[PageContext]]:
        context = PageContext(page=1, per_page=6)
        return await self.handle_next_page(entity, client, context)

    async def handle_next_page(self, entity: NicochannelMonitorEntity,
                               client: HttpClient,
                               context: PageContext
                               ) -> Tuple[Optional[Sequence[NicochannelPostRecord]], Optional[PageContext]]:
        info = await self.get_info(entity, client)
        if info is None:
            return None, None
        endpoint = NicochannelUrl(info.fanclub_id)
        r = endpoint.article_pages(context.page, context.per_page)
        data = await self.request_json_endpoint(entity, client, r)
        articles = extract_articles(data)
        context.max_page = extract_articles_count(data)
        records = parse_articles(articles, info, with_prefix(self.logger, f'[{entity.name}] '))
        if context.max_page is None or context.max_page <= context.page:
            self.logger.debug(
                f'[{entity.name}] reached end of the feed. Current page: {context.page}, total pages: {context.max_page}')
            return records, None
        context.page += 1
        return records, context


@Plugins.register('nicochannel', Plugins.kind.ACTOR)
class NicochannelMonitor(NicochannelNewsMonitor):
    """
    Monitor Nicochannel fanclub

    Monitors fanclub for ongoing and upcoming lives. Example of supported url:

    - `https://nicochannel.jp/creatorname`

    """

    async def get_records(self, entity: NicochannelMonitorEntity,
                          client: HttpClient) -> List[NicochannelVideoRecord]:
        contexts = [
            LivePageContext(page=1, per_page=10, live_type=1),
            LivePageContext(page=1, per_page=6, live_type=2),
            LivePageContext(page=1, per_page=8, live_type=3),
            LivePageContext(page=1, per_page=8, live_type=4),
        ]
        combined_records: List[NicochannelVideoRecord] = []
        for context in contexts:
            entity.current_context = context
            records = await super().get_records(entity, client)
            self.logger.debug(f'[{entity.name}] partial update with {context} returned {len(records)} records')
            combined_records.extend(records)  # type: ignore
        return combined_records

    async def handle_first_page(self, entity: NicochannelMonitorEntity,
                                client: HttpClient
                                ) -> Tuple[Optional[Sequence[NicochannelVideoRecord]], Optional[LivePageContext]]:
        if entity.current_context is None:
            self.logger.exception(f'[{entity.name}] no current context passed, aborting update')
            return None, None
        return await self.handle_next_page(entity, client, entity.current_context)

    async def handle_next_page(self, entity: NicochannelMonitorEntity,
                               client: HttpClient,
                               context: LivePageContext
                               ) -> Tuple[Optional[Sequence[NicochannelVideoRecord]], Optional[LivePageContext]]:
        info = await self.get_info(entity, client)
        if info is None:
            return None, None
        endpoint = NicochannelUrl(info.fanclub_id)
        r = endpoint.live_pages(context.live_type, context.page, context.per_page)
        data = await self.request_json_endpoint(entity, client, r)
        videos = extract_videos(data)
        context.max_page = extract_videos_count(data)
        records = parse_videos(videos, info, with_prefix(self.logger, f'[{entity.name}] '))
        if context.max_page is None or context.max_page <= context.page or len(records) == 0:
            self.logger.debug(
                f'[{entity.name}] reached end of the feed. Current page: {context.page} with {len(records)} records, total pages: {context.max_page}')
            return records, None
        context.page += 1
        return records, context


class NicochannelUrl:

    def __init__(self, fanclub_id: int, fc_use_device: Optional[str] = None):
        self.site_id = str(fanclub_id)
        self.fc_use_device = fc_use_device or 'null'

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            'fc_site_id': self.site_id,
            'fc_use_device': self.fc_use_device,
            'Origin': 'https://nicochannel.jp',
            'Referer': 'https://nicochannel.jp'
        }

    @staticmethod
    def login() -> RequestDetails:
        url = f'https://api.nicochannel.jp/fc/fanclub_sites/1/login'
        headers = {
            'fc_site_id': 1,
            'fc_use_device': 'null',
            'Origin': 'https://nicochannel.jp',
            'Referer': 'https://nicochannel.jp'
        }
        return RequestDetails(url=url, headers=headers)

    @staticmethod
    def fanclub_id(current_site_domain: str) -> RequestDetails:
        url = 'https://api.nicochannel.jp/fc/content_providers/channel_domain'
        params = {'current_site_domain': current_site_domain}
        headers = {'Origin': 'https://nicochannel.jp', 'Referer': 'https://nicochannel.jp'}
        return RequestDetails(url=url, params=params, headers=headers)

    def fanclub_info(self) -> RequestDetails:
        url = f'https://api.nicochannel.jp/fc/fanclub_sites/{self.site_id}/page_base_info'
        return RequestDetails(url=url, headers=self._headers)

    def live_pages(self, live_type: int, page: int, per_page: Optional[int] = None) -> RequestDetails:
        url = f'https://api.nicochannel.jp/fc/fanclub_sites/{self.site_id}/live_pages'
        params = {'page': page, 'live_type': live_type, 'per_page': per_page}
        if 'per_page' in params and params.get('per_page') is None:
            params.pop('per_page')
        return RequestDetails(url=url, params=params, headers=self._headers)

    def video_pages(self, page: int, per_page: int = 6) -> RequestDetails:
        url = f'https://api.nicochannel.jp/fc/fanclub_sites/{self.site_id}/video_pages'
        params = {'page': page, 'per_page': per_page, 'sort': '-display_date'}
        return RequestDetails(url=url, params=params, headers=self._headers)

    def article_pages(self, page: int, per_page: int = 6) -> RequestDetails:
        url = f'https://api.nicochannel.jp/fc/fanclub_sites/{self.site_id}/article_themes/news/articles'
        params = {'page': page, 'per_page': per_page, 'sort': 'published_at_desc'}
        return RequestDetails(url=url, params=params, headers=self._headers)


def parse_fanclub_id(data: JSONType) -> Optional[int]:
    value = find_one(data, '$..fanclub_site.id')
    if isinstance(value, int):
        return value
    return None


class FanclubInfo(BaseModel):
    url: str
    fanclub_id: int
    fanclub_code: str
    name: str
    description: str
    avatar_url: str
    banner_url: str

    @classmethod
    def from_data(cls, url: str, fanclub_id: int, data: JSONType) -> 'FanclubInfo':
        result = find_one(data, '$.data.fanclub_site')
        if result is None:
            raise ValueError(f'failed to parse data into {cls.__name__}: no "fanclub_site" property')
        return cls.from_result(url, fanclub_id, result)

    @classmethod
    def from_result(cls, url: str, fanclub_id: int, result: JSONType) -> 'FanclubInfo':
        if not isinstance(result, dict):
            raise ValueError(f'unexpected result info structure, expected a dict, got {type(result)}')
        fanclub_code = result['fanclub_code']
        name = result['fanclub_site_name']
        description = result['description']
        avatar_url = result['favicon_url']
        banner_url = result['thumbnail_image_url']
        return cls(
            url=url,
            fanclub_id=fanclub_id,
            fanclub_code=fanclub_code,
            name=name,
            description=description,
            avatar_url=avatar_url,
            banner_url=banner_url
        )


def extract_articles_count(data: JSONType) -> Optional[int]:
    total = find_one(data, '$.data.article_theme.articles.total')
    if not isinstance(total, int):
        return None
    return total


def extract_articles(data: JSONType) -> List[JSONType]:
    items = find_one(data, '$.data.article_theme.articles.list')
    if not isinstance(items, list):
        return []
    return items


def parse_articles(items: List[JSONType], fanclub: FanclubInfo, logger: logging.Logger) -> List[NicochannelPostRecord]:
    records = []
    for item in items:
        try:
            record = parse_article(item, fanclub)
            records.append(record)
        except Exception as e:
            logger.warning(f'failed to parse article: {e}')
            logger.debug(f'raw article: {item}', exc_info=True)
    return records


def parse_article(item: JSONType, fanclub: FanclubInfo) -> NicochannelPostRecord:
    if not isinstance(item, dict):
        raise ValueError(f'unexpected format: expected dict, got {type(item)}')
    post_id = item['article_code']
    published = dateutil.parser.parse(item['publish_at'])
    return NicochannelPostRecord(
        post_id=post_id,
        url=f'{fanclub.url.rstrip("/")}/articles/news/{post_id}',
        title=item['article_title'],
        full_text=item['contents'] or '',
        published=published,
        thumbnail_url=item['thumbnail_url'],

        post_authorization=find_one(item, '$.article_authorization_type.authorization_name'),  # type: ignore
        post_category=find_one(item, '$.article_article_categories..category_name'),  # type: ignore
        post_status=find_one(item, '$.article_status.status_name'),  # type: ignore

        author=fanclub.name,
        avatar_url=fanclub.avatar_url,
        fanclub_id=fanclub.fanclub_id,
        fanclub_url=fanclub.url
    )


def extract_videos_count(data: JSONType) -> Optional[int]:
    total = find_one(data, '$.data.video_pages.total')
    if not isinstance(total, int):
        return None
    return total


def extract_videos(data: JSONType) -> List[JSONType]:
    items = find_one(data, '$.data.video_pages.list')
    if not isinstance(items, list):
        return []
    return items


def parse_videos(items: List[JSONType], fanclub: FanclubInfo, logger: logging.Logger) -> List[NicochannelVideoRecord]:
    records = []
    for item in items:
        try:
            record = parse_video(item, fanclub)
            records.append(record)
        except Exception as e:
            logger.warning(f'failed to parse video info: {e}')
            logger.debug(f'raw video info: {item}', exc_info=True)
    return records


def parse_video(item: JSONType, fanclub: FanclubInfo) -> NicochannelVideoRecord:
    if not isinstance(item, dict):
        raise ValueError(f'unexpected format: expected dict, got {type(item)}')
    video_id = item['content_code']
    published = dateutil.parser.parse(item['released_at'])
    scheduled = try_parse_date(item.get('live_scheduled_start_at'))
    length = find_one(item, '$.active_video_filename.length')
    return NicochannelVideoRecord(
        video_id=video_id,
        url=f'{fanclub.url.rstrip("/")}/{"live" if length is None else "video"}/{video_id}',
        title=item['title'],
        summary=item.get('description'),
        published=published,
        scheduled=scheduled,
        thumbnail_url=item['thumbnail_url'],
        length=length,  # type: ignore

        author=fanclub.name,
        avatar_url=fanclub.avatar_url,
        fanclub_id=fanclub.fanclub_id,
        fanclub_url=fanclub.url,

        is_live=not bool(item.get('live_finished_at')),
        is_upcoming=scheduled is not None and not bool(item.get('live_started_at'))
    )

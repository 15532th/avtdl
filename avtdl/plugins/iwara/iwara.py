import datetime
from abc import ABC
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import dateutil.parser
from pydantic import Field, PositiveFloat

from avtdl.core.formatters import Fmt
from avtdl.core.interfaces import Record
from avtdl.core.monitors import PagedFeedMonitor, PagedFeedMonitorConfig, PagedFeedMonitorEntity
from avtdl.core.plugins import Plugins
from avtdl.core.request import HttpClient
from avtdl.core.utils import JSONType, find_all


class IwaraPostRecord(Record, ABC):
    url: str
    """link to the post"""
    title: str
    """post title"""
    published: datetime.datetime
    """post publish date"""
    name: str
    """uploader visible name"""
    username: str
    """uploader username"""
    avatar_url: Optional[str] = None
    """link to the picture used as the uploader avatar"""
    private: bool
    """whether post is private or unlisted"""
    tags: List[str] = []
    """list of tags of the post"""

    def get_uid(self) -> str:
        return f'{self.url}'


@Plugins.register('iwr', Plugins.kind.ASSOCIATED_RECORD)
class IwaraVideoRecord(IwaraPostRecord):
    thumbnail_url: str
    """url of the video thumbnail"""
    preview_url: Optional[str]
    """url of the animated preview, if available"""
    size: int
    """video size, in bytes"""
    duration: int
    """video duration, in seconds"""

    def __str__(self) -> str:
        return f'{self.url}\n[{self.name}] {self.title}'

    def __repr__(self) -> str:
        template = '[{}] {} ({})'
        return template.format(self.name, self.title[:60], self.url)

    def as_embed(self) -> dict:
        profile_url = f'https://www.iwara.tv/profile/{self.username}'
        footer = '{}, {}'.format(Fmt.duration(self.duration), Fmt.size(self.size))
        return {
            'title': self.title,
            'url': self.url,
            'description': None,
            'image': {'url': self.thumbnail_url, '_preview': self.preview_url},
            'color': None,
            'author': {'name': self.name, 'url': profile_url, 'icon_url': self.avatar_url},
            'timestamp': self.published.isoformat(),
            'footer': {'text': footer},
            'fields': []
        }


@Plugins.register('iwr', Plugins.kind.ACTOR_CONFIG)
class IwaraMonitorConfig(PagedFeedMonitorConfig):
    pass


@Plugins.register('iwr', Plugins.kind.ACTOR_ENTITY)
class IwaraMonitorEntity(PagedFeedMonitorEntity):
    url: str = Field(default=None, exclude=True)
    """url field is unused"""
    update_interval: PositiveFloat = 3600
    """how often update should happen, in seconds"""


@dataclass
class Context:
    page: int


class IsEmbeddedVideoError(Exception):
    """Raised when a parsed post embeds link to Youtube"""


@Plugins.register('iwr', Plugins.kind.ACTOR)
class IwaraMonitor(PagedFeedMonitor):
    """
    Monitor Iwara uploads

    Checks Latest Videos feed in reverse chronological order,
    does not retrieve descriptions and comments.
    Posts embedding videos from Youtube are skipped.
    Does not provide filtering capabilities, selecting records
    for further processing can be accomplished by using "filter.match" or other filters.
    """

    async def handle_first_page(self,
                                entity: IwaraMonitorEntity,
                                client: HttpClient) -> Tuple[Optional[Sequence[Record]], Optional[Context]]:
        data = await self._fetch_data(entity, client, None)
        records = self._parse_data(data)
        return records, Context(page=1)

    async def handle_next_page(self,
                               entity: IwaraMonitorEntity,
                               client: HttpClient,
                               context: Optional[Context]) -> Tuple[Optional[Sequence[Record]], Optional[Context]]:
        if context is None:
            return None, None
        data = await self._fetch_data(entity, client, context)
        records = self._parse_data(data)
        if records is not None:
            context.page += 1
        else:
            context = None
        return records, context

    async def _fetch_data(self,
                          entity: IwaraMonitorEntity,
                          client: HttpClient,
                          context: Optional[Context]) -> Optional[JSONType]:
        url = 'https://api.iwara.tv/videos'
        params = {'rating': 'all', 'sort': 'date'}
        if context:
            params['page'] = str(context.page)
        data = await self.request_json(url, entity, client, params=params)
        return data

    def _parse_data(self, data: JSONType) -> Optional[Sequence[IwaraVideoRecord]]:
        if data is None:
            return None
        if not isinstance(data, dict) or 'results' not in data:
            self.logger.warning(f'failed to parse API response: no results')
            self.logger.debug(f'raw response: {data}')
            return None
        results = data['results']
        records = []
        for result in results:
            try:
                record = self._parse_record(result)
            except IsEmbeddedVideoError as e:
                self.logger.debug(f'skipping embedded video: {e}')
            except Exception as e:
                self.logger.warning(f'failed to parse record: {type(e)}: {e}')
                self.logger.debug(f'raw item: {result}', exc_info=e)
            else:
                records.append(record)
        return records

    def _parse_record(self, data: JSONType) -> IwaraVideoRecord:
        if not isinstance(data, dict):
            raise ValueError(f'unexpected format')
        if 'user' not in data:
            raise ValueError('no user info')
        if data.get('embedUrl') is not None:
            raise IsEmbeddedVideoError(data['embedUrl'])
        if 'file' not in data:
            raise ValueError('no video info')

        user = data['user']
        if user.get('avatar') is None:
            avatar_url = None
        else:
            avatar_path = user['avatar']['name']
            avatar_id = user['avatar']['id']
            avatar_url = f'https://i.iwara.tv/image/avatar/{avatar_id}/{avatar_path}'

        tags = [str(tag) for tag in find_all(data, '$.tags[*].id')]

        file = data['file']
        file_id = file['id']
        thumbnail_id = int(data['thumbnail'])

        post_id = data['id']
        post_slug = data['slug'] or ''

        record = IwaraVideoRecord(
            url=f'https://www.iwara.tv/video/{post_id}/{post_slug}',
            title=data['title'],
            private=bool(data['private'] or data['unlisted'] or False),
            published=dateutil.parser.parse(data['createdAt']),
            tags=tags,
            name=user['name'],
            username=user['username'],
            avatar_url=avatar_url,
            duration=file.get('duration'),
            size=file['size'],
            thumbnail_url=f'https://i.iwara.tv/image/thumbnail/{file_id}/thumbnail-{thumbnail_id:02}.jpg',
            preview_url=f'https://i.iwara.tv/image/original/{file_id}/preview.webp'
        )
        return record

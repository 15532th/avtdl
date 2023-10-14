import datetime
from textwrap import shorten
from typing import Sequence, Optional, Any, Dict

import aiohttp
import feedparser
from pydantic import ValidationError, ConfigDict

from core.interfaces import Record, MAX_REPR_LEN, TextRecord
from core.monitors import BaseFeedMonitor, BaseFeedMonitorEntity, BaseFeedMonitorConfig
from core.plugins import Plugins
from core.utils import make_datetime


class GenericRSSRecord(Record):
    model_config = ConfigDict(extra='allow')

    uid: str
    url: str
    summary: str
    author: str = ''
    title: str = ''
    published: datetime.datetime

    def __str__(self):
        second_line = f'{self.author}: {self.title}\n' if self.author and self.title else ''
        summary = shorten(self.summary, MAX_REPR_LEN * 2)
        return f'[{self.published}] {self.url}\n{second_line}{summary}'

    def __repr__(self):
        title = shorten(self.title, MAX_REPR_LEN)
        return f'GenericRSSRecord(updated="{self.published.isoformat()}", url="{self.url}", title="{title}")'


@Plugins.register('generic_rss', Plugins.kind.ACTOR_CONFIG)
class GenericRSSMonitorConfig(BaseFeedMonitorConfig):
    pass

@Plugins.register('generic_rss', Plugins.kind.ACTOR_ENTITY)
class GenericRSSMonitorEntity(BaseFeedMonitorEntity):
    pass

@Plugins.register('generic_rss', Plugins.kind.ACTOR)
class GenericRSSMonitor(BaseFeedMonitor):
    async def get_records(self, entity: BaseFeedMonitorEntity, session: aiohttp.ClientSession) -> Sequence[Record]:
        raw_feed = await self._get_feed(entity, session)
        if raw_feed is None:
            return []
        records = self._parse_entries(raw_feed)
        return records

    def get_record_id(self, record: GenericRSSRecord) -> str:
        return record.uid

    async def _get_feed(self, entity: BaseFeedMonitorEntity, session: aiohttp.ClientSession) -> Optional[feedparser.FeedParserDict]:
        response = await self.request(entity.url, entity, session)
        if response is None:
            return None
        text = await response.text()
        try:
            feed = feedparser.parse(text, response_headers=response.headers)
            if feed.get('entries') is not None:
                return feed
            else:
                from pprint import pformat

                self.logger.debug(f'feed for {entity.url} has no entries, probably broken:')
                self.logger.debug(pformat(feed))
                raise Exception(f'got broken feed while fetching {entity.url}')
        except Exception as e:
            self.logger.warning('Exception while updating rss feed: {}'.format(e))
            return None

    @staticmethod
    def _get_uid(entry: feedparser.FeedParserDict) -> str:
        # https://www.詹姆斯.com/blog/2006/08/rss-dup-detection
        guid = entry.get('guid') or entry.get('id')
        if guid is not None:
            return guid
        link = entry.get('link') or entry.get('href', '')
        title = entry.get('title', '')
        summary = entry.get('summary', '')
        if any([link, title, summary]):
            return link + (title or summary)
        return TextRecord(text=str(entry)).hash() #short way to write sha1(str(entry))

    @classmethod
    def _parse_entry(cls, entry: feedparser.FeedParserDict) -> GenericRSSRecord:
        # base properties with substitutions defined for feedparser.FeedParserDict
        # keymap = {
        #     'channel': 'feed',
        #     'items': 'entries',
        #     'guid': 'id',
        #     'date': 'updated',
        #     'date_parsed': 'updated_parsed',
        #     'description': ['summary', 'subtitle'],
        #     'description_detail': ['summary_detail', 'subtitle_detail'],
        #     'url': ['href'],
        #     'modified': 'updated',
        #     'modified_parsed': 'updated_parsed',
        #     'issued': 'published',
        #     'issued_parsed': 'published_parsed',
        #     'copyright': 'rights',
        #     'copyright_detail': 'rights_detail',
        #     'tagline': 'subtitle',
        #     'tagline_detail': 'subtitle_detail',
        # }

        parsed: Dict[str, Any] = {}

        parsed['uid'] = cls._get_uid(entry)
        parsed['url'] = entry.pop('link', '') or entry.pop('href', '') or entry.pop('url', '')
        parsed['summary'] = entry.pop('summary', '')
        parsed['author'] = entry.pop('author', '')
        parsed['title'] = entry.pop('title', '')

        parsed['published'] = make_datetime(entry.pop('published_parsed'))
        entry.pop('published')
        updated = entry.pop('updated_parsed', None)
        if updated is not None:
            parsed['updated'] = make_datetime(updated)
            entry.pop('updated', '')

        for key, value in entry.items():
            parsed[key] = value

        record = GenericRSSRecord(**parsed)
        return record

    def _parse_entries(self, feed: feedparser.FeedParserDict) -> Sequence[GenericRSSRecord]:
        records = []
        for entry in feed['entries']:
            try:
                record = self._parse_entry(entry)
            except KeyError as e:
                self.logger.warning(f'rss parser failed to construct record from rss entry {entry}: missing  necessarily field {e}')
                continue
            except ValidationError as e:
                self.logger.warning(f'rss parser failed to construct record from rss entry {entry}: {e}')
                continue
            records.append(record)
        return records

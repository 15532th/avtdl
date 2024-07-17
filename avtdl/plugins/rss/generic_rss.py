import datetime
from textwrap import shorten
from typing import Any, Dict, Optional, Sequence, Union

import aiohttp
import feedparser
from pydantic import ConfigDict, ValidationError

from avtdl.core.interfaces import MAX_REPR_LEN, Record, TextRecord
from avtdl.core.monitors import BaseFeedMonitor, BaseFeedMonitorConfig, BaseFeedMonitorEntity
from avtdl.core.plugins import Plugins
from avtdl.core.utils import html_to_text, make_datetime


@Plugins.register('generic_rss', Plugins.kind.ASSOCIATED_RECORD)
class GenericRSSRecord(Record):
    """
    Represents RSS feed entry

    Might contain additional fields if they are present in the feed.
    """
    model_config = ConfigDict(extra='allow')

    uid: str
    """value that is unique for this entry of RSS feed"""
    url: str
    """"href" or "link" field value of this entry"""
    summary: str
    """"summary" or "description" field value of this entry"""
    author: str = ''
    """"author" field value. Might be empty"""
    title: str = ''
    """"title" field value. Might be empty"""
    published: datetime.datetime
    """"published" or "issued" field value of this entry"""

    def __str__(self):
        second_line = f'[{self.published}] {self.author}: {self.title}\n' if self.author or self.title else ''
        summary = html_to_text(self.summary)
        summary = shorten(summary, MAX_REPR_LEN * 5)
        return f'{self.url}\n{second_line}{summary}'

    def __repr__(self):
        title = shorten(self.title, MAX_REPR_LEN)
        return f'GenericRSSRecord(updated="{self.published.isoformat()}", url="{self.url}", title="{title}")'

    def get_uid(self) -> str:
        return self.uid


@Plugins.register('generic_rss', Plugins.kind.ACTOR_CONFIG)
class GenericRSSMonitorConfig(BaseFeedMonitorConfig):
    pass

@Plugins.register('generic_rss', Plugins.kind.ACTOR_ENTITY)
class GenericRSSMonitorEntity(BaseFeedMonitorEntity):
    pass

@Plugins.register('generic_rss', Plugins.kind.ACTOR)
class GenericRSSMonitor(BaseFeedMonitor):
    """
    RSS feed monitor

    Monitors RSS feed for new entries. Will attempt to adjust
    update interval based on HTTP response headers.

    Depending on a specific feed format, fields names and content
    might vary greatly. Commonly present standardized fields are
    `url`, `title` and `author`, though they might be empty
    in some feeds.

    Before defining a command to be executed for records of a newly
    added feed it is recommended to inspect the feed entity content by
    forwarding records in a file in JSON format using `to_file` plugin.

    Normally feeds have some kind of value to uniquely identify
    feed entries, but in case there is none, parser will attempt
    to create one by combining `link` and `title` or `summary` fields.
    """

    async def get_records(self, entity: BaseFeedMonitorEntity, session: aiohttp.ClientSession) -> Sequence[GenericRSSRecord]:
        raw_feed = await self._get_feed(entity, session)
        if raw_feed is None:
            return []
        records = self._parse_entries(raw_feed)
        return records

    async def _get_feed(self, entity: BaseFeedMonitorEntity, session: aiohttp.ClientSession) -> Optional[feedparser.FeedParserDict]:
        response = await self.request_raw(entity.url, entity, session)
        if response is None:
            return None
        raw_text = await response.text()

        # Charset detection used by aiohttp fails on RSS feed often enough,
        # and feedparser is capable of taking bytes as input and detecting
        # encoding itself. Since aiohttp.ClientResponse does not provide
        # a way to get response body as raw bytes multiple times, encode
        # text back to bytes instead.
        # Skip it for UTF-8 since it's most likely to be correct.
        text: Union[str, bytes] = raw_text
        if not response.get_encoding().lower() in ('utf-8', 'utf8'):
            self.logger.debug(f'[{entity.name}] detected encoding is {response.get_encoding()}, reverting to bytes')
            try:
                text = raw_text.encode(response.get_encoding())
            except Exception as e:
                self.logger.exception(f'[{entity.name}] failed to encode raw feed back to bytes: {e}')

        # Set "content-location" header to allow feedparser to resolve
        # relative links in feed entries. Header name must be
        # all lowercase because this is how it is checked in feedparser,
        # and it uses regular case-sensitive dict for headers internally
        response_headers = response.headers.copy()
        if 'Content-Location' in response.headers:
            response_headers['content-location'] = response_headers['Content-Location']
        else:
            response_headers['content-location'] = entity.url

        try:
            feed = feedparser.parse(text, response_headers=response_headers, resolve_relative_uris=True)
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
        records = records[::-1]  # records are ordered from new to old in the feed, reorder in chronological order
        return records

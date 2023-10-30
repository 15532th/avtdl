import asyncio
import datetime
import re
from textwrap import shorten
from typing import List, Optional, Sequence

import aiohttp
import lxml.html
from dateutil import parser
from pydantic import ConfigDict

from core import utils
from core.interfaces import MAX_REPR_LEN, Record, FilterEntity, Filter
from core.monitors import BaseFeedMonitor, BaseFeedMonitorConfig, BaseFeedMonitorEntity
from core.plugins import Plugins
from plugins.filters.filters import EmptyFilterConfig


class NitterRecord(Record):
    model_config = ConfigDict(extra='allow')

    retweet_header: Optional[str] = None
    reply_header: Optional[str] = None
    url: str
    author: str
    username: str
    published: datetime.datetime
    text: str
    html: str = ''
    attachments: List[str] = []
    quote: Optional['NitterQuoteRecord'] = None

    def __str__(self):
        elements = []
        elements.append(self.url)
        if self.retweet_header:
            elements.append(self.retweet_header)
        if self.reply_header:
            elements.append(self.reply_header)
        elements.append(f'{self.author} ({self.username}):')
        elements.append(self.text)
        if self.attachments:
            elements.append('\n'.join(self.attachments))
        if self.quote:
            elements.append('\nReferring to ')
            elements.append(str(self.quote))
        return '\n'.join(elements)

    def __repr__(self):
        return f'NitterRecord(author="{self.author}", url="{self.url}", text="{shorten(self.text, MAX_REPR_LEN)}")'

class NitterQuoteRecord(NitterRecord):
    quote: None = None


@Plugins.register('nitter', Plugins.kind.ACTOR_CONFIG)
class NitterMonitorConfig(BaseFeedMonitorConfig):
    pass

@Plugins.register('nitter', Plugins.kind.ACTOR_ENTITY)
class NitterMonitorEntity(BaseFeedMonitorEntity):
    update_interval: float = 1800

    max_continuation_depth: int = 10
    next_page_delay: float = 1
    allow_discontiniuty: bool = False # store already fetched records on failure to load one of older pages
    fetch_until_the_end_of_feed_mode: bool = False


def get_text_content(element: lxml.html.HtmlElement) -> str:
    def handle_element(element: lxml.html.HtmlElement) -> str:
        if isinstance(element, str):
            return element
        link = element.attrib.get('href')
        if link is None:
            return element.text
        if link.find('/search') > -1 and element.text.startswith('#'): # hashtag
            return element.text
        return link

    strings = [handle_element(child) for child in element.xpath("node()")]
    text = ''.join(strings)
    test = element.text_content()
    return text

def get_html_content(element: lxml.html.HtmlElement) -> str:
    return ''.join([x if isinstance(x, str) else lxml.etree.tounicode(x) for x in element.xpath("node()")])


@Plugins.register('nitter', Plugins.kind.ACTOR)
class NitterMonitor(BaseFeedMonitor):

    # nitter.net instance returns 403 in absense of this two headers and if UserAgent contains "python-requests"
    HEADERS = {'Accept-Language': 'en-US', 'Accept-Encoding': 'gzip, deflate',
               'Cookie': 'Cookie: hideBanner=on; hidePins=on; replaceTwitter=; replaceYouTube=; replaceReddit='}

    async def get_records(self, entity: NitterMonitorEntity, session: aiohttp.ClientSession) -> Sequence[Record]:
        raw_page = await self._get_user_page(entity, session)
        if raw_page is None:
            return []
        page = self._parse_html(raw_page, entity.url)
        records = current_page_records = self._parse_entries(page)
        next_page_url = self._get_continuation_url(page)

        if entity.fetch_until_the_end_of_feed_mode:
            self.logger.info(f'[{entity.name}] "fetch_until_the_end_of_feed_mode" setting is enabled, will keep loading through already seen pages until the end. Disable it in config after it succeeds once')

        current_page = 1
        while True:
            if next_page_url is None:
                self.logger.debug(f'[{entity.name}] no continuation link on {current_page - 1} page, end of feed reached')
                entity.fetch_until_the_end_of_feed_mode = False
                break
            if not entity.fetch_until_the_end_of_feed_mode:
                if current_page > entity.max_continuation_depth:
                    self.logger.info(f'[{entity.name}] reached continuation limit of {entity.max_continuation_depth}, aborting update')
                    break
                if not all(self.record_is_new(record, entity) for record in current_page_records):
                    self.logger.debug(f'[{entity.name}] found already stored records on {current_page - 1} page')
                    break
            self.logger.debug(f'[{entity.name}] all records on page {current_page - 1} are new, loading next one')
            raw_page = await utils.request(next_page_url, session, self.logger, headers=self.HEADERS, retry_times=3, retry_multiplier=2, retry_delay=5)
            if raw_page is None:
                if entity.allow_discontiniuty or entity.fetch_until_the_end_of_feed_mode:
                    # when unable to load _all_ new records, return at least current progress
                    return records
                else:
                    # when unable to load _all_ new records, throw away all already parsed and return nothing
                    # to not cause discontinuity in stored data
                    return []
            page = self._parse_html(raw_page, entity.url)
            current_page_records = self._parse_entries(page) or [] # perhaps should also issue total failure if no records on the page?
            records.extend(current_page_records)
            next_page_url = self._get_continuation_url(page)

            current_page += 1
            await asyncio.sleep(entity.next_page_delay)

        return records

    def get_record_id(self, record: NitterRecord) -> str:
        return record.url

    async def _get_user_page(self, entity: NitterMonitorEntity, session: aiohttp.ClientSession) -> Optional[str]:
        response = await self.request(entity.url, entity, session, headers=self.HEADERS)
        text = await response.text() if response else None
        return text

    def _parse_entries(self, page: lxml.html.HtmlElement) -> List[NitterRecord]:
        try:
            posts_section = self._parse_timeline(page)
        except Exception as e:
            self.logger.debug(f'error parsing nitter page: {e}')
            return []
        records = []
        for post_node in posts_section:
            try:
                record = self._parse_post(post_node)
            except Exception as e:
                self.logger.exception(f'error parsing a post: {e}')
                self.logger.debug(f'raw post: {get_html_content(post_node)}')
            else:
                records.append(record)
        return records

    def _parse_html(self, raw_page: str, base_url: str) -> lxml.html.HtmlElement:
        root = lxml.html.fromstring(raw_page, base_url=base_url)
        root.make_links_absolute(base_url)
        return root

    def _parse_timeline(self, page: lxml.html.HtmlElement) -> Sequence[lxml.html.HtmlElement]:
        posts = page.xpath(".//*[@class='timeline-item ']") # note trailing space in class name
        return posts

    def _get_continuation_url(self, page: lxml.html.HtmlElement) -> Optional[str]:
        [continuation] = page.xpath(".//*[@class='show-more']/a/@href") or [None]
        return continuation

    def _parse_attachments(self, raw_attachments: lxml.html.HtmlElement) -> List[str]:
        links = raw_attachments.xpath(".//a/@href")
        return links

    def _parse_quote(self, raw_quote: lxml.html.HtmlElement) -> Optional[NitterQuoteRecord]:
        url = raw_quote.xpath(".//*[@class='quote-link']/@href")[0]
        url = re.sub('#m$', '', url)
        author = raw_quote.xpath(".//*[@class='fullname']/@title")[0]
        username = raw_quote.xpath(".//*[@class='username']/@title")[0]

        published_text = raw_quote.xpath(".//*[@class='tweet-date']/a/@title")[0]
        published = parser.parse(published_text.replace('·', ''))

        [post_body] = raw_quote.xpath(".//*[@class='quote-text']") or [None]
        if post_body is None:
            text = html = ''
        else:
            text = get_text_content(post_body)
            html = get_html_content(post_body)

        [raw_attachments] = raw_quote.xpath(".//*[@class='quote-media-container']/*[@class='attachments']") or [None]
        attachments = self._parse_attachments(raw_attachments) if raw_attachments is not None else []

        return NitterQuoteRecord(url=url, author=author, username=username, published=published, text=text, html=html, attachments=attachments)

    def _parse_post(self, raw_post: lxml.html.HtmlElement) -> NitterRecord:
        retweet_header = ''.join(element.text_content() for element in raw_post.xpath(".//*[@class='retweet-header']")).lstrip() or None
        reply_header = ''.join(element.text_content() for element in raw_post.xpath(".//*[@class='tweet-body']/*[@class='replying-to']")).lstrip() or None

        url = raw_post.xpath(".//*[@class='tweet-link']/@href")[0]
        url = re.sub('#m$', '', url)
        author = raw_post.xpath(".//*[@class='fullname']/@title")[0]
        username = raw_post.xpath(".//*[@class='username']/@title")[0]

        published_text = raw_post.xpath(".//*[@class='tweet-date']/a/@title")[0]
        published = parser.parse(published_text.replace('·', ''))

        post_body = raw_post.xpath(".//*[@class='tweet-content media-body']")[0]
        text = get_text_content(post_body)
        html = get_html_content(post_body)

        [raw_attachments] = raw_post.xpath("(.//*[@class='attachments'])[1]") or [None]
        attachments = self._parse_attachments(raw_attachments) if raw_attachments is not None else []

        [raw_quote] = raw_post.xpath(".//*[@class='quote quote-big']") or [None]
        quote = self._parse_quote(raw_quote) if raw_quote is not None else None

        return NitterRecord(url=url,
                            author=author,
                            username=username,
                            published=published,
                            text=text,
                            html=html,
                            retweet_header=retweet_header,
                            reply_header=reply_header,
                            attachments=attachments,
                            quote=quote
                            )


@Plugins.register('filter.nitter.pick', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('filter.nitter.drop', Plugins.kind.ACTOR_CONFIG)
class NitterFilterConfig(EmptyFilterConfig):
    pass

@Plugins.register('filter.nitter.pick', Plugins.kind.ACTOR_ENTITY)
@Plugins.register('filter.nitter.drop', Plugins.kind.ACTOR_ENTITY)
class NitterFilterEntity(FilterEntity):
    retweet: bool = False
    reply: bool = False
    quote: bool = False
    regular_tweet: bool = False
    author: Optional[str] = None
    username: Optional[str] = None



@Plugins.register('filter.nitter.pick', Plugins.kind.ACTOR)
class NitterFilterPick(Filter):

    def __init__(self, config: NitterFilterConfig, entities: Sequence[NitterFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: NitterFilterEntity, record: NitterRecord) -> Optional[NitterRecord]:
        if not isinstance(record, NitterRecord):
            self.logger.debug(f'[{entity.name}] record dropped due to unsupported type, expected NitterRecord, got {type(record)}')
            return None
        if entity.retweet and record.retweet_header is not None:
            return record
        if entity.reply and record.reply_header is not None:
            return record
        if entity.quote and not record.quote is not None:
            return record
        if entity.regular_tweet:
            if all([x is None for x in [record.reply_header, record.retweet_header, record.quote]]):
                return record
        if entity.author and record.author.find(entity.author) > -1:
            return record
        if entity.username and record.username.find(entity.username) > -1:
            return record
        return None

@Plugins.register('filter.nitter.drop', Plugins.kind.ACTOR)
class NitterFilterDrop(Filter):

    def __init__(self, config: NitterFilterConfig, entities: Sequence[NitterFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: NitterFilterEntity, record: NitterRecord) -> Optional[NitterRecord]:
        if not isinstance(record, NitterRecord):
            self.logger.debug(f'[{entity.name}] record dropped due to unsupported type, expected NitterRecord, got {type(record)}')
            return None
        if entity.retweet and record.retweet_header is not None:
            return None
        if entity.reply and record.reply_header is not None:
            return None
        if entity.quote and not record.quote is not None:
            return None
        if entity.regular_tweet:
            if all([x is None for x in [record.reply_header, record.retweet_header, record.quote]]):
                return None
        if entity.author and record.author.find(entity.author) > -1:
            return None
        if entity.username and record.username.find(entity.username) > -1:
            return None
        return record

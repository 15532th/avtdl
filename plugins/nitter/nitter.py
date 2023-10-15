import datetime
import re
from textwrap import shorten
from typing import List, Optional, Sequence

import aiohttp
import lxml.html
from dateutil import parser
from pydantic import ConfigDict

from core import utils
from core.interfaces import MAX_REPR_LEN, Record
from core.monitors import BaseFeedMonitor, BaseFeedMonitorConfig, BaseFeedMonitorEntity
from core.plugins import Plugins


class NitterRecord(Record):
    model_config = ConfigDict(extra='allow')

    header: Optional[str] = None
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
        if self.header:
            elements.append(self.header)
        elements.append(f'{self.author} ({self.username}):')
        elements.append(self.text)
        if self.attachments:
            elements.append('\n'.join(self.attachments))
        if self.quote:
            elements.append('Referring to ')
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
    async def get_records(self, entity: NitterMonitorEntity, session: aiohttp.ClientSession) -> Sequence[Record]:
        raw_page = await self._get_user_page(entity, session)
        if raw_page is None:
            return []
        records = self._parse_entries(raw_page, entity.url)
        return records

    def get_record_id(self, record: NitterRecord) -> str:
        return record.url

    async def _get_user_page(self, entity: NitterMonitorEntity, session: aiohttp.ClientSession) -> Optional[str]:
        # nitter.net instance returns 403 in absense of this two headers and if UserAgent contains "python-requests"
        headers={'Accept-Language': 'en-US', 'Accept-Encoding': 'gzip, deflate', 'Cookie':  'Cookie: hideBanner=on; hidePins=on; replaceTwitter=; replaceYouTube=; replaceReddit='}
        response = await self.request(entity.url, entity, session, headers=headers)
        text = await response.text() if response else None
        return text

    def _parse_entries(self, raw_page: Optional[str], base_url: str) -> Sequence[NitterRecord]:
        if raw_page is None:
            return []
        try:
            posts_section = self._parse_timeline(raw_page, base_url)
        except Exception as e:
            self.logger.debug(f'error parsing nitter page: {e}')
            return []
        records = []
        for post_node in posts_section:
            try:
                record = self._parse_post(post_node)
            except Exception as e:
                self.logger.exception(f'error parsing a post: {e}')
                self.logger.debug(f'raw')
            else:
                records.append(record)
        return records

    def _parse_timeline(self, raw_page: str, base_url: str) -> Sequence[lxml.html.HtmlElement]:
        root = lxml.html.fromstring(raw_page, base_url=base_url)
        root.make_links_absolute(base_url)
        posts = root.find_class('timeline-item') # it is actually "timeline-item " in html, find_class() seems to trim trailing whitespace
        return posts

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

        post_body = raw_quote.xpath(".//*[@class='quote-text']")[0]
        text = get_text_content(post_body)
        html = get_html_content(post_body)

        [raw_attachments] = raw_quote.xpath(".//*[@class='quote-media-container']/*[@class='attachments']") or [None]
        attachments = self._parse_attachments(raw_attachments) if raw_attachments is not None else []

        return NitterQuoteRecord(url=url, author=author, username=username, published=published, text=text, html=html, attachments=attachments)

    def _parse_post(self, raw_post: lxml.html.HtmlElement) -> NitterRecord:
        header = ''.join(element.text_content() for element in raw_post.xpath(".//*[@class='retweet-header'] | .//*[@class='replying-to']")).lstrip() or None

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


        return NitterRecord(url=url, author=author, username=username, published=published, text=text, html=html, header=header, media=attachments, quote=quote)

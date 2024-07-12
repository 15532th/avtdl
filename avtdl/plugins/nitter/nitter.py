import datetime
import re
from textwrap import shorten
from typing import Any, List, Optional, Sequence, Tuple
from urllib import parse as urllibparse

import aiohttp
import lxml.html
from dateutil import parser
from pydantic import ConfigDict

from avtdl.core import utils
from avtdl.core.interfaces import Filter, FilterEntity, MAX_REPR_LEN, Record
from avtdl.core.monitors import PagedFeedMonitor, PagedFeedMonitorConfig, PagedFeedMonitorEntity
from avtdl.core.plugins import Plugins
from avtdl.plugins.filters.filters import EmptyFilterConfig


@Plugins.register('nitter', Plugins.kind.ASSOCIATED_RECORD)
@Plugins.register('filter.nitter.pick', Plugins.kind.ASSOCIATED_RECORD)
@Plugins.register('filter.nitter.drop', Plugins.kind.ASSOCIATED_RECORD)
class NitterRecord(Record):
    """
    Single post as parsed from Nitter instance

    Depending on the tweet type (regular, retweet, reply, quote) some fields might be empty
    """
    model_config = ConfigDict(extra='allow')

    retweet_header: Optional[str] = None
    """text line saying this is a retweet"""
    reply_header: Optional[str] = None
    """text line saying this tweet is a reply"""
    url: str
    """tweet url"""
    author: str
    """user's visible name"""
    username: str
    """user's handle"""
    avatar_url: Optional[str] = None
    """link to the picture used as the user's avatar"""
    published: datetime.datetime
    """tweet timestamp"""
    text: str
    """tweet text with stripped formatting"""
    html: str = ''
    """tweet text as raw html"""
    attachments: List[str] = []
    """list of links to attached images or video thumbnails"""
    quote: Optional['NitterQuoteRecord'] = None
    """Nested NitterRecord containing tweet being quited"""

    def __str__(self):
        elements = []
        elements.append(self.url)
        if self.retweet_header:
            elements.append(self.retweet_header)
        if self.reply_header:
            elements.append(self.reply_header)
        elements.append(f'{self.author} ({self.username}) [{self.published.strftime("%Y-%m-%d %H:%M:%S")}]:')
        elements.append(self.text)
        if self.attachments:
            elements.append('\n'.join(self.attachments))
        if self.quote:
            elements.append('\nReferring to ')
            elements.append(str(self.quote))
        return '\n'.join(elements)

    def __repr__(self):
        return f'NitterRecord(author="{self.author}", url="{self.url}", text="{shorten(self.text, MAX_REPR_LEN)}")'

    def get_uid(self) -> str:
        return self.url

    def discord_embed(self) -> List[dict]:
        text_items = [self.text]
        if len(self.attachments) > 1:
            text_items.extend(self.attachments)
        if self.quote:
            text_items.append('\nReferring to ')
            text_items.append(str(self.quote))
        text = '\n'.join(text_items)

        author_render = f'{self.author} ({self.username})'
        if self.retweet_header:
            author = self.retweet_header
            title = author_render
        else:
            author = author_render
            title = self.reply_header or self.url

        embed = {
            'title': title,
            'description': text,
            'url': self.url,
            'color': None,
            'author': {'name': author, 'icon_url': self.avatar_url},
            'timestamp': self.published.isoformat(),
        }

        def format_attachments(post_url: str, attachments: List[str]) -> List[dict]:
            return [{'url': post_url, 'image': {'url': attachment}} for attachment in attachments]

        if self.attachments:
            images = format_attachments(self.url, self.attachments)
            embed['image'] = images.pop(0)['image']
            embeds = [embed, *images]
        elif self.quote and self.quote.attachments:
            images = format_attachments(self.quote.url, self.quote.attachments)
            embed['image'] = images.pop(0)['image']
            embeds = [embed, *images]
        else:
            embeds = [embed]
        return embeds

class NitterQuoteRecord(NitterRecord):
    quote: None = None


@Plugins.register('nitter', Plugins.kind.ACTOR_CONFIG)
class NitterMonitorConfig(PagedFeedMonitorConfig):
    pass

@Plugins.register('nitter', Plugins.kind.ACTOR_ENTITY)
class NitterMonitorEntity(PagedFeedMonitorEntity):
    update_interval: float = 1800
    """How often the monitored url will be checked, in seconds"""


def get_text_content(element: lxml.html.HtmlElement) -> str:
    def handle_element(element: lxml.html.HtmlElement) -> str:
        if isinstance(element, str):
            return element
        link = element.attrib.get('href')
        if link is None:
            return element.text
        if link.find('/search?q=') > -1: # hashtag
            return element.text
        return link

    strings = [handle_element(child) for child in element.xpath("node()")]
    text = ''.join(strings)
    test = element.text_content()
    return text

def get_html_content(element: lxml.html.HtmlElement) -> str:
    return ''.join([x if isinstance(x, str) else lxml.etree.tounicode(x) for x in element.xpath("node()")])


@Plugins.register('nitter', Plugins.kind.ACTOR)
class NitterMonitor(PagedFeedMonitor):
    """
    Monitor for Nitter instances

    Monitors recent tweets, retweets and replies of Twitter user
    by scraping and parsing data from a Nitter instance.

    Examples of supported urls:

    - `https://nitter.net/username`
    - `https://nitter.net/username/with_replies`

    Some instances might not be happy about getting automated scraping. Make sure
    to use a reasonable `update_interval` and keep an eye out for 4XX and 5XX responses
    in log, as they might indicate server is under high load or refuses to
    communicate.

    Nitter has a built-in RSS feed, though not all instances enable it, so it
    can also be monitored with `generic_rss` plugin instead of this one.

    Twitter Spaces appears on user feed as normal tweets with text only
    containing a single link similar to `https://x.com/i/spaces/2FsjOybqEbnzR`.
    It therefore can be picked up by using a regular full-text `match` filter.
    """

    # nitter.net instance returns 403 in absense of this two headers and if UserAgent contains "python-requests"
    HEADERS = {'Accept-Language': 'en-US', 'Accept-Encoding': 'gzip, deflate',
               'Cookie': 'Cookie: hideBanner=on; hidePins=on; replaceTwitter=; replaceYouTube=; replaceReddit='}

    async def handle_first_page(self, entity: NitterMonitorEntity, session: aiohttp.ClientSession) -> Tuple[Optional[Sequence[Record]], Optional[Any]]:
        raw_page = await self._get_user_page(entity, session)
        if raw_page is None:
            return None, None
        page = self._parse_html(raw_page, entity.url)
        records = self._parse_entries(page)
        next_page_url = self._get_continuation_url(page)
        return records, next_page_url

    async def handle_next_page(self, entity: NitterMonitorEntity, session: aiohttp.ClientSession, context: Optional[Any]) -> Tuple[Optional[Sequence[Record]], Optional[Any]]:
        next_page_url: Optional[str] = context
        if next_page_url is None:
            return None, None
        raw_page = await utils.request(next_page_url, session, self.logger, headers=self.HEADERS, retry_times=3, retry_multiplier=2, retry_delay=5)
        if raw_page is None:
            return None, None
        page = self._parse_html(raw_page, entity.url)
        records = self._parse_entries(page)
        next_page_url = self._get_continuation_url(page)
        return records, next_page_url

    async def _get_user_page(self, entity: NitterMonitorEntity, session: aiohttp.ClientSession) -> Optional[str]:
        text = await self.request(entity.url, entity, session, headers=self.HEADERS)
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
        [avatar_url] = raw_quote.xpath(".//img[@class='avatar round mini']/@src") or [None]

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

        return NitterQuoteRecord(url=url,
                                 author=author,
                                 username=username,
                                 avatar_url=avatar_url,
                                 published=published,
                                 text=text,
                                 html=html,
                                 attachments=attachments
                                 )

    def _parse_post(self, raw_post: lxml.html.HtmlElement) -> NitterRecord:
        retweet_header = ''.join(element.text_content() for element in raw_post.xpath(".//*[@class='retweet-header']")).lstrip() or None
        reply_header = ''.join(element.text_content() for element in raw_post.xpath(".//*[@class='tweet-body']/*[@class='replying-to']")).lstrip() or None

        url = raw_post.xpath(".//*[@class='tweet-link']/@href")[0]
        url = re.sub('#m$', '', url)
        author = raw_post.xpath(".//*[@class='fullname']/@title")[0]
        username = raw_post.xpath(".//*[@class='username']/@title")[0]
        [avatar_url] = raw_post.xpath(".//*[@class='tweet-avatar']/img/@src") or [None]

        published_text = raw_post.xpath(".//*[@class='tweet-date']/a/@title")[0]
        published = parser.parse(published_text.replace('·', ''))

        post_body = raw_post.xpath(".//*[@class='tweet-content media-body']")[0]
        text = get_text_content(post_body)
        html = get_html_content(post_body)

        [raw_attachments] = raw_post.xpath("(.//*[@class='tweet-body']/*[@class='attachments'])[1]") or [None]
        attachments = self._parse_attachments(raw_attachments) if raw_attachments is not None else []

        video_attachments = raw_post.xpath(".//*[@class='attachment video-container']/video/@poster")
        if video_attachments:
            thumbnails = [urllibparse.urljoin(raw_post.base_url, thumb) for thumb in video_attachments]
            attachments.extend(thumbnails)

        [raw_quote] = raw_post.xpath(".//*[@class='quote quote-big']") or [None]
        quote = self._parse_quote(raw_quote) if raw_quote is not None else None

        return NitterRecord(url=url,
                            author=author,
                            username=username,
                            avatar_url=avatar_url,
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
    """match retweets"""
    reply: bool = False
    """match replies"""
    quote: bool = False
    """match quotes"""
    regular_tweet: bool = False
    """match regular tweets that are not a retweet, reply or quote"""
    author: Optional[str] = None
    """match if a given string is a part of the name of the author of the tweet"""
    username: Optional[str] = None
    """match if a given string is a part of tweet author's username (without the "@" symbol)"""


@Plugins.register('filter.nitter.pick', Plugins.kind.ACTOR)
class NitterFilterPick(Filter):
    """
    Pick `NitterRecord` with specified properties

    Lets through `NitterRecord` if it matches any of specified criteria.
    All records from other sources pass through without filtering.
    """

    def __init__(self, config: NitterFilterConfig, entities: Sequence[NitterFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: NitterFilterEntity, record: NitterRecord) -> Optional[NitterRecord]:
        if not isinstance(record, NitterRecord):
            self.logger.debug(f'[{entity.name}] record is not a NitterRecord, letting through: {record!r}')
            return record
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
    """
    Drop `NitterRecord` without specified properties.

    Lets through `NitterRecord` if it doesn't match all of the specified criteria.
    All records from other sources pass through without filtering.
    """

    def __init__(self, config: NitterFilterConfig, entities: Sequence[NitterFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: NitterFilterEntity, record: NitterRecord) -> Optional[NitterRecord]:
        if not isinstance(record, NitterRecord):
            self.logger.debug(f'[{entity.name}] record is not a NitterRecord, letting through: {record!r}')
            return record
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

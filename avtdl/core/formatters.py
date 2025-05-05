import datetime
import logging
import re
from email.utils import mktime_tz
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import lxml.html

from avtdl.core.interfaces import Record
from avtdl.core.utils import is_url


def sanitize_filename(name: str, collapse: bool = False) -> str:
    """Replace symbols not allowed in file names on NTFS with underscores"""
    pattern = r'[\\/:*?"<>|]+' if collapse else r'[\\/:*?"<>|]'
    return re.sub(pattern, "_", name)


def make_datetime(items) -> datetime.datetime:
    """take 10-tuple and return datetime object with UTC timezone"""
    if len(items) == 9:
        items = *items, None
    if len(items) != 10:
        raise ValueError(f'Expected tuple with 10 elements, got {len(items)}')
    timestamp = mktime_tz(items)
    return datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)


def html_from_string(html: str, base_url: Optional[str] = None) -> lxml.html.HtmlElement:
    try:
        root: lxml.html.HtmlElement = lxml.html.fromstring(html)
        if base_url is not None:
            root.make_links_absolute(base_url=base_url, handle_failures='ignore')
        return root
    except Exception as e:
        logging.getLogger('html_to_text').exception(e)
        raise


def html_to_text(html: str, base_url: Optional[str] = None) -> str:
    """take html fragment, try to parse it and convert to text using lxml"""
    try:
        root = html_from_string(html, base_url)
    except Exception:
        return html
    # text_content() skips <img> content altogether
    # walk tree manually and for images containing links
    # add them to text representation
    for elem in root.iter():
        if elem.tag == 'br':
            elem.text = '\n'
        if elem.tag == 'a':
            link = elem.get('href')
            if link is not None:
                if elem.text_content():
                    elem.text = f'{elem.text_content()} ({link})'
                else:
                    elem.text = f'{link}'
        if elem.tag == 'img':
            image_link = elem.get('src')
            if image_link is not None:
                elem.text = f'\n{image_link}\n'
    text = root.text_content()
    return text


def html_images(html: str, base_url: Optional[str]) -> List[str]:
    """take html fragment, try to parse it and extract image links"""
    try:
        root = html_from_string(html, base_url)
    except Exception:
        return []
    images = [elem.get('src') for elem in root.iter() if elem.tag == 'img' and elem.get('src')]
    return images


class OutputFormat(str, Enum):
    text = 'text'
    repr = 'short'
    json = 'json'
    pretty_json = 'pretty_json'
    hash = 'hash'


class Fmt:
    """Helper class to interpolate format string from config using data from Record"""

    @classmethod
    def format(cls, fmt: str, record: Record, missing: Optional[str] = None, tz: Optional[datetime.tzinfo] = None,
               sanitize: bool = False, extra: Optional[Dict[str, Any]] = None) -> str:
        """Take string with placeholders like {field} and replace them with record fields"""
        logger = logging.getLogger().getChild('format')
        result = cls.strftime(fmt, datetime.datetime.now(tz))
        record_as_dict = record.model_dump()
        if extra is not None:
            record_as_dict.update(extra)
        placeholders: List[str] = re.findall(r'({[^{}\\]+})', fmt)
        for placeholder in placeholders:
            field = placeholder.strip('{}')
            value = record_as_dict.get(field)
            if value is not None:
                value = cls.format_value(value, sanitize)
                result = result.replace(placeholder, value)
            else:
                if missing is not None:
                    result = result.replace(placeholder, missing)
                else:
                    logger.warning(
                        f'placeholder "{placeholder}" used by format string "{fmt}" is not a field of {record.__class__.__name__} ({record!r}), resulting command is unlikely to be valid')
        result = result.replace(r'\{', '{')
        result = result.replace(r'\}', '}')
        return result

    @classmethod
    def format_value(cls, value: Any, sanitize: bool = False) -> str:
        if value is None:
            value = ''
        elif isinstance(value, datetime.datetime):
            value = cls.date(value)
        else:
            value = str(value)
            if sanitize:
                value = sanitize_filename(value)
        return value

    @classmethod
    def format_path(cls, path: Union[str, Path], record: Record, missing: Optional[str] = None,
                    tz: Optional[datetime.tzinfo] = None, extra: Optional[Dict[str, Any]] = None) -> Path:
        """Take string with placeholders and replace them with record fields, but strip them from bad symbols"""
        fmt = str(path)
        formatted_path = cls.format(fmt, record, missing, tz=tz, sanitize=True, extra=extra)
        return Path(formatted_path)

    @classmethod
    def format_filename(cls, path: Union[str, Path], name: str, record: Record, missing: Optional[str] = None,
                        tz: Optional[datetime.tzinfo] = None, extra: Optional[Dict[str, Any]] = None) -> Path:
        """format file path and filename templates into a Path object"""
        path = cls.format_path(path, record, missing, tz, extra)
        formatted_name = cls.format(name, record, missing, tz=tz, sanitize=True, extra=extra)
        sanitized_name = sanitize_filename(formatted_name)
        return path / sanitized_name

    @classmethod
    def strftime(cls, fmt: str, dt: datetime.datetime) -> str:
        if '%' in fmt:
            fmt = re.sub(r'(%[^aAwdbBmyYHIpMSfzZjUWcxX%GuV])', r'%\1', fmt)
        try:
            return dt.strftime(fmt)
        except ValueError as e:
            logger = logging.getLogger().getChild('format').getChild('strftime')
            logger.debug(f'error adding current date to template "{fmt}": {e}')
            return fmt

    @classmethod
    def date(cls, dt: datetime.datetime) -> str:
        return dt.strftime('%Y-%m-%d %H:%M')

    @classmethod
    def size(cls, size: Union[int, float]) -> str:
        for unit in ['B', 'kB', 'MB', 'GB', 'TB']:
            if size < 1024:
                break
            size /= 1024
        return f"{size:.2f} {unit}"

    @classmethod
    def duration(cls, seconds: int) -> str:
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours}:{minutes:02}:{seconds:02}"

    @classmethod
    def dtf(cls, dt: datetime.datetime) -> str:
        """format datetime to Discord timestamp"""
        ts = int(dt.timestamp())
        return f'<t:{ts}>'

    @classmethod
    def save_as(cls, record: Record, output_format: OutputFormat = OutputFormat.text) -> str:
        """Take a record and convert in to string as text/json or sha1"""
        if output_format == OutputFormat.text:
            return str(record)
        if output_format == OutputFormat.repr:
            return repr(record)
        if output_format == OutputFormat.json:
            return record.as_json()
        if output_format == OutputFormat.pretty_json:
            return record.as_json(2)
        if output_format == OutputFormat.hash:
            return record.hash()


class DiscordEmbedLimits:
    TOTAL = 6000
    AUTHOR_NAME = 256
    TITLE = 256
    DESCRIPTION = 4096
    FOOTER_TEXT = 2048
    EMBEDS_PER_MESSAGE = 10


class MessageFormatter:

    @classmethod
    def format(cls, records: List[Record]) -> Tuple[dict, List[Record]]:
        """take records and format them in Discord webhook payload as embeds
        after the limit on embeds is reached, the rest of the records are returned back"""
        embeds: List[Dict[str, Any]] = []
        excess_records = []
        for i, record in enumerate(records):
            record_embeds = cls.make_embeds(record, True)
            if len(embeds) + len(record_embeds) > DiscordEmbedLimits.EMBEDS_PER_MESSAGE:
                excess_records = records[i:]
                break
            else:
                embeds.extend(record_embeds)
        message = cls.make_message(embeds)
        return message, excess_records

    @classmethod
    def make_message(cls, embeds: List[Dict[str, Any]]) -> dict:
        return {
            "content": None,
            "embeds": embeds
        }

    @classmethod
    def make_embeds(cls, record: Record, strip_extra: bool = False) -> List[Dict[str, Any]]:
        embeds = record.as_embed()
        if not isinstance(embeds, list):
            embeds = [embeds]
        if strip_extra:
            for embed in embeds:
                cls.clean_embed(embed)
        else:
            # only adding more extra fields if they are not going to be stripped
            for embed in embeds:
                embed['_timestamp'] = int(record.created_at.timestamp() * 1000)
                embed['_origin'] = record.origin

        return embeds

    @classmethod
    def clean_embed(cls, embed: Dict[str, Any]):
        """remove embed fields starting with underscore"""
        extra_fields = []
        for field in embed:
            if field.startswith('_'):
                extra_fields.append(field)
            elif isinstance(field, dict):
                cls.clean_embed(field)
        for field in extra_fields:
            embed.pop(field)

    @classmethod
    def rewrite_embed_links(cls, embed: Dict[str, Any], rewriter: Callable[[str], Optional[str]]):
        """replace known image urls in the embed with rewriter(url)"""
        image_fields = [('image', 'url'),
                        ('image', 'thumbnail'),
                        ('image', '_preview'),
                        ('author', 'icon_url'),
                        ('footer', 'icon_url')]
        for field_name, subfield_name in image_fields:
            field = embed.get(field_name, None)
            if not isinstance(field, dict):
                continue
            image_url = field.get(subfield_name, None)
            if is_url(image_url):
                new_url = rewriter(image_url)
                if new_url is not None:
                    embed[field_name][subfield_name] = new_url

    @classmethod
    def check_limits(cls, message: dict) -> bool:
        # doesn't count field.name and field.value number and size in hope it will not change outcome

        total_length = 0
        embeds = message.get('embeds', [])
        if len(embeds) > DiscordEmbedLimits.EMBEDS_PER_MESSAGE:
            return False

        for embed in embeds:
            author_name = len(embed.get('author', {}).get('name', '') or '')
            title = len(embed.get('title') or '')
            description = len(embed.get('description') or '')
            footer_text = len((embed.get('footer') or {}).get('text') or '')
            if author_name > DiscordEmbedLimits.AUTHOR_NAME:
                return False
            if title > DiscordEmbedLimits.TITLE:
                return False
            if description > DiscordEmbedLimits.DESCRIPTION:
                return False
            if footer_text > DiscordEmbedLimits.FOOTER_TEXT:
                return False
            total_length += author_name + title + description + footer_text

        if total_length > DiscordEmbedLimits.TOTAL:
            return False
        return True

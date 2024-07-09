import datetime
import re
from typing import Optional, Tuple

from pydantic import BaseModel, Field, ValidationError, field_validator

from avtdl.core.utils import find_all, find_one
from avtdl.plugins.youtube.common import extract_keys, get_continuation_token, thumbnail_url


class VideoRendererInfo(BaseModel):
    video_id: str
    url: str
    title: str
    summary: Optional[str] = Field(repr=False)
    scheduled: Optional[datetime.datetime] = None
    author: Optional[str]
    avatar_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    channel_link: Optional[str] = None
    channel_id: Optional[str] = None
    published_text: Optional[str]
    length: Optional[str]

    is_upcoming: bool
    is_live: bool
    is_member_only: bool


def get_video_renderers(page: str, anchor: str = 'var ytInitialData = ') -> Tuple[list, Optional[str], dict]:
    keys = ['gridVideoRenderer', 'videoRenderer', 'playlistVideoRenderer', 'continuationEndpoint']
    items, data = extract_keys(page, keys, anchor)
    continuation_token = get_continuation_token(items.pop('continuationEndpoint', {}))
    renderers = []
    for item in items.values():
        renderers.extend(item)
    return renderers, continuation_token, data


def parse_scheduled(timestamp: Optional[str]) -> Optional[datetime.datetime]:
    if timestamp is None:
        return None
    try:
        timestamp_value = float(timestamp)
    except ValueError:
        return None
    scheduled = datetime.datetime.fromtimestamp(timestamp_value, tz=datetime.timezone.utc)
    return scheduled


def get_author_fallback(item: dict) -> Optional[str]:
    full_text = find_one(item, '$.title.accessibility..label')
    title_part = find_one(item, '$.title.runs..text')
    views_part = find_one(item, '$.viewCountText.simpleText')
    views_part_fallback = re.search('\d+\D+$', full_text) if full_text else None
    if not all((full_text, title_part, views_part or views_part_fallback)):
        return None
    start = full_text.find(title_part)

    if views_part is not None:
        end = full_text.find(views_part)
    elif views_part_fallback is not None:
        end = views_part_fallback.start()
    else:
        end = -1

    if -1 in [start, end]:
        return None
    try:
        author_text = full_text[start + len(title_part): end].split(':')[1].strip()
    except (IndexError, AttributeError):
        return None
    return author_text


class AuthorInfo(BaseModel):
    name: str
    channel: str
    channel_id: str
    avatar_url: Optional[str] = None

    @field_validator('channel')
    @classmethod
    def add_prefix(cls, channel: str) -> str:
        if channel.startswith('/'):
            return 'https://www.youtube.com' + channel
        else:
            return channel


def parse_author(video_render: dict) -> Optional[AuthorInfo]:
    author_info = find_one(video_render, '$.ownerText,shortBylineText')
    if author_info is None:
        return None
    author = find_one(author_info, '$..text')
    channel_link = find_one(author_info, '$..browseEndpoint.canonicalBaseUrl')
    channel_id = find_one(author_info, '$..browseEndpoint.browseId')
    avatar_url = find_one(video_render, '$.channelThumbnailSupportedRenderers..thumbnails.[::-1].url')
    try:
        return AuthorInfo(name=author, channel=channel_link, channel_id=channel_id, avatar_url=avatar_url)
    except ValidationError:
        return None


def parse_owner_info(page: dict) -> Optional[AuthorInfo]:
    return parse_owner_from_header(page) or parse_owner_from_metadata(page) or None


def parse_owner_from_metadata(page: dict) -> Optional[AuthorInfo]:
    metadata = find_one(page, '$.metadata.channelMetadataRenderer')
    if metadata is None:
        return None
    author = find_one(metadata, '$.title')
    channel_link = find_one(metadata, '$.vanityChannelUrl')
    channel_id = find_one(metadata, '$.externalId')
    avatar_url = find_one(metadata, '$.avatar.thumbnails.[::-1].url')
    try:
        return AuthorInfo(name=author, channel=channel_link, channel_id=channel_id, avatar_url=avatar_url)
    except ValidationError:
        return None

def parse_owner_from_header(page: dict) -> Optional[AuthorInfo]:
    owner_info_data = find_one(page, '$.header.c4TabbedHeaderRenderer')
    if owner_info_data is None:
        return None
    author = find_one(owner_info_data, '$.title')
    channel_link = find_one(owner_info_data, '$.navigationEndpoint.browseEndpoint.canonicalBaseUrl')
    channel_id = find_one(owner_info_data, '$.channelId')
    avatar_url = find_one(owner_info_data, '$.avatar.thumbnails.[::-1].url')
    try:
        return AuthorInfo(name=author, channel=channel_link, channel_id=channel_id, avatar_url=avatar_url)
    except ValidationError:
        return None


def parse_video_renderer(item: dict, owner_info: Optional[AuthorInfo], raise_on_error: bool = False) -> Optional[
    VideoRendererInfo]:
    video_id = item.get('videoId')
    url = f'https://www.youtube.com/watch?v={video_id}'
    title = find_one(item, '$.title..text,simpleText')
    summary = find_one(item, '$.descriptionSnippet..text')
    thumbnail = thumbnail_url(str(video_id)) or None

    author_info = parse_author(item) or owner_info
    if author_info is None:
        author_name = get_author_fallback(item)
        channel_link = channel_id = avatar_url = None
    else:
        author_name = author_info.name
        channel_link = author_info.channel
        channel_id = author_info.channel_id
        avatar_url = author_info.avatar_url

    scheduled_timestamp = find_one(item, '$.upcomingEventData.startTime')
    scheduled = parse_scheduled(scheduled_timestamp)
    published_text = find_one(item, '$.publishedTimeText.simpleText')
    length = find_one(item, '$.lengthText.simpleText') or find_one(item,
                                                                   '$..thumbnailOverlayTimeStatusRenderer.simpleText')

    badges = find_all(item, '$.badges..style')
    is_member_only = 'BADGE_STYLE_TYPE_MEMBERS_ONLY' in badges
    is_live = 'BADGE_STYLE_TYPE_LIVE_NOW' in badges
    is_upcoming = scheduled is not None

    try:
        info = VideoRendererInfo(video_id=video_id,
                                 url=url,
                                 title=title,
                                 summary=summary,
                                 scheduled=scheduled,
                                 author=author_name,
                                 avatar_url=avatar_url,
                                 thumbnail_url=thumbnail,
                                 channel_link=channel_link,
                                 channel_id=channel_id,
                                 published_text=published_text,
                                 length=length,
                                 is_live=is_live,
                                 is_upcoming=is_upcoming,
                                 is_member_only=is_member_only)
        return info
    except ValidationError:
        if raise_on_error:
            raise
        return None


def handle_page(page: str) -> list:
    items, continuation, data = get_video_renderers(page)
    owner_info = parse_owner_info(data)
    info = [parse_video_renderer(x, owner_info) for x in items]
    return info

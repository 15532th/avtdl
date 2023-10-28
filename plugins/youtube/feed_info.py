import datetime
import re
from typing import Optional

import requests
from pydantic import BaseModel, Field, ValidationError, field_validator

from plugins.youtube.utils import find_all, find_one, get_initial_data


class VideoRendererInfo(BaseModel):
    video_id: str
    url: str
    title: str
    summary: Optional[str] = Field(repr=False)
    scheduled: Optional[datetime.datetime] = None
    author: Optional[str]
    channel_link: Optional[str] = None
    channel_id: Optional[str] = None
    published_text: Optional[str]
    length: Optional[str]

    is_upcoming: bool
    is_live: bool
    is_member_only: bool

def get_video_renderers(data: dict) -> list:
    items = find_all(data, '$..[videoRenderer,gridVideoRenderer]')
    return items

def parse_scheduled(timestamp: Optional[str]) -> Optional[datetime.datetime]:
    if timestamp is None:
        return None
    try:
        timestamp_value = float(timestamp)
    except ValueError:
        return None
    scheduled = datetime.datetime.fromtimestamp(timestamp_value, tz=datetime.timezone.utc)
    return scheduled

def get_author_fallback(item:dict) -> Optional[str]:
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
        author_text = full_text[start + len(title_part) : end].split(':')[1].strip()
    except (IndexError, AttributeError):
        return None
    return author_text


class AuthorInfo(BaseModel):
    name: str
    channel: str
    channel_id: str
    
    @field_validator('channel')
    @classmethod
    def add_prefix(cls, channel: str) -> str:
        if channel.startswith('/'):
            return 'https://www.youtube.com' + channel
        else:
            return channel


def parse_author(video_render: dict) -> Optional[AuthorInfo]:
    author_info = find_one(video_render, '$.[ownerText,shortBylineText]')
    if author_info is None:
        return None
    author = find_one(author_info, '$..text')
    channel_link = find_one(author_info, '$..browseEndpoint.canonicalBaseUrl')
    channel_id = find_one(author_info, '$..browseEndpoint.browseId')
    try:
        return AuthorInfo(name=author, channel=channel_link, channel_id=channel_id)
    except ValidationError:
        return None

def parse_owner_info(page: dict) -> Optional[AuthorInfo]:
    owner_info_data = find_one(page, '$.header.c4TabbedHeaderRenderer')
    if owner_info_data is None:
        return None
    author = find_one(owner_info_data, '$.title')
    channel_link = find_one(owner_info_data, '$..canonicalBaseUrl')
    channel_id = find_one(owner_info_data, '$.channelId')
    try:
        return AuthorInfo(name=author, channel=channel_link, channel_id=channel_id)
    except ValidationError:
        return None

def parse_video_renderer(item: dict, owner_info: Optional[AuthorInfo]) -> Optional[VideoRendererInfo]:
    video_id = item.get('videoId')
    url = f'https://www.youtube.com/watch?v={video_id}'
    title = find_one(item, '$.title..[text,simpleText]')
    summary = find_one(item, '$.descriptionSnippet..text')
    
    author_info = parse_author(item) or owner_info
    if author_info is None:
        author_name = get_author_fallback(item) 
        channel_link = channel_id = None
    else:
        author_name = author_info.name
        channel_link = author_info.channel
        channel_id = author_info.channel_id

    scheduled_timestamp = find_one(item, '$.upcomingEventData.startTime')
    scheduled = parse_scheduled(scheduled_timestamp)
    published_text = find_one(item, '$.publishedTimeText.simpleText')
    length = find_one(item, '$.lengthText.simpleText') or find_one(item, '$..thumbnailOverlayTimeStatusRenderer.simpleText')

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
                                 channel_link=channel_link,
                                 channel_id=channel_id,
                                 published_text=published_text,
                                 length=length,
                                 is_live=is_live,
                                 is_upcoming=is_upcoming,
                                 is_member_only=is_member_only)
        return info
    except ValidationError:
        return None

def handle_page(page: str) -> list:
    data = get_initial_data(page)
    owner_info = parse_owner_info(data)
    items = get_video_renderers(data)
    info = [parse_video_renderer(x, owner_info) for x in items]
    return info

def handle_url(url: str) -> list:
    page = requests.get(url).text
    return handle_page(page)

if __name__ == '__main__':
    urls = ['https://www.youtube.com/@OmaruPolka/streams',
            'https://www.youtube.com/@OmaruPolka/videos',
            'https://www.youtube.com/@OmaruPolka/featured']
    x1 = handle_url(urls[0])
    x2 = handle_url(urls[1])
    x3 = handle_url(urls[2])

    ...
import datetime
import json
import re
from typing import Any, Optional

import jsonpath_ng
import requests
from pydantic import BaseModel, Field


class VideoRendererInfo(BaseModel):
    video_id: str
    url: str
    title: str
    summary: Optional[str] = Field(repr=False)
    scheduled: Optional[datetime.datetime] = None
    author: Optional[str]
    channel_id: Optional[str]
    published_text: Optional[str]
    length: Optional[str]

    is_upcoming: bool
    is_live: bool
    is_member_only: bool


def find_all(data: Any, jsonpath: str, cache={}) -> list:
    if jsonpath not in cache:
        cache[jsonpath] = jsonpath_ng.parse(jsonpath)
    parser = cache[jsonpath]
    return [item.value for item in parser.find(data)]

def find_one(data: Any, jsonpath: str) -> Optional[Any]:
    result = find_all(data, jsonpath)
    return result[0] if result else None

def get_initial_data(page: str) -> dict:
    re_initial_data = 'var ytInitialData = ({.*?});'
    match = re.search(re_initial_data, page)
    if match is None:
        raise ValueError(f'Failed to find ytInitialData on the page')
    raw_data = match.groups()[0]
    data = json.loads(raw_data)
    return data

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



def parse_video_renderer(item: dict) -> VideoRendererInfo:
    video_id = item.get('videoId')
    url = f'https://www.youtube.com/watch?v={video_id}'
    title = find_one(item, '$.title..[text,simpleText]')
    summary = find_one(item, '$.descriptionSnippet..text')

    scheduled_timestamp = find_one(item, '$.upcomingEventData.startTime')
    scheduled = parse_scheduled(scheduled_timestamp)

    author = find_one(item, '$.shortBylineText..text') or get_author_fallback(item)
    channel_id = find_one(item, '$.shortBylineText..canonicalBaseUrl')
    channel_id = channel_id.strip('/') if channel_id else channel_id
    published_text = find_one(item, '$.publishedTimeText.simpleText')
    length = find_one(item, '$.lengthText.simpleText') or find_one(item, '$..thumbnailOverlayTimeStatusRenderer.simpleText')

    badges = find_all(item, '$.badges..style')
    is_member_only = 'BADGE_STYLE_TYPE_MEMBERS_ONLY' in badges
    is_live = 'BADGE_STYLE_TYPE_LIVE_NOW' in badges
    is_upcoming = scheduled is not None

    print(badges, title) if badges else ...
    try:
        info = VideoRendererInfo(video_id=video_id,
                                 url=url,
                                 title=title,
                                 summary=summary,
                                 scheduled=scheduled,
                                 author=author,
                                 channel_id=channel_id,
                                 published_text=published_text,
                                 length=length,
                                 is_live=is_live,
                                 is_upcoming=is_upcoming,
                                 is_member_only=is_member_only)
    except Exception as e:
        f'{e!r}: {e}'
        return None
    return info

def handle_page(page: str) -> list:
    data = get_initial_data(page)
    items = get_video_renderers(data)
    info = [parse_video_renderer(x) for x in items]
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
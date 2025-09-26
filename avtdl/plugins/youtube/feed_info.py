import datetime
import logging
import re
from typing import Any, Optional, Tuple

import dateutil.parser
from pydantic import BaseModel, Field, ValidationError, field_validator

from avtdl.core.utils import JSONType, find_all, find_one
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


def get_video_renderers(page: str, anchor: str = 'var ytInitialData = ') -> Tuple[list, list, Optional[str], dict]:
    keys = [
        'gridVideoRenderer', 'videoRenderer', 'playlistVideoRenderer',
        'lockupViewModel',
        'continuationEndpoint'
    ]
    items, data = extract_keys(page, keys, anchor)
    continuation_token = get_continuation_token(items.pop('continuationEndpoint', {}))
    lockup_views = items.pop('lockupViewModel', [])
    renderers = []
    for item in items.values():
        renderers.extend(item)
    return renderers, lockup_views, continuation_token, data


def parse_scheduled(timestamp: Optional[Any]) -> Optional[datetime.datetime]:
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
    if not isinstance(full_text, str) or not isinstance(title_part, str) or not isinstance(views_part, str):
        return None
    views_part_fallback = re.search('\d+\D+$', full_text) if full_text is not None else None
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


def prime_channel_link(channel: str) -> str:
    if channel.startswith('/'):
        return 'https://www.youtube.com' + channel
    else:
        return channel


class AuthorInfo(BaseModel):
    name: str
    channel: str
    channel_id: str
    avatar_url: Optional[str] = None

    @field_validator('channel')
    @classmethod
    def add_prefix(cls, channel: str) -> str:
        return prime_channel_link(channel)


def parse_author(video_render: dict) -> Optional[AuthorInfo]:
    author_info = find_one(video_render, '$.ownerText,shortBylineText')
    if author_info is None:
        return None
    author = find_one(author_info, '$..text')
    channel_link = find_one(author_info, '$..browseEndpoint.canonicalBaseUrl')
    channel_id = find_one(author_info, '$..browseEndpoint.browseId')
    avatar_url = find_one(video_render, '$.channelThumbnailSupportedRenderers..thumbnails.[::-1].url')
    try:
        return AuthorInfo(name=author, channel=channel_link, channel_id=channel_id, avatar_url=avatar_url) # type: ignore
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
        return AuthorInfo(name=author, channel=channel_link, channel_id=channel_id, avatar_url=avatar_url) # type: ignore
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
        return AuthorInfo(name=author, channel=channel_link, channel_id=channel_id, avatar_url=avatar_url) # type: ignore
    except ValidationError:
        return None


def parse_video_renderer(item: dict, owner_info: Optional[AuthorInfo]) -> VideoRendererInfo:
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

    info = VideoRendererInfo(video_id=video_id,  # type: ignore
                             url=url,
                             title=title,  # type: ignore
                             summary=summary,  # type: ignore
                             scheduled=scheduled,
                             author=author_name,
                             avatar_url=avatar_url,
                             thumbnail_url=thumbnail,
                             channel_link=channel_link,
                             channel_id=channel_id,
                             published_text=published_text,  # type: ignore
                             length=length,  # type: ignore
                             is_live=is_live,
                             is_upcoming=is_upcoming,
                             is_member_only=is_member_only)
    return info


class ContentTypeNotSupportedException(Exception):
    """Raised when contentType is not video"""


def parse_upcoming_timestamp(text: JSONType) -> Optional[datetime.datetime]:
    if not isinstance(text, str):
        return None
    try:
        [clean_text] = re.findall(r'\d.*$', text)
        timestamp = dateutil.parser.parse(clean_text)
        timestamp_utc = timestamp.astimezone(datetime.timezone.utc)
        return timestamp_utc
    except Exception as e:
        logging.getLogger('parse_upcoming_timestamp').warning(f'failed to parse upcoming time "{text}": {e}')
        return None


def parse_lockup_view(item: dict, owner_info: Optional[AuthorInfo], try_unknown_type: bool = False) -> VideoRendererInfo:
    content_type = item.get('contentType')
    if content_type is None:
        if find_one(item, '$.metadata.feedAdMetadataViewModel.headline'):
            raise ContentTypeNotSupportedException('feedAdMetadataViewModel')
        else:
            raise ValueError('contentType is missing')
    if not content_type == 'LOCKUP_CONTENT_TYPE_VIDEO' and not try_unknown_type:
        raise ContentTypeNotSupportedException(f'{content_type}')
    video_id = item.get('contentId')
    url = f'https://www.youtube.com/watch?v={video_id}'
    title = find_one(item, '$.metadata.lockupMetadataViewModel.title.content')
    thumbnail = thumbnail_url(str(video_id)) or None

    metadata_parts = find_all(item, '$..metadata.lockupMetadataViewModel.metadata.contentMetadataViewModel.metadataRows[*].metadataParts[*].text.content')
    author_info = parse_author(item) or owner_info
    if author_info is None:
        author_name = metadata_parts[0] if metadata_parts else None
        avatar_view_model = find_one(item, '$.metadata.lockupMetadataViewModel')
        channel_link = find_one(item, '$.image..rendererContext..innertubeCommand.browseEndpoint.canonicalBaseUrl') or find_one(item, '$.metadata.lockupMetadataViewModel.metadata.contentMetadataViewModel..innertubeCommand.browseEndpoint.canonicalBaseUrl')
        if isinstance(channel_link, str):
            channel_link = prime_channel_link(channel_link)
        channel_id = find_one(item, '$.metadata.lockupMetadataViewModel.metadata.contentMetadataViewModel..innertubeCommand.browseEndpoint.browseId')
        avatar_url = find_one(avatar_view_model, '$.image..avatar..image..url')
    else:
        author_name = author_info.name
        channel_link = author_info.channel
        channel_id = author_info.channel_id
        avatar_url = author_info.avatar_url

    badges_texts = find_all(item, '$..thumbnailBadges..text')
    badges = find_all(item, '$..badgeStyle')
    is_member_only = 'BADGE_MEMBERS_ONLY' in badges
    is_live = 'THUMBNAIL_OVERLAY_BADGE_STYLE_LIVE' in badges

    published_text = metadata_parts[-1] if metadata_parts else None
    length = find_one(item, '$.contentImage.thumbnailViewModel..thumbnailOverlayBadgeViewModel.thumbnailBadges..thumbnailBadgeViewModel.text')
    if is_live:
        published_text = None
        length = None

    is_upcoming = find_one(item, 'attachmentSlot.lockupAttachmentsViewModel..toggleButtonViewModel..innertubeCommand.addUpcomingEventReminderEndpoint') is not None
    if is_upcoming:
        scheduled = parse_upcoming_timestamp(published_text)
    else:
        scheduled = None

    info = VideoRendererInfo(video_id=video_id,  # type: ignore
                             url=url,
                             title=title,  # type: ignore
                             summary=None,
                             scheduled=scheduled,
                             author=author_name, # type: ignore
                             avatar_url=avatar_url, # type: ignore
                             thumbnail_url=thumbnail,
                             channel_link=channel_link, # type: ignore
                             channel_id=channel_id, # type: ignore
                             published_text=published_text,  # type: ignore
                             length=length,  # type: ignore
                             is_live=is_live,
                             is_upcoming=is_upcoming,
                             is_member_only=is_member_only)
    return info

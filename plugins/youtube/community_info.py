import time
from hashlib import sha1
from typing import List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from pydantic import BaseModel

from plugins.youtube.common import find_all, find_one

CLIENT_VERSION = '2.20231023.04.02'


class CommunityPostInfo(BaseModel):
    channel_id: str
    post_id: str
    author: str
    avatar_url: Optional[str] = None
    vote_count: str
    sponsor_only: bool
    published_text: str
    full_text: str
    attachments: List[str]
    video_id: Optional[str] = None
    original_post: Optional['CommunityPostInfo'] = None

    @classmethod
    def render_full_text(cls, post_renderer):
        items = find_one(post_renderer, '$.contentText.runs')
        return ''.join(cls.render_text_item(item) for item in items)

    @staticmethod
    def render_text_item(item):
        if 'watchEndpoint' in item:
            video_template = 'https://www.youtube.com/watch?v={}'
            video_id = item['watchEndpoint']['videoId']
            text = video_template.format(video_id)
        elif 'navigationEndpoint' in item:
            url = item['navigationEndpoint']['commandMetadata']['webCommandMetadata']['url']
            if url.startswith('https://www.youtube.com/redirect'):
                parsed_url = urlparse(url)
                redirect_url = parse_qs(parsed_url.query)['q'][0]
                url = unquote(redirect_url)
            elif url.startswith('/hashtag'):
                url = item['text']
            elif url.startswith('/'):
                site = 'https://www.youtube.com'
                url = site + url
            text = url
        else:
            text = ''.join(item['text'])
        return text.replace('\r', '')

    @classmethod
    def from_post_renderer(cls, post_renderer: str) -> 'CommunityPostInfo':
        author = find_one(post_renderer, '$.authorText..text')
        channel_id = find_one(post_renderer, '$.authorText..browseId')
        post_id = find_one(post_renderer, '$.postId')
        avatar_url = find_one(post_renderer, '$.authorThumbnail.thumbnails.[-1].url')
        if avatar_url is not None and str(avatar_url).startswith(r'//'):
            avatar_url = 'https:' + avatar_url

        vote_count = find_one(post_renderer, '$.voteCount.simpleText')

        sponsor_only = find_one(post_renderer, '$.sponsorsOnlyBadge') is not None
        published_text = find_one(post_renderer, '$.publishedTimeText..text')

        full_text = cls.render_full_text(post_renderer)

        attachments = find_all(post_renderer, '$.backstageAttachment..backstageImageRenderer.image.thumbnails.[-1:].url')
        video_id = find_one(post_renderer, '$.backstageAttachment..videoRenderer.videoId')

        original_post_render = find_one(post_renderer, '$.originalPost')
        original_post = cls.from_post_renderer(original_post_render) if original_post_render else None

        post = CommunityPostInfo(
            author=author,
            channel_id=channel_id,
            post_id=post_id,
            avatar_url=avatar_url,
            vote_count=vote_count,
            sponsor_only=sponsor_only,
            published_text=published_text,
            full_text=full_text,
            attachments=attachments,
            video_id=video_id,
            original_post=original_post
        )
        return post


def get_posts_renderers(data: dict) -> list:
    items = find_all(data, '$..post.backstagePostRenderer')
    return items

def get_continuation_token(data: dict) -> Optional[str]:
    token = find_one(data, '$..continuationEndpoint.continuationCommand.token')
    return token

def get_auth_header(sapisid: str) -> str:
    timestamp = str(int(time.time()))
    sapisidhash = sha1(' '.join([timestamp, sapisid, 'https://www.youtube.com']).encode()).hexdigest()
    return f'SAPISIDHASH {timestamp}_{sapisidhash}'

def prepare_next_page_request(initial_page_data: dict, continuation_token, cookies=None, client_version=None) -> Tuple[str, dict, dict]:
    BROWSE_ENDPOINT = 'https://www.youtube.com/youtubei/v1/browse?key=AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8'
    cookies = cookies or {}

    response_context = find_one(initial_page_data, '$.responseContext')
    session_index = find_one(response_context, '$.webResponseContextExtensionData.ytConfigData.sessionIndex') or ''

    if client_version is None:
        client_version = find_one(initial_page_data, '$..serviceTrackingParams..params[?key = "client.version"].value')
        if client_version is None:
            client_version = CLIENT_VERSION

    headers = {
        'X-Goog-AuthUser': session_index,
        'X-Origin': 'https://www.youtube.com',
        'X-Youtube-Client-Name': '1',
        'Content-Type': 'application/json'
    }
    if 'SAPISID' in cookies:
        headers['Authorization'] = get_auth_header(cookies['SAPISID'])

    post_body = {
        'context': {
            'client': {'clientName': 'WEB', 'clientVersion': client_version}},
        'continuation': continuation_token
    }
    return BROWSE_ENDPOINT, headers, post_body


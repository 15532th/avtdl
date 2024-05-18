from typing import List, Optional, Union

from pydantic import BaseModel

from avtdl.core.utils import find_all, find_one
from avtdl.plugins.youtube.common import parse_navigation_endpoint, thumbnail_url


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

    @classmethod
    def from_post_renderer(cls, post_renderer: dict) -> 'CommunityPostInfo':
        author = find_one(post_renderer, '$.authorText..text')
        channel_id = find_one(post_renderer, '$.authorText..browseId')
        post_id = find_one(post_renderer, '$.postId')
        avatar_url = find_one(post_renderer, '$.authorThumbnail.thumbnails.[::-1].url')
        if avatar_url is not None and str(avatar_url).startswith(r'//'):
            avatar_url = 'https:' + avatar_url

        vote_count = find_one(post_renderer, '$.voteCount.simpleText')

        sponsor_only = find_one(post_renderer, '$.sponsorsOnlyBadge') is not None
        published_text = find_one(post_renderer, '$.publishedTimeText..text')

        text_runs = find_one(post_renderer, '$.contentText.runs') or []
        full_text = render_full_text(text_runs)

        attachments = find_all(post_renderer, '$.backstageAttachment..backstageImageRenderer.image.thumbnails.[-1:].url')
        attachments = [link.split('=', 1)[0] + '=s0?imgmax=0' if 'fcrop' in link else link for link in attachments]
        video_id = find_one(post_renderer, '$.backstageAttachment..videoRenderer.videoId')
        video_thumbnail =  thumbnail_url(video_id) if video_id else find_one(post_renderer, '$.backstageAttachment..videoRenderer.thumbnail.url')
        if video_thumbnail is not None:
            attachments.append(video_thumbnail)

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
            video_id=video_id
        )
        return post


class SharedCommunityPostInfo(BaseModel):
    channel_id: str
    post_id: str
    author: str
    avatar_url: Optional[str] = None
    published_text: str
    full_text: str
    original_post: Optional[CommunityPostInfo] = None

    @classmethod
    def from_post_renderer(cls, post_renderer: dict) -> 'SharedCommunityPostInfo':
        author = find_one(post_renderer, '$.displayName..text')
        channel_id = find_one(post_renderer, '$.displayName..browseId')
        post_id = find_one(post_renderer, '$.postId')
        avatar_url = find_one(post_renderer, '$.thumbnail.thumbnails.[::-1].url')
        if avatar_url is not None and str(avatar_url).startswith(r'//'):
            avatar_url = 'https:' + avatar_url

        published_text = find_one(post_renderer, '$.publishedTimeText..text')

        text_runs = find_one(post_renderer, '$.content.runs') or []
        full_text = render_full_text(text_runs)

        original_post_render = find_one(post_renderer, '$.originalPost.backstagePostRenderer')
        original_post = CommunityPostInfo.from_post_renderer(original_post_render)

        post = SharedCommunityPostInfo(
            author=author,
            channel_id=channel_id,
            post_id=post_id,
            avatar_url=avatar_url,
            published_text=published_text,
            full_text=full_text,
            original_post=original_post
        )
        return post


def render_full_text(runs: list) -> str:
    return ''.join(render_text_item(item) for item in runs)


def render_text_item(item):
    if 'watchEndpoint' in item:
        video_template = 'https://www.youtube.com/watch?v={}'
        video_id = item['watchEndpoint']['videoId']
        text = video_template.format(video_id)
    elif 'navigationEndpoint' in item:
        text = parse_navigation_endpoint(item)
    else:
        text = ''.join(item['text'])
    return text.replace('\r', '')

def get_renderers(data: Union[dict, list]) -> list:
    renderers = find_all(data, '$..backstagePostThreadRenderer')
    return renderers


def get_posts_renderers(data: Union[dict, list]) -> list:
    renderers = find_all(data, '$..post.backstagePostRenderer')
    return renderers


def get_shared_posts_renderers(data: Union[dict, list]) -> list:
    renderers = find_all(data, '$..post.sharedPostRenderer')
    return renderers

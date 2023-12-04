from typing import List, Optional

from pydantic import BaseModel

from plugins.youtube.common import find_all, find_one, parse_navigation_endpoint


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
            text = parse_navigation_endpoint(item)
        else:
            text = ''.join(item['text'])
        return text.replace('\r', '')

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

import datetime
import json
import logging
from pathlib import Path
from textwrap import shorten
from time import perf_counter_ns
from typing import List, Optional, Tuple

import dateutil.parser
from pydantic import BaseModel

from avtdl.core.interfaces import MAX_REPR_LEN, Record
from avtdl.core.utils import find_one

local_logger = logging.getLogger().getChild('twitter_extractors')


class TwitterRecord(Record):
    """
    Single post as parsed from Twitter instance

    Depending on the tweet type (regular, retweet, reply, quote) some fields might be empty
    """
    uid: str
    """tweet id"""
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
    """tweet text. Links are unshortened if possible"""
    attachments: List[str] = []
    """list of links to attached images or video thumbnails"""
    images: List[str] = []
    """list of links to attached images"""
    videos: List[str] = []
    """list of links to attached videos and gifs"""
    replying_to_username: Optional[str] = None
    """for replies, name of the user that got replied to"""
    retweet: Optional['TwitterRecord'] = None
    """for retweets, nested TwitterRecord containing tweet that was retweeted"""
    quote: Optional['TwitterRecord'] = None
    """for quotes, nested TwitterRecord containing tweet being quoted"""

    def __repr__(self):
        return f'TwitterRecord(author="{self.author}", url="{self.url}", text="{shorten(self.text, MAX_REPR_LEN)}")'

    def __str__(self):
        tweet = self.retweet or self
        elements = []
        elements.append(tweet.url)
        if self.retweet is not None:
            retweet_header = f'[{self.published.strftime("%Y-%m-%d %H:%M:%S")}] {self.author} (@{self.username}) has retweeted:'
            elements.append(retweet_header)
        if tweet.replying_to_username is not None:
            reply_header = f'Replying to @{tweet.replying_to_username}:'
            elements.append(reply_header)
        elements.append(f'[{tweet.published.strftime("%Y-%m-%d %H:%M:%S")}] {tweet.author} (@{tweet.username}):')
        elements.append(tweet.text)
        if tweet.attachments:
            elements.append('\n'.join(tweet.attachments))
        if tweet.quote:
            elements.append('\nReferring to ')
            elements.append(str(tweet.quote))
        return '\n'.join(elements)


    def discord_embed(self) -> List[dict]:
        text_items = [self.text]
        if len(self.attachments) > 1:
            text_items.extend(self.attachments)
        if self.quote:
            text_items.append('\nReferring to ')
            text_items.append(str(self.quote))
        text = '\n'.join(text_items)

        if self.retweet is not None:
            author = f'[{self.published.strftime("%Y-%m-%d %H:%M:%S")}] {self.author} (@{self.username}) has retweeted:'
            title =  f'{self.retweet.author} ({self.retweet.username})'
            if self.replying_to_username:
                title += f', replying to @{self.replying_to_username}:'
            avatar = self.retweet.avatar_url
            timestamp = self.retweet.published.isoformat()
        else:
            author = f'{self.author} ({self.username})'
            title = f'Replying to @{self.replying_to_username}:' if self.replying_to_username else self.url
            avatar = self.avatar_url
            timestamp = self.published.isoformat()

        embed = {
            'title': title,
            'description': text,
            'url': self.url,
            'color': None,
            'author': {'name': author, 'icon_url': avatar},
            'timestamp': timestamp,
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

class UserInfo(BaseModel):
    rest_id: str
    handle: str
    name: str
    description: str
    avatar_url: str
    banner_url: Optional[str] = None
    location: str

    @classmethod
    def from_data(cls, data: dict) -> 'UserInfo':
        result = find_one(data, '$.data.user.result')
        if result is None:
            raise ValueError(f'failed to parse data into {cls.__name__}: no "result" property')
        return cls.from_result(result)

    @classmethod
    def from_result(cls, result: dict) -> 'UserInfo':
        typename = result.get('__typename')
        if typename != 'User':
            raise ValueError(f'failed to parse result into {cls.__name__}: __typename is "{typename}, expected "User"')
        rest_id = result['rest_id']

        legacy = result['legacy']

        handle = legacy['screen_name']
        name = legacy['name']
        description = legacy['description']
        avatar_url = legacy['profile_image_url_https'].replace('_normal', '')
        banner_url = legacy.get('profile_banner_url')
        location = legacy['location']
        return cls(rest_id=rest_id, handle=handle, name=name, description=description, avatar_url=avatar_url, banner_url=banner_url, location=location)


def extract_contents(data: str) -> Tuple[List[dict], Optional[str]]:
    """Picks all tweets, individual and inside conversations. Also picks bottom cursor value"""
    tweets: List[dict] = []
    continuation = None

    def handle_item(obj):
        nonlocal continuation
        # drop pinned tweet
        if "type" in obj and obj['type'] == 'TimelinePinEntry':
            try:
                pinned = obj['entry']['content']['itemContent']['tweet_results']
            except IndexError:
                local_logger.warning(f'TimelinePinEntry is present but no "tweet_result" was found inside. Raw data:\n"{obj}"')
                return obj
            if tweets and tweets[-1] == pinned:
                # when handle_item() gets called for "TimelinePinEntry", it must have been
                # already called for the "tweet_results" inside it, which then should be
                # on top of the "tweets" list now
                tweets.pop()
            else:
                local_logger.warning(f'TimelinePinEntry is present but pinned tweet was not collected. Raw data:\n"{obj}"')
            return obj
        if '__typename' not in obj:
            return obj
        # tweet
        if 'tweet_results' in obj:
            tweets.append(obj['tweet_results'])
            return obj
        # cursor
        typename = obj['__typename']
        if typename == 'TimelineTimelineCursor':
            if obj.get('cursorType') == 'Bottom':
                continuation = obj.get('value')
            return obj
        return obj

    decoder = json.JSONDecoder(object_hook=handle_item)
    data, _ = decoder.raw_decode(data)
    return tweets, continuation


def parse_tweet(tweet_results: dict):
    tweet_result = tweet_results.get('result')
    if tweet_result is None:
        raise ValueError(f'failed to parse tweet: no "result"')
    typename = tweet_result.get('__typename')
    if typename == 'TweetWithVisibilityResults':
        tweet_result = tweet_result['tweet']
    elif typename != 'Tweet':
        raise ValueError(f'failed to parse tweet: __typename is "{typename}, expected "Tweet"')
    rest_id = tweet_result['rest_id']

    try:
        user_result = tweet_result['core']['user_results']['result']
    except (KeyError, TypeError):
        raise ValueError(f'failed to parse tweet: no user_result found')
    try:
        user = UserInfo.from_result(user_result)
    except Exception as e:
        raise ValueError(f'failed to parse tweet: {e}')
    url = f'https://twitter.com/{user.handle}/status/{rest_id}'

    legacy = tweet_result['legacy']
    published = dateutil.parser.parse(legacy['created_at'])

    try:
        text = tweet_text(tweet_result)
    except Exception as e:
        raise ValueError(f'failed to parse tweet: {e}')

    attachments, images, videos = parse_media(tweet_result) or []

    replying_to_username: Optional[str] = legacy.get('in_reply_to_screen_name')

    retweet_result = legacy.get('retweeted_status_result')
    retweet = parse_tweet(retweet_result) if retweet_result else None
    if retweet is not None:
        text = retweet.text

    quote_tesult = tweet_result.get('quoted_status_result')
    quote = parse_tweet(quote_tesult) if quote_tesult else None

    tweet = TwitterRecord(
        uid=rest_id,
        url=url,
        author=user.name,
        username=user.handle,
        avatar_url=user.avatar_url,
        published=published,
        text=text,
        attachments=attachments,
        images=images,
        videos=videos,
        replying_to_username=replying_to_username,
        retweet=retweet,
        quote=quote
    )
    return tweet


def parse_media(tweet_result: dict) -> Tuple[List[str], List[str], List[str]]:
    try:
        legacy = tweet_result['legacy']
    except KeyError as e:
        raise ValueError(f'failed to parse tweet media: no {e} found')
    media = (legacy.get('entities') or {}).get('media', [])
    extended_media = (legacy.get('extended_entities') or {}).get('media', [])
    if not media and not extended_media:
        return [], [], []
    attachments = [media_item['media_url_https'] for media_item in media]
    images = []
    videos = []
    for item in media:
        item_type = item['type']
        if item_type == 'photo':
            media_url = item['media_url_https']
            images.append(media_url)
        elif item_type in ['video', 'animated_gif']:
            try:
                variants = item['video_info']['variants']
            except (KeyError, TypeError):
                variants = []
            best_variant = max(variants, key=lambda variant: variant.get('bitrate', -1))
            video_url = best_variant['url']
            videos.append(video_url)
        else:
            msg = f'unknown media type {item_type}. Raw tweet_result:\n"{tweet_result}"'
            local_logger.debug(msg)
    return attachments, images, videos


def tweet_text(tweet_result: dict) -> str:
    try:
        legacy = tweet_result['legacy']
        text = legacy['full_text']
        entities = legacy['entities']
    except (KeyError, TypeError) as e:
        raise ValueError(f'failed to parse tweet text: no {e} found')
    urls = entities.get('urls', [])
    for url in urls:
        try:
            text = text.replace(url['url'], url['expanded_url'])
        except KeyError:
            pass
    img_urls = entities.get('media', [])
    for url in img_urls:
        try:
            text = text.replace(url['url'], '\n' + url['expanded_url'])
        except KeyError:
            pass
    return text


def parse_timeline(text: str) -> Tuple[List[TwitterRecord], Optional[str]]:
    tweets = []
    raw_tweets, continuation = extract_contents(text)
    for tweet_result in raw_tweets:
        tweet = parse_tweet(tweet_result)
        tweets.append(tweet)
    return tweets, continuation


def main():
    for name in Path('.').glob('*.json'):
        tweets = []
        print(f'*** {name} ***')
        with open(name, 'rt', encoding='utf8') as fp:
            text = fp.read()
        t1 = perf_counter_ns()
        raw_tweets, continuation = extract_contents(text)
        t2 = perf_counter_ns()
        for tweet_result in raw_tweets:
            tweet = parse_tweet(tweet_result)
            tweets.append(tweet)
        t3 = perf_counter_ns()
        print(f'extract: {(t2 - t1) / 10 ** 6}, parse: {(t3 - t2) / 10 ** 6}')
        ...
        with Path(f'D:/test/{name}.txt').open('wt', encoding='utf8') as fp:
            for tweet in tweets:
                fp.write(str(tweet.as_timezone()))
                fp.write('\n\n' + '*' * 80 + '\n')
    ...


if __name__ == '__main__':
    main()

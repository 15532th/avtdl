import datetime
import json
import logging
import re
from textwrap import shorten
from typing import Any, Dict, List, Optional, Tuple, Union

import dateutil.parser
from pydantic import BaseModel, ValidationError

from avtdl.core.config import format_validation_error
from avtdl.core.interfaces import MAX_REPR_LEN, Record
from avtdl.core.utils import Fmt, find_one

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
    space_id: Optional[str] = None
    """for tweets or retweets mentioning Twitter Space url contains unique part of the url, otherwise empty"""

    def model_post_init(self, __context: Any) -> None:
        self.space_id = find_space_id(self.text)

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

    def get_uid(self) -> str:
        return self.url

    def discord_embed(self) -> List[dict]:
        quote = self.quote if self.retweet is None else self.retweet.quote

        text_items = [self.text]
        if len(self.attachments) > 1:
            text_items.extend(self.attachments)
        if quote:
            text_items.append('\nReferring to ')
            text_items.append(str(quote))
        text = '\n'.join(text_items)

        if self.retweet is not None:
            author = f'[{self.published.strftime("%Y-%m-%d %H:%M:%S")}] {self.author} (@{self.username}) has retweeted:'
            title = f'{self.retweet.author} ({self.retweet.username})'
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
            'author': {'name': author, 'icon_url': avatar, 'url': user_url_by_id(self.username)},
            'timestamp': timestamp,
        }

        def format_attachments(post_url: str, attachments: List[str]) -> List[dict]:
            return [{'url': post_url, 'image': {'url': attachment}} for attachment in attachments]

        if self.attachments:
            images = format_attachments(self.url, self.attachments)
            embed['image'] = images.pop(0)['image']
            embeds = [embed, *images]
        elif quote and quote.attachments:
            images = format_attachments(quote.url, quote.attachments)
            embed['image'] = images.pop(0)['image']
            embeds = [embed, *images]
        else:
            embeds = [embed]
        return embeds


def find_space_id(text: str) -> Optional[str]:
    rest_id_match = re.search(r'/i/spaces/([0-9a-zA-Z]+)/?', text)
    if rest_id_match is None:
        return None
    return rest_id_match.groups()[0]


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


def parse_tweet(tweet_results: dict) -> TwitterRecord:
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
    url = tweet_url_by_id(user.handle, rest_id)

    legacy = tweet_result.get('legacy')
    if legacy is None:
        raise ValueError(f'failed to parse tweet: no "legacy" field in tweet_result')

    published = tweet_timestamp(rest_id) or dateutil.parser.parse(legacy['created_at'])

    try:
        text = note_text(tweet_result) or tweet_text(tweet_result)
    except Exception as e:
        raise ValueError(f'failed to parse tweet text: {e}')

    attachments, images, videos = parse_media(tweet_result) or []

    replying_to_username: Optional[str] = legacy.get('in_reply_to_screen_name')

    retweet_result = legacy.get('retweeted_status_result')
    retweet = parse_tweet(retweet_result) if retweet_result else None
    if retweet is not None:
        text = retweet.text

    quote_tesults = tweet_result.get('quoted_status_result') or {}
    quote_tesult = quote_tesults.get('result') or {}
    if quote_tesult.get('__typename') == 'Tweet':
        quote: Optional[TwitterRecord] = parse_tweet(quote_tesults)
    elif quote_tesult.get('__typename') == 'TweetTombstone':
        quote = parse_quoted_tombstone(tweet_result)
    elif legacy.get('quoted_status_id_str') and not retweet:
        # 'quoted_status_result' might be empty despite quote fields being present in 'legacy'
        quote = parse_quoted_tombstone(tweet_result)
    else:
        quote = None

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


def parse_quoted_tombstone(tweet_result: dict) -> Optional[TwitterRecord]:
    """given valid tweet that quotes tombstone, try to extract some data on the tombstone"""
    try:
        text = tweet_result['quoted_status_result']['result']['tombstone']['text']['text']
    except (KeyError, TypeError):
        text = 'Tweet content is unavailable'

    try:
        legacy = tweet_result['legacy']
        url = legacy['quoted_status_permalink']['expanded']
        rest_id = legacy['quoted_status_id_str']
        published = tweet_timestamp(rest_id) or datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)

        username_match = re.search(r'/([^/]+)/status/\d+', url)
        username = username_match.groups()[0] if username_match else 'username_missing'
    except Exception:
        return None

    return TwitterRecord(
        uid=rest_id,
        url=url,
        author=username,
        username=username,
        published=published,
        text=text)


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


def replace_urls(text: str, urls: List[Dict[str, str]]) -> str:
    for url in urls:
        try:
            text = text.replace(url['url'], url['expanded_url'])
        except KeyError:
            pass
    return text


def tweet_text(tweet_result: dict) -> str:
    try:
        legacy = tweet_result['legacy']
        text = legacy['full_text']
        entities = legacy['entities']
    except (KeyError, TypeError) as e:
        raise ValueError(f'failed to parse tweet text: no {e} found')
    urls = entities.get('urls', [])
    text = replace_urls(text, urls)
    img_urls = entities.get('media', [])
    # remove media links from the bottom of the tweet
    for url in img_urls:
        try:
            text = text.replace(url['url'], '')
        except KeyError:
            pass
    return text


def note_text(tweet_result: dict) -> Optional[str]:
    note = tweet_result.get('note_tweet')
    if note is None:
        return None
    try:
        result = note['note_tweet_results']['result']
    except (KeyError, TypeError):
        local_logger.debug(f'failed to parse note_tweet: no result. Raw tweet_result: "{tweet_result}"')
        return None
    text = result.get('text')
    if text is None:
        local_logger.debug(f'failed to parse note_tweet: no text. Raw tweet_result: "{tweet_result}"')
        return None
    urls = result.get('entity_set', {}).get('urls', [])
    if urls:
        text = replace_urls(text, urls)
    return text


def maybe_date(timestamp: Union[int, str, None], tz: Optional[datetime.timezone] = datetime.timezone.utc) -> Optional[datetime.datetime]:
    if timestamp is None:
        return None
    try:
        timestamp = int(timestamp)
        date = datetime.datetime.fromtimestamp(timestamp / 1000, tz=tz)
        return date
    except Exception:
        return None


def tweet_timestamp(tweet_id: Union[str, int]) -> Optional[datetime.datetime]:
    try:
        tweet_id = int(tweet_id)
    except ValueError:
        return None
    if tweet_id < 30_000_000_000:  # old incremental id
        return None
    try:
        timestamp = ((tweet_id >> 22) + 1288834974657) / 1000
        return datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)
    except Exception:
        return None


def parse_timeline(text: str) -> Tuple[List[TwitterRecord], Optional[str]]:
    tweets = []
    raw_tweets, continuation = extract_contents(text)
    for tweet_result in raw_tweets:
        tweet = parse_tweet(tweet_result)
        tweets.append(tweet)
    return tweets, continuation


def space_url_by_id(space_id: str) -> str:
    return f'https://twitter.com/i/spaces/{space_id}'


def user_url_by_id(handle: str) -> str:
    return f'https://twitter.com/{handle}'


def tweet_url_by_id(handle: str, rest_id: str) -> str:
    return f'https://twitter.com/{handle}/status/{rest_id}'


def parse_space(data: dict) -> 'TwitterSpaceRecord':
    metadata = find_one(data, '$..metadata')
    if metadata is None:
        raise ValueError(f'failed to parse space: no metadata found')
    try:
        user_result = metadata['creator_results']['result']
        user = UserInfo.from_result(user_result)
    except (KeyError, TypeError):
        raise ValueError(f'failed to parse space: no creator_result found')
    except ValidationError as e:
        err = format_validation_error(e)
        raise ValueError(f'failed to parse space author details: {err}')
    try:
        uid = metadata.get('rest_id')
        record = TwitterSpaceRecord(
            uid=uid,
            url=space_url_by_id(uid),
            state=metadata.get('state'),
            media_key=metadata.get('media_key'),
            title=metadata.get('title', ''),
            author=user.name,
            username=user.handle,
            avatar_url=user.avatar_url,
            published=maybe_date(metadata.get('created_at')) or maybe_date(metadata.get('started_at')) or datetime.datetime.now(tz=datetime.timezone.utc),
            scheduled=maybe_date(metadata.get('scheduled_start')),
            started=maybe_date(metadata.get('started_at')),
            ended=maybe_date(metadata.get('ended_at')),
            updated=maybe_date(metadata.get('updated_at')),
            recording_enabled=metadata.get('is_space_available_for_replay', False)
        )
    except ValidationError as e:
        err = format_validation_error(e)
        raise ValueError(f'failed to parse space: {err}')
    return record


def parse_media_url(data: dict) -> Optional[str]:
    source = data.get('source') or {}
    media_url = source.get('location') or source.get('noRedirectPlaybackUrl') or None
    return media_url


class TwitterSpaceRecord(Record):
    uid: str
    """space id"""
    url: str
    """url of the space"""
    state: str
    """description of current status of a space: upcoming, ongoing, ended"""
    media_key: str
    """id that can be used to fetch url of the underlying HLS stream"""
    media_url: Optional[str] = None
    """link to the underlying stream, when available"""
    title: str
    """space title"""
    author: str
    """user's visible name"""
    username: str
    """user's handle"""
    avatar_url: Optional[str] = None
    """link to the picture used as the user's avatar"""
    published: datetime.datetime
    """timestamp of the space creation"""
    scheduled: Optional[datetime.datetime] = None
    """scheduled time for an upcoming space to start at, otherwise absent"""
    started: Optional[datetime.datetime] = None
    """timestamp of the space start, empty for upcoming spaces"""
    ended: Optional[datetime.datetime] = None
    """timestamp of the space end, empty for not yet ended spaces"""
    updated: Optional[datetime.datetime] = None
    """timestamp of the last update"""
    recording_enabled: bool = False
    """whether host enabled recording of the space. When true, archive is likely to be available"""

    def __str__(self) -> str:
        header = f'[{self.published.strftime("%Y-%m-%d %H:%M:%S")}] Twitter Space by {self.author} (@{self.username}) [{self.state}]'
        if not self.recording_enabled:
            header += ' [unarchived]'
        scheduled = f'\nscheduled at {self.scheduled}' if self.scheduled else ''
        return f'{self.url}\n{header}\n{self.title}{scheduled}'

    def __repr__(self):
        return f'TwitterSpaceRecord(author="{self.author}", url="{self.url}")'

    def get_uid(self) -> str:
        return self.uid

    def discord_embed(self) -> List[dict]:
        author = f'{self.author} ({self.username})'
        fields: List[dict] = []
        state_text = ' '.join(re.split('(?=[A-Z])', self.state)).strip().lower()
        fields.append({'name': f'[{state_text}]', 'value': '', 'inline': True})
        if not self.recording_enabled:
            fields.append({'name': '[unarchived]', 'value': '', 'inline': True})
        if self.scheduled:
            fields.append({'name': 'scheduled', 'value': Fmt.dtf(self.scheduled), 'inline': False})
        if self.started:
            fields.append({'name': 'started', 'value': Fmt.dtf(self.started), 'inline': False})
        if self.ended:
            fields.append({'name': 'ended', 'value': Fmt.dtf(self.ended), 'inline': False})

        embed = {
            'title': self.title or self.url,
            'description': self.media_url,
            'url': self.url,
            'color': None,
            'author': {'name': author, 'icon_url': self.avatar_url, 'url': user_url_by_id(self.username)},
            'timestamp': self.published.isoformat(),
            'fields': fields
        }

        return [embed]

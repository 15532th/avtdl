import asyncio
import datetime
import json
from abc import abstractmethod
from typing import Any, List, Optional, Sequence, Tuple

import aiohttp
from pydantic import Field, FilePath

from avtdl.core.interfaces import Record
from avtdl.core.monitors import PagedFeedMonitor, PagedFeedMonitorConfig, PagedFeedMonitorEntity
from avtdl.core.plugins import Plugins
from avtdl.plugins.twitter.endpoints import LatestTimelineEndpoint, TimelineEndpoint, TwitterEndpoint, \
    UserIDEndpoint, UserLikesEndpoint, UserTweetsEndpoint, UserTweetsRepliesEndpoint
from avtdl.plugins.twitter.extractors import TwitterRecord, extract_contents, parse_tweet

Plugins.register('twitter.user', Plugins.kind.ASSOCIATED_RECORD)(TwitterRecord)
Plugins.register('twitter.home', Plugins.kind.ASSOCIATED_RECORD)(TwitterRecord)


@Plugins.register('twitter.user', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('twitter.home', Plugins.kind.ACTOR_CONFIG)
class TwitterMonitorConfig(PagedFeedMonitorConfig):
    pass


class TwitterMonitorEntity(PagedFeedMonitorEntity):
    cookies_file: FilePath
    """path to a text file containing cookies in Netscape format"""
    update_interval: float = 1800
    """how often the monitored url will be checked, in seconds"""
    url: str = 'https://twitter.com'
    """Twitter domain name"""
    rate_limited_until: Optional[datetime.datetime] = Field(exclude=True, default=None)
    """internal variable to persist state between updates. Used to store time when currently active rate limit expires"""
    adjust_update_interval: bool = Field(exclude=True, default=False)
    """this monitor handles adjusting interval itself, so it is disabled to make sure superclass won't overwrite it"""


class TwitterMonitor(PagedFeedMonitor):
    """Base class for concrete Twitter monitors"""

    MIN_CONTINUATION_DELAY: int = 1

    async def handle_first_page(self, entity: TwitterMonitorEntity, session: aiohttp.ClientSession) -> Tuple[Optional[Sequence[Record]], Optional[Any]]:
        raw_page = await self._get_page(entity, session, continuation=None)
        if raw_page is None:
            return None, None
        records, continuation = await self._parse_entries(raw_page)
        if not records:
            continuation = None
        return records, continuation

    async def handle_next_page(self, entity: TwitterMonitorEntity, session: aiohttp.ClientSession, context: Optional[Any]) -> Tuple[Optional[Sequence[Record]], Optional[Any]]:
        continuation: Optional[str] = context
        if continuation is None:
            return None, None
        raw_page = await self._get_page(entity, session, continuation)
        if raw_page is None:
            return None, None
        records, continuation = await self._parse_entries(raw_page)
        if not records:
            # for user timeline Twitter keeps responding with cursor even if there is no tweets on continuation page
            continuation = None
        return records, continuation

    @abstractmethod
    async def _get_page(self, entity: TwitterMonitorEntity, session: aiohttp.ClientSession, continuation: Optional[str]) -> Optional[str]:
        """Retrieve raw response string from endpoint"""

    async def _parse_entries(self, page: str) -> Tuple[List[TwitterRecord], Optional[str]]:
        raw_tweets, continuation = extract_contents(page)
        await asyncio.sleep(0)
        records = []
        for tweet_result in raw_tweets:
            try:
                record = parse_tweet(tweet_result)
            except Exception as e:
                self.logger.exception(f'error parsing tweet: {e}')
                self.logger.debug(f'raw tweet_result: {tweet_result}')
            else:
                records.append(record)
        return records, continuation


@Plugins.register('twitter.home', Plugins.kind.ACTOR_ENTITY)
class TwitterHomeMonitorEntity(TwitterMonitorEntity):
    following: bool = True
    """monitor tweets from the "Following" tab instead of "For you" """


@Plugins.register('twitter.home', Plugins.kind.ACTOR)
class TwitterHomeMonitor(TwitterMonitor):
    """
    Monitor for Twitter home timeline

    Monitors tweets on Twitter Home Timeline, either the "Following"
    of the "For you" tab.

    Requires login cookies from a logged in Twitter account to work.
    """

    async def _get_page(self, entity: TwitterHomeMonitorEntity, session: aiohttp.ClientSession, continuation: Optional[str]) -> Optional[str]:
        endpoint = LatestTimelineEndpoint if entity.following else TimelineEndpoint
        data = await endpoint.request(self.logger, session, entity.url, session.cookie_jar, continuation)
        return data

@Plugins.register('twitter.user', Plugins.kind.ACTOR_ENTITY)
class TwitterUserMonitorEntity(TwitterMonitorEntity):
    user: str
    """user handle"""
    with_replies: bool = True
    """include replies by monitored user"""
    only_likes: bool = False
    """monitor tweets liked by the user instead of user's own tweets"""
    user_id: Optional[str] = Field(exclude=True, default=None)
    """internal variable to persist state between updates. Used to cache user id for monitored user"""


@Plugins.register('twitter.user', Plugins.kind.ACTOR)
class TwitterUserMonitor(TwitterMonitor):
    """
    Monitor for user tweets

    Monitors timeline of a user for new tweets, including retweets and quotes.
    Enabling `with_replies` will additionally include replies posted by the user.

    With `only_likes` option enabled tweets from the "Likes" tab are collected
    instead of user's own tweets.

    Requires login cookies from a logged in Twitter account to work.
    """

    @staticmethod
    def _pick_endpoint(entity: TwitterUserMonitorEntity) -> type[TwitterEndpoint]:
        if entity.only_likes:
            return UserLikesEndpoint
        elif entity.with_replies:
            return UserTweetsRepliesEndpoint
        else:
            return UserTweetsEndpoint

    async def _get_user_id(self, entity: TwitterUserMonitorEntity, session: aiohttp.ClientSession) -> Optional[str]:
        if entity.user_id is None:
            text = await UserIDEndpoint.request(self.logger, session, entity.url, session.cookie_jar, entity.user)
            if text is None:
                return None
            try:
                data = json.loads(text)
            except json.JSONDecodeError as e:
                self.logger.warning(f'[{entity.name}] failed to parse user_id for @{entity.user}: {e}. Raw response: {text}')
                return None
            user_id = UserIDEndpoint.get_user_id(data)
            entity.user_id = user_id
        return entity.user_id

    async def _get_page(self, entity: TwitterUserMonitorEntity, session: aiohttp.ClientSession, continuation: Optional[str]) -> Optional[str]:
        user_id = await self._get_user_id(entity, session)
        if user_id is None:
            self.logger.warning(f'failed to get user id from user handle for "{entity.user}", aborting update')
            return None
        endpoint = self._pick_endpoint(entity)
        data = await endpoint.request(self.logger, session, entity.url, session.cookie_jar, user_id, continuation)
        return data

import asyncio
import datetime
import json
import traceback
from abc import abstractmethod
from typing import Any, List, Optional, Sequence, Tuple

from pydantic import Field, FilePath, PositiveFloat

from avtdl.core.interfaces import Record
from avtdl.core.monitors import PagedFeedMonitor, PagedFeedMonitorConfig, PagedFeedMonitorEntity
from avtdl.core.plugins import Plugins
from avtdl.core.request import DataResponse, HttpClient
from avtdl.plugins.twitter.endpoints import LatestTimelineEndpoint, SearchQueryType, SearchTimelineEndpoint, \
    TimelineEndpoint, TwitterEndpoint, \
    UserIDEndpoint, UserTweetsEndpoint, UserTweetsRepliesEndpoint
from avtdl.plugins.twitter.extractors import TwitterRecord, extract_contents, parse_tweet

Plugins.register('twitter.user', Plugins.kind.ASSOCIATED_RECORD)(TwitterRecord)
Plugins.register('twitter.home', Plugins.kind.ASSOCIATED_RECORD)(TwitterRecord)
Plugins.register('twitter.search', Plugins.kind.ASSOCIATED_RECORD)(TwitterRecord)


@Plugins.register('twitter.user', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('twitter.home', Plugins.kind.ACTOR_CONFIG)
@Plugins.register('twitter.search', Plugins.kind.ACTOR_CONFIG)
class TwitterMonitorConfig(PagedFeedMonitorConfig):
    pass


class TwitterMonitorEntity(PagedFeedMonitorEntity):
    cookies_file: FilePath
    """path to a text file containing cookies in Netscape format"""
    update_interval: PositiveFloat = 1800
    """how often the monitored url will be checked, in seconds"""
    url: str = 'https://twitter.com'
    """Twitter domain name"""
    rate_limited_until: Optional[datetime.datetime] = Field(exclude=True, default=None)
    """internal variable to persist state between updates. Used to store time when currently active rate limit expires"""
    adjust_update_interval: bool = Field(exclude=True, default=False)
    """this monitor handles adjusting interval itself, so it is disabled to make sure superclass won't overwrite it"""


class TwitterMonitor(PagedFeedMonitor):
    """Base class for concrete Twitter monitors"""

    async def handle_first_page(self, entity: TwitterMonitorEntity, client: HttpClient) -> Tuple[Optional[Sequence[Record]], Optional[Any]]:
        raw_page = await self._get_page(entity, client, continuation=None)
        if raw_page is None:
            return None, None
        records, continuation = await self._parse_entries(raw_page)
        if not records:
            continuation = None
        return records, continuation

    async def handle_next_page(self, entity: TwitterMonitorEntity, client: HttpClient,
                               context: Optional[Any]) -> Tuple[Optional[Sequence[Record]], Optional[Any]]:
        continuation: Optional[str] = context
        if continuation is None:
            return None, None
        raw_page = await self._get_page(entity, client, continuation)
        if raw_page is None:
            return None, None
        records, continuation = await self._parse_entries(raw_page)
        if not records:
            # for user timeline Twitter keeps responding with cursor even if there is no tweets on continuation page
            continuation = None
        return records, continuation

    @abstractmethod
    async def _get_page(self, entity: TwitterMonitorEntity, client: HttpClient, continuation: Optional[str]) -> Optional[str]:
        """Retrieve raw response string from endpoint"""

    async def _parse_entries(self, page: str) -> Tuple[List[TwitterRecord], Optional[str]]:
        if not page:
            self.logger.exception(f'failed to extract tweets from page: page is empty, no data to parse')
            return [], None
        try:
            raw_tweets, continuation = extract_contents(page)
        except Exception as e:
            self.logger.exception(f'failed to extract tweets from page: {e}')
            self.logger.debug(f'raw page: {page}')
            return [], None
        await asyncio.sleep(0)
        records = []
        empty = 0
        for tweet_result in raw_tweets:
            if not tweet_result:
                empty += 1
                continue
            try:
                record = parse_tweet(tweet_result)
            except Exception as e:
                self.logger.warning(f'error parsing tweet: {e}')
                self.logger.debug(f'stack trace: {traceback.format_exc()}')
                self.logger.debug(f'raw tweet_result: {tweet_result}')
            else:
                records.append(record)

        if empty == 1:
            self.logger.debug(f'skipping empty tweet result')
        elif empty > 1:
            self.logger.warning(f'got {empty} empty tweet results')
        if not records:
            self.logger.warning(f'no records on page')
            self.logger.debug(f'raw page: {page}')
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

    async def _get_page(self, entity: TwitterHomeMonitorEntity, client: HttpClient, continuation: Optional[str]) -> Optional[str]:
        endpoint = LatestTimelineEndpoint if entity.following else TimelineEndpoint
        request_details = endpoint.prepare(entity.url, client.cookie_jar, continuation)
        response = await self.request_endpoint(entity, client, request_details)
        return response.text if response is not None else None


@Plugins.register('twitter.user', Plugins.kind.ACTOR_ENTITY)
class TwitterUserMonitorEntity(TwitterMonitorEntity):
    user: str
    """user handle"""
    with_replies: bool = True
    """include replies by monitored user"""
    only_likes: bool = False
    """user likes are long disabled and this option doesn't work"""
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
        if entity.with_replies:
            return UserTweetsRepliesEndpoint
        else:
            return UserTweetsEndpoint

    async def _get_user_id(self, entity: TwitterUserMonitorEntity, client: HttpClient) -> Optional[str]:
        if entity.user_id is None:
            request_details = UserIDEndpoint.prepare(entity.url, client.cookie_jar, entity.user)
            response = await self.request_endpoint(entity, client, request_details)
            if not isinstance(response, DataResponse):
                return None
            try:
                data = json.loads(response.text)
            except json.JSONDecodeError as e:
                self.logger.warning(f'[{entity.name}] failed to parse user_id for @{entity.user}: {e}. Raw response: {response.text}')
                return None
            user_id = UserIDEndpoint.get_user_id(data)
            entity.user_id = user_id
        return entity.user_id

    async def _get_page(self, entity: TwitterUserMonitorEntity, client: HttpClient, continuation: Optional[str]) -> Optional[str]:
        user_id = await self._get_user_id(entity, client)
        if user_id is None:
            self.logger.warning(f'failed to get user id from user handle for "{entity.user}", aborting update')
            return None
        endpoint = self._pick_endpoint(entity)
        request_details = endpoint.prepare(entity.url, client.cookie_jar, user_id, continuation)
        response = await self.request_endpoint(entity, client, request_details)
        return response.text if response is not None else None


@Plugins.register('twitter.search', Plugins.kind.ACTOR_ENTITY)
class TwitterSearchEntity(TwitterMonitorEntity):
    query: str
    """hashtag or a search query"""
    query_type: SearchQueryType = SearchQueryType.LATEST
    """search results tab. One of "Latest", "Top" and "Media" """


@Plugins.register('twitter.search', Plugins.kind.ACTOR)
class TwitterSearchMonitor(TwitterMonitor):
    """
    Monitor Twitter hashtag or search query

    Monitors tweets for given hashtag or results of a search query.
    Queries constructed with the "Advanced search" menu should work as well.
    Note that only default value for `query_type` option produces
    chronologically ordered results, and selecting other values might
    result to new but unpopular tweets never getting picked.

    Requires login cookies from a logged in Twitter account to work.
    """

    async def _get_page(self, entity: TwitterSearchEntity, client: HttpClient, continuation: Optional[str]) -> Optional[str]:
        request_details = SearchTimelineEndpoint.prepare(entity.url, client.cookie_jar, entity.query, entity.query_type, continuation)
        response = await self.request_endpoint(entity, client, request_details)
        return response.text if response is not None else None

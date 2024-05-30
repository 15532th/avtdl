from typing import Any, List, Optional, Sequence, Tuple

import aiohttp
from pydantic import Field, FilePath

from avtdl.core import utils
from avtdl.core.interfaces import Record
from avtdl.core.monitors import PagedFeedMonitor, PagedFeedMonitorConfig, PagedFeedMonitorEntity
from avtdl.core.plugins import Plugins
from avtdl.plugins.twitter.endpoints import UserIDEndpoint, UserTweetsRepliesEndpoint, get_rate_limit_delay, get_user_id
from avtdl.plugins.twitter.extractors import TwitterRecord, extract_contents, parse_tweet

Plugins.register('twitter.user', Plugins.kind.ASSOCIATED_RECORD)(TwitterRecord)


@Plugins.register('twitter.user', Plugins.kind.ACTOR_CONFIG)
class TwitterMonitorConfig(PagedFeedMonitorConfig):
    pass


@Plugins.register('twitter.user', Plugins.kind.ACTOR_ENTITY)
class TwitterMonitorEntity(PagedFeedMonitorEntity):
    cookies_file: FilePath
    """path to a text file containing cookies in Netscape format"""
    user: str
    """user handle"""
    user_id: Optional[str] = Field(exclude=True, default=None)
    """internal variable to persist state between updates. Used to cache user id for monitored user"""
    adjust_update_interval: bool = Field(exclude=True, default=False)
    """this monitor handles adjusting interval itself, so it is disabled to make sure superclass won't overwrite it"""
    update_interval: float = 1800
    """how often the monitored url will be checked, in seconds"""


@Plugins.register('twitter.user', Plugins.kind.ACTOR)
class TwitterUserMonitor(PagedFeedMonitor):
    """

    """

    async def handle_first_page(self, entity: TwitterMonitorEntity, session: aiohttp.ClientSession) -> Tuple[Optional[Sequence[Record]], Optional[Any]]:
        raw_page = await self._get_page(entity, session, continuation=None)
        if raw_page is None:
            return None, None
        records, continuation = self._parse_entries(raw_page)
        if not records:
            continuation = None
        return records, continuation

    async def handle_next_page(self, entity: TwitterMonitorEntity, session: aiohttp.ClientSession, context: Optional[Any]) -> Tuple[Optional[Sequence[Record]], Optional[Any]]:
        continuation: Optional[str] = context  # type: ignore
        if continuation is None:
            return None, None
        raw_page = await self._get_page(entity, session, continuation)
        if raw_page is None:
            return None, None
        records, continuation = self._parse_entries(raw_page)
        if not records:
            # for user timeline twitter keeps responding with cursor even if there is no tweets on continuation page
            continuation = None
        return records, continuation

    def get_record_id(self, record: TwitterRecord) -> str:
        return record.url

    async def _get_user_id(self, entity: TwitterMonitorEntity, session: aiohttp.ClientSession) -> Optional[str]:
        if entity.user_id is None:
            r = UserIDEndpoint.prepare(session.cookie_jar, entity.user)
            # does not check for rate x-rate-limit headers, exceeding limit is unlikely since the result is cached
            data = await utils.request_json(url=r.url, session=session, logger=self.logger, headers=r.headers, params=r.params, retry_times=3)
            if data is None:
                return None
            user_id = get_user_id(data)
            entity.user_id = user_id
        return entity.user_id

    async def _get_page(self, entity: TwitterMonitorEntity, session: aiohttp.ClientSession, continuation: Optional[str]) -> Optional[str]:
        user_id = await self._get_user_id(entity, session)
        if user_id is None:
            self.logger.warning(f'failed to get user id from user handle for "{entity.user}", aborting update')
            return None
        r = UserTweetsRepliesEndpoint.prepare(session.cookie_jar, user_id, continuation)
        response = await self.request_raw(entity.url, entity, session, headers=r.headers, params=r.params)
        if response is None:
            return None
        delay = get_rate_limit_delay(response.headers)
        entity.update_interval = max(entity.update_interval, delay)
        page = await response.text()
        return page

    def _parse_entries(self, page: str) -> Tuple[List[TwitterRecord], Optional[str]]:
        raw_tweets, continuation = extract_contents(page)
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


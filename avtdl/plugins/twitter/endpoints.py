# Prepare request to retrieve json data from graphql endpoints
#
# - timeline (home and chronological)
# - user tweets (without and with replies)
# - user likes
# - single tweet
#
# - user id by screen name
import abc
import datetime
import json
import logging
import urllib.parse
from dataclasses import dataclass
from http.cookiejar import CookieJar
from typing import Any, Dict, Optional, Union

import aiohttp
from multidict import CIMultiDictProxy

from avtdl.core import utils
from avtdl.core.utils import RateLimit, find_all, find_one, get_retry_after

USER_FEATURES = '{"hidden_profile_likes_enabled":true,"hidden_profile_subscriptions_enabled":true,"rweb_tipjar_consumption_enabled":true,"responsive_web_graphql_exclude_directive_enabled":true,"verified_phone_label_enabled":false,"subscriptions_verification_info_is_identity_verified_enabled":true,"subscriptions_verification_info_verified_since_enabled":true,"highlights_tweets_tab_ui_enabled":true,"responsive_web_twitter_article_notes_tab_enabled":true,"creator_subscriptions_tweet_preview_api_enabled":true,"responsive_web_graphql_skip_user_profile_image_extensions_enabled":false,"responsive_web_graphql_timeline_navigation_enabled":true}'
TWEETS_FEATURES = '{"rweb_tipjar_consumption_enabled":true,"responsive_web_graphql_exclude_directive_enabled":true,"verified_phone_label_enabled":false,"creator_subscriptions_tweet_preview_api_enabled":true,"responsive_web_graphql_timeline_navigation_enabled":true,"responsive_web_graphql_skip_user_profile_image_extensions_enabled":false,"communities_web_enable_tweet_community_results_fetch":true,"c9s_tweet_anatomy_moderator_badge_enabled":true,"tweetypie_unmention_optimization_enabled":true,"responsive_web_edit_tweet_api_enabled":true,"graphql_is_translatable_rweb_tweet_is_translatable_enabled":true,"view_counts_everywhere_api_enabled":true,"longform_notetweets_consumption_enabled":true,"responsive_web_twitter_article_tweet_consumption_enabled":true,"tweet_awards_web_tipping_enabled":false,"creator_subscriptions_quote_tweet_preview_enabled":false,"freedom_of_speech_not_reach_fetch_enabled":true,"standardized_nudges_misinfo":true,"tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled":true,"rweb_video_timestamps_enabled":true,"longform_notetweets_rich_text_read_enabled":true,"longform_notetweets_inline_media_enabled":true,"responsive_web_enhance_cards_enabled":false}'
SPACE_FEATURES = '{"spaces_2022_h2_spaces_communities":true,"spaces_2022_h2_clipping":true,"creator_subscriptions_tweet_preview_api_enabled":true,"rweb_tipjar_consumption_enabled":true,"responsive_web_graphql_exclude_directive_enabled":true,"verified_phone_label_enabled":false,"communities_web_enable_tweet_community_results_fetch":true,"c9s_tweet_anatomy_moderator_badge_enabled":true,"articles_preview_enabled":true,"responsive_web_graphql_skip_user_profile_image_extensions_enabled":false,"tweetypie_unmention_optimization_enabled":true,"responsive_web_edit_tweet_api_enabled":true,"graphql_is_translatable_rweb_tweet_is_translatable_enabled":true,"view_counts_everywhere_api_enabled":true,"longform_notetweets_consumption_enabled":true,"responsive_web_twitter_article_tweet_consumption_enabled":true,"tweet_awards_web_tipping_enabled":false,"creator_subscriptions_quote_tweet_preview_enabled":false,"freedom_of_speech_not_reach_fetch_enabled":true,"standardized_nudges_misinfo":true,"tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled":true,"rweb_video_timestamps_enabled":true,"longform_notetweets_rich_text_read_enabled":true,"longform_notetweets_inline_media_enabled":true,"responsive_web_graphql_timeline_navigation_enabled":true,"responsive_web_enhance_cards_enabled":false}'


class EndpointUrl:
    TIMELINE = 'https://twitter.com/i/api/graphql/uPv755D929tshj6KsxkSZg/HomeTimeline'
    LATEST_TIMELINE = 'https://twitter.com/i/api/graphql/70b_oNkcK9IEN13WNZv8xA/HomeLatestTimeline'
    USER_TWEETS = 'https://twitter.com/i/api/graphql/piUHOePH_uDdwbD9GkquJA/UserTweets'
    USER_TWEETS_AND_REPLIES = 'https://twitter.com/i/api/graphql/KJiZSYLD2ijyHRBmgddo8Q/UserTweetsAndReplies'
    USER_LIKES = 'https://twitter.com/i/api/graphql/W42Y54_EmIjbTEdg9mGLDQ/Likes'
    TWEET_DETAIL = 'https://twitter.com/i/api/graphql/F45teiuFI9MDxaS9UYKv-g/TweetDetail'
    USER_BY_SCREEN_NAME = 'https://twitter.com/i/api/graphql/qW5u-DAuXpMEG0zA1F7UGQ/UserByScreenName'
    AUDIOSPACE_BY_ID = 'https://twitter.com/i/api/graphql/d03OdorPdZ_sH9V3D1_yWQ/AudioSpaceById'


@dataclass
class RequestDetails:
    url: str
    params: Dict[str, Any]
    headers: Dict[str, Any]
    cookies: CookieJar

    def with_base_url(self, base_url: str) -> 'RequestDetails':
        new_url = replace_url_host(self.url, base_url)
        return RequestDetails(url=new_url, params=self.params, headers=self.headers, cookies=self.cookies)


def replace_url_host(url: str, new_host: str) -> str:
    parsed_host = urllib.parse.urlparse(new_host)
    parsed_url = urllib.parse.urlparse(url)
    scheme = parsed_url.scheme or parsed_host.scheme or 'https://'
    new_url = urllib.parse.urlunparse((scheme, parsed_host.netloc, parsed_url.path, parsed_url.params, parsed_url.query, parsed_url.fragment))
    return new_url


def get_netloc(host: str) -> str:
    return urllib.parse.urlparse(host).netloc


def get_cookie_value(jar: Union[CookieJar, aiohttp.CookieJar], name: str) -> Optional[str]:
    if isinstance(jar, CookieJar):
        found = [x for x in jar if x.name == name]
    else:
        found = [x for x in jar if x.key == name]

    if not found:
        return None
    return found[0].value


def get_auth_headers(cookies) -> dict[str, Any]:
    ct0 = get_cookie_value(cookies, 'ct0') or ''
    THE_API_KEY = 'Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs=1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA'
    headers = {
        'authorization': THE_API_KEY,
        'x-csrf-token': ct0
    }
    return headers


class TwitterRateLimit(RateLimit):

    def _submit_headers(self, headers: Union[Dict[str, str], CIMultiDictProxy[str]], logger: logging.Logger):
        try:
            self.limit_total = int(headers.get('x-rate-limit-limit', -1))
            self.limit_remaining = int(headers.get('x-rate-limit-remaining', -1))
            self.reset_at = int(headers.get('x-rate-limit-reset', -1))
        except ValueError:
            logger.warning(f'[{self.name}] error parsing rate limit headers: "{headers}"')
        else:
            logger.debug(f'[{self.name}] rate limit {self.limit_remaining}/{self.limit_total}, resets after {datetime.timedelta(seconds=self.reset_after)}')


class TwitterEndpoint(abc.ABC):
    """
    Superclass providing utility methods for concrete Endpoints

    Concrete Endpoints must implement prepare() method taking arbitrary
    arguments, that returns RequestDetails instance.

    They might use methods provided by this class for convenience,
    however, implementing them is not required.
    """
    FEATURES = TWEETS_FEATURES
    URL = 'https://twitter.com/...'

    _rate_limit: Optional[TwitterRateLimit] = None

    @classmethod
    def rate_limit(cls) -> TwitterRateLimit:
        if cls._rate_limit is None:
            cls._rate_limit = TwitterRateLimit(cls.__name__)
        return cls._rate_limit

    @staticmethod
    def get_base_variables(has_continuation: bool = False):
        raise NotImplementedError

    @classmethod
    def get_variables(cls, continuation: Optional[str] = None, count: int = 20, user_id: Optional[str] = None) -> str:
        variables = cls.get_base_variables(continuation is not None)
        if continuation is not None:
            variables['cursor'] = continuation
        variables['count'] = count
        if user_id is not None:
            variables['userId'] = user_id
        variables_text = json.dumps(variables)
        return variables_text

    @classmethod
    def prepare_for(cls, host, cookies, variables: str) -> RequestDetails:
        url = replace_url_host(cls.URL, host)

        params = {}
        params['variables'] = variables
        params['features'] = cls.FEATURES

        headers = get_auth_headers(cookies)

        details = RequestDetails(url=url, params=params, headers=headers, cookies=cookies)
        return details

    @classmethod
    @abc.abstractmethod
    def prepare(cls, *args, **kwargs) -> RequestDetails:
        """Prepare a RequestDetails object based on passed arguments"""

    @classmethod
    async def request_raw(cls, logger: logging.Logger, session: aiohttp.ClientSession, *args, **kwargs) -> Optional[aiohttp.ClientResponse]:
        r = cls.prepare(*args, **kwargs)
        if r is None:
            return None
        async with cls.rate_limit() as rate_limit:
            try:
                response = await utils.request_raw(r.url, session, logger, params=r.params, headers=r.headers, retry_times=0, raise_errors=True)
            except Exception as e:
                if isinstance(e, aiohttp.ClientResponseError):
                    rate_limit.submit_headers(e.headers, logger)
                    logger.debug(f' got code {e.status} ({e.message}) while fetching {r.url}: {e}')
                else:
                    logger.debug(f'error while fetching {r.url}: {e}')
                return None
            assert response is not None, 'request_raw() returned None despite raise_errors=True'
            rate_limit.submit_headers(response.headers, logger)
            return response

    @classmethod
    async def request(cls, logger: logging.Logger, session: aiohttp.ClientSession, *args, **kwargs) -> Optional[str]:
        response = await cls.request_raw(logger, session, *args, **kwargs)
        if response is None:
            return None
        return await response.text()


class UserIDEndpoint(TwitterEndpoint):
    URL = EndpointUrl.USER_BY_SCREEN_NAME
    FEATURES = USER_FEATURES

    @classmethod
    def prepare(cls, host: str, cookies, user_handle: str) -> RequestDetails:
        user_handle = user_handle.strip('@/')
        variables = {'screen_name': user_handle, 'withSafetyModeUserFields': True}
        variables_text = json.dumps(variables)
        return cls.prepare_for(host, cookies, variables_text)

    @staticmethod
    def get_user_id(data: dict) -> Optional[str]:
        return find_one(data, '$.data.user.result.rest_id')


class TweetDetailEndpoint(TwitterEndpoint):
    URL = EndpointUrl.TWEET_DETAIL

    @classmethod
    def prepare(cls, host: str, cookies, tweet_id: str, continuation: Optional[str] = None) -> RequestDetails:
        variables = {'focalTweetId': tweet_id, 'with_rux_injections': False, 'includePromotedContent': True,
                     'withCommunity': True, 'withQuickPromoteEligibilityTweetFields': True, 'withBirdwatchNotes': True,
                     'withVoice': True, 'withV2Timeline': True}
        if continuation:
            variables.update({'referrer': 'tweet', 'cursor': continuation})
        variables_text = json.dumps(variables)
        return cls.prepare_for(host, cookies, variables_text)


class AudioSpaceEndpoint(TwitterEndpoint):
    URL = EndpointUrl.AUDIOSPACE_BY_ID
    FEATURES = SPACE_FEATURES

    @classmethod
    def prepare(cls, host, cookies, space_id: str) -> RequestDetails:
        variables = {'id': space_id, 'isMetatagsQuery': False, 'withReplays': True, 'withListeners': True}
        variables_text = json.dumps(variables)
        return cls.prepare_for(host, cookies, variables_text)


class LiveStreamEndpoint(TwitterEndpoint):
    """Endpoint for retrieving HLS playlist for AudioSpace"""
    url = 'https://twitter.com/i/api/1.1/live_video_stream/status/{}'

    @classmethod
    def prepare(cls, host: str, cookies, media_key: str) -> RequestDetails:
        url = cls.url.format(media_key)
        url = replace_url_host(url, host)
        params = {'client': 'web', 'use_syndication_guest_id': 'false', 'cookie_set_host': get_netloc(host)}
        headers = get_auth_headers(cookies)

        details = RequestDetails(url=url, params=params, headers=headers, cookies=cookies)
        return details


class LatestTimelineEndpoint(TwitterEndpoint):
    URL = EndpointUrl.LATEST_TIMELINE

    @staticmethod
    def get_base_variables(is_continuation: bool = False):
        if not is_continuation:
            request_context = 'launch'
        else:
            request_context = 'ptr'
        variables = {'includePromotedContent': True, 'latestControlAvailable': True, 'requestContext': request_context}
        return variables

    @classmethod
    def prepare(cls, host: str, cookies, continuation: Optional[str] = None, count: int = 20) -> RequestDetails:
        variables = cls.get_variables(continuation=continuation, count=count)
        return cls.prepare_for(host, cookies, variables)


class TimelineEndpoint(LatestTimelineEndpoint):
    URL = EndpointUrl.TIMELINE

    @staticmethod
    def get_base_variables(is_continuation: bool = False):
        return {"includePromotedContent": True, "latestControlAvailable": True, "withCommunity": True}


class UserTweetsEndpoint(TwitterEndpoint):
    URL = EndpointUrl.USER_TWEETS

    @staticmethod
    def get_base_variables(is_continuation: bool = False):
        return {'includePromotedContent': True, 'withQuickPromoteEligibilityTweetFields': True, 'withVoice': True, 'withV2Timeline': True}

    @classmethod
    def prepare(cls, host: str, cookies, user_id: str, continuation: Optional[str] = None, count: int = 20) -> RequestDetails:
        variables = cls.get_variables(user_id=user_id, count=count, continuation=continuation)
        return cls.prepare_for(host, cookies, variables)


class UserTweetsRepliesEndpoint(UserTweetsEndpoint):
    URL = EndpointUrl.USER_TWEETS_AND_REPLIES

    @staticmethod
    def get_base_variables(is_continuation: bool = False):
        return {'includePromotedContent': True, 'withCommunity': True, 'withVoice': True, 'withV2Timeline': True}


class UserLikesEndpoint(UserTweetsEndpoint):
    URL = EndpointUrl.USER_LIKES

    @staticmethod
    def get_base_variables(is_continuation: bool = False):
        return {'includePromotedContent': False, 'withClientEventToken': False, 'withBirdwatchNotes': False, 'withVoice': True, 'withV2Timeline': True}


def get_rate_limit_delay(headers: Union[Dict[str, str], CIMultiDictProxy[str]], logger: Optional[logging.Logger] = None) -> int:
    logger = logger or logging.getLogger().getChild('twitter_endpoints')
    retry_after = get_retry_after(headers)
    if retry_after is not None:
        return retry_after
    try:
        limit_total = int(headers.get('x-rate-limit-limit', -1))
        limit_remaining = int(headers.get('x-rate-limit-remaining', -1))
        reset_at = int(headers.get('x-rate-limit-reset', -1))
    except ValueError as e:
        logger.debug(f'error parsing limit headers: "{headers}"')
        return 0
    now = int(datetime.datetime.now().timestamp())
    reset_after = max(0, reset_at - now)
    logger.debug(f'rate limit {limit_remaining}/{limit_total}, resets after {reset_after} (at {reset_at})')
    if limit_remaining <= 1:
        return reset_after + 1
    return 0


def get_continuation(data: dict) -> Optional[str]:
    entries = find_all(data, '$..instructions..entries..content,itemContent')
    continuation = entries[-1]['value']
    return continuation

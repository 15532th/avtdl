# Prepare request to retrieve json data from graphql endpoints
#
# - timeline (home and chronological)
# - user tweets (without and with replies)
# - user likes
# - single tweet
#
# - user id by screen name
import datetime
import json
from dataclasses import dataclass
from http.cookiejar import CookieJar
from pathlib import Path
from time import sleep
from typing import Any, Dict, Optional

from avtdl.core.utils import find_all, find_one, load_cookies

USER_FEATURES = '{"hidden_profile_likes_enabled":true,"hidden_profile_subscriptions_enabled":true,"rweb_tipjar_consumption_enabled":true,"responsive_web_graphql_exclude_directive_enabled":true,"verified_phone_label_enabled":false,"subscriptions_verification_info_is_identity_verified_enabled":true,"subscriptions_verification_info_verified_since_enabled":true,"highlights_tweets_tab_ui_enabled":true,"responsive_web_twitter_article_notes_tab_enabled":true,"creator_subscriptions_tweet_preview_api_enabled":true,"responsive_web_graphql_skip_user_profile_image_extensions_enabled":false,"responsive_web_graphql_timeline_navigation_enabled":true}'
TWEETS_FEATURES = '{"rweb_tipjar_consumption_enabled":true,"responsive_web_graphql_exclude_directive_enabled":true,"verified_phone_label_enabled":false,"creator_subscriptions_tweet_preview_api_enabled":true,"responsive_web_graphql_timeline_navigation_enabled":true,"responsive_web_graphql_skip_user_profile_image_extensions_enabled":false,"communities_web_enable_tweet_community_results_fetch":true,"c9s_tweet_anatomy_moderator_badge_enabled":true,"tweetypie_unmention_optimization_enabled":true,"responsive_web_edit_tweet_api_enabled":true,"graphql_is_translatable_rweb_tweet_is_translatable_enabled":true,"view_counts_everywhere_api_enabled":true,"longform_notetweets_consumption_enabled":true,"responsive_web_twitter_article_tweet_consumption_enabled":true,"tweet_awards_web_tipping_enabled":false,"creator_subscriptions_quote_tweet_preview_enabled":false,"freedom_of_speech_not_reach_fetch_enabled":true,"standardized_nudges_misinfo":true,"tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled":true,"rweb_video_timestamps_enabled":true,"longform_notetweets_rich_text_read_enabled":true,"longform_notetweets_inline_media_enabled":true,"responsive_web_enhance_cards_enabled":false}'


class EndpointUrl:
    TIMELINE = 'https://twitter.com/i/api/graphql/uPv755D929tshj6KsxkSZg/HomeTimeline'
    LATEST_TIMELINE = 'https://twitter.com/i/api/graphql/70b_oNkcK9IEN13WNZv8xA/HomeLatestTimeline'
    USER_TWEETS = 'https://twitter.com/i/api/graphql/piUHOePH_uDdwbD9GkquJA/UserTweets'
    USER_TWEETS_AND_REPLIES = 'https://twitter.com/i/api/graphql/KJiZSYLD2ijyHRBmgddo8Q/UserTweetsAndReplies'
    USER_LIKES = 'https://twitter.com/i/api/graphql/W42Y54_EmIjbTEdg9mGLDQ/Likes'
    TWEET_DETAIL = 'https://twitter.com/i/api/graphql/F45teiuFI9MDxaS9UYKv-g/TweetDetail'
    USER_BY_SCREEN_NAME = 'https://twitter.com/i/api/graphql/qW5u-DAuXpMEG0zA1F7UGQ/UserByScreenName'


@dataclass
class RequestDetails:
    url: str
    params: Dict[str, Any]
    headers: Dict[str, Any]
    cookies: CookieJar


def get_cookie_value(jar: CookieJar, name: str) -> Optional[str]:
    found = [x for x in jar if x.name == name]
    if not found:
        return None
    return found[0].value


def get_auth_headers(ct0: str) -> dict[str, Any]:
    """ct0: value of the ct0 cookie"""
    THE_API_KEY = 'Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs=1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA'
    headers = {
        'authorization': THE_API_KEY,
        'x-csrf-token': ct0
    }
    return headers


class Endpoint:
    """
    Superclass providing utility methods for concrete Endpoints

    Concrete Endpoints must implement prepare() method taking arbitrary
    arguments, that returns RequestDetails instance.

    They might use methods provided by this class for convenience,
    however, implementing them is not required.
    """
    FEATURES = TWEETS_FEATURES
    URL = 'https://twitter.com/...'

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
    def prepare_for(cls, cookies, variables: str) -> RequestDetails:
        params = {}
        params['variables'] = variables
        params['features'] = cls.FEATURES

        ct0 = get_cookie_value(cookies, 'ct0') or ''
        headers = get_auth_headers(ct0)

        details = RequestDetails(url=cls.URL, params=params, headers=headers, cookies=cookies)
        return details


class UserIDEndpoint(Endpoint):
    URL = EndpointUrl.USER_BY_SCREEN_NAME
    FEATURES = USER_FEATURES

    @classmethod
    def prepare(cls, cookies, user_handle: str) -> RequestDetails:
        user_handle = user_handle.strip('@/')
        variables = {'screen_name': user_handle, 'withSafetyModeUserFields': True}
        variables_text = json.dumps(variables)
        return super().prepare_for(cookies, variables_text)


class TweetDetailEndpoint(Endpoint):
    URL = EndpointUrl.TWEET_DETAIL

    @classmethod
    def prepare(cls, cookies, tweet_id: str, continuation: Optional[str] = None) -> RequestDetails:
        variables = {'focalTweetId': tweet_id, 'with_rux_injections': False, 'includePromotedContent': True, 'withCommunity': True, 'withQuickPromoteEligibilityTweetFields': True, 'withBirdwatchNotes': True, 'withVoice': True, 'withV2Timeline': True}
        if continuation:
            variables.update({'referrer': 'tweet', 'cursor': continuation})
        variables_text = json.dumps(variables)
        return super().prepare_for(cookies, variables_text)


class LatestTimelineEndpoint(Endpoint):
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
    def prepare(cls, cookies, continuation: Optional[str] = None, count: int = 20) -> RequestDetails:
        variables = super().get_variables(continuation=continuation, count=count)
        return super().prepare_for(cookies, variables)


class TimelineEndpoint(LatestTimelineEndpoint):
    URL = EndpointUrl.TIMELINE

    @staticmethod
    def get_base_variables(is_continuation: bool = False):
        return {"includePromotedContent": True, "latestControlAvailable": True, "withCommunity": True}


class UserTweetsEndpoint(Endpoint):
    URL = EndpointUrl.USER_TWEETS

    @staticmethod
    def get_base_variables(is_continuation: bool = False):
        return {'includePromotedContent': True, 'withQuickPromoteEligibilityTweetFields': True, 'withVoice': True, 'withV2Timeline': True}

    @classmethod
    def prepare(cls, cookies, user_id: str, continuation: Optional[str] = None, count: int = 20) -> RequestDetails:
        variables = super().get_variables(user_id=user_id, count=count, continuation=continuation)
        return super().prepare_for(cookies, variables)


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


def make_request(endpoint, cookies, **kwargs):
    request = endpoint.prepare(cookies, **kwargs)
    response = requests.get(request.url, params=request.params, headers=request.headers, cookies=cookies)
    try:
        check_rate_limit_headers(response.headers)
        response.raise_for_status()
    except Exception as e:
        raise
    data = response.json()
    return data

def check_rate_limit_headers(headers: Dict[str, str]):
    try:
        limit_total = int(headers.get('x-rate-limit-limit', -1))
        limit_remaining = int(headers.get('x-rate-limit-remaining', -1))
        reset_at = int(headers.get('x-rate-limit-reset', -1))
    except ValueError as e:
        print(f'error parsing limit headers')
        return
    now = int(datetime.datetime.now().timestamp())
    reset_after = max(0, reset_at - now)
    print(f'rate limit {limit_remaining}/{limit_total} after {reset_after} (at {reset_at})')
    if limit_remaining <= 1:
        print(f'sleeping {reset_after}')
        sleep(reset_after + 1)


def get_continuation(data):
    entries = find_all(data, '$..instructions..entries..content,itemContent')
    continuation = entries[-1]['value']
    return continuation


def get_user_id(data):
    return find_one(data, '$.data.user.result.rest_id')


def store(name: str, data):
    with open(name, 'wt', encoding='utf8') as fp:
        json.dump(data, fp, indent=4, ensure_ascii=False)

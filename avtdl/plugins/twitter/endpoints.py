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
import enum
import json
import logging
import urllib.parse
from typing import Any, Dict, Optional, Union

from multidict import CIMultiDictProxy

from avtdl.core.request import BucketRateLimit, Endpoint, HttpResponse, RequestDetails, get_retry_after
from avtdl.core.utils import find_one, get_cookie_value

TIMELINE_FEATURES = '{"rweb_video_screen_enabled":false,"payments_enabled":false,"profile_label_improvements_pcf_label_in_post_enabled":true,"responsive_web_profile_redirect_enabled":false,"rweb_tipjar_consumption_enabled":true,"verified_phone_label_enabled":false,"creator_subscriptions_tweet_preview_api_enabled":true,"responsive_web_graphql_timeline_navigation_enabled":true,"responsive_web_graphql_skip_user_profile_image_extensions_enabled":false,"premium_content_api_read_enabled":false,"communities_web_enable_tweet_community_results_fetch":true,"c9s_tweet_anatomy_moderator_badge_enabled":true,"responsive_web_grok_analyze_button_fetch_trends_enabled":false,"responsive_web_grok_analyze_post_followups_enabled":true,"responsive_web_jetfuel_frame":true,"responsive_web_grok_share_attachment_enabled":true,"articles_preview_enabled":true,"responsive_web_edit_tweet_api_enabled":true,"graphql_is_translatable_rweb_tweet_is_translatable_enabled":true,"view_counts_everywhere_api_enabled":true,"longform_notetweets_consumption_enabled":true,"responsive_web_twitter_article_tweet_consumption_enabled":true,"tweet_awards_web_tipping_enabled":false,"responsive_web_grok_show_grok_translated_post":false,"responsive_web_grok_analysis_button_from_backend":true,"creator_subscriptions_quote_tweet_preview_enabled":false,"freedom_of_speech_not_reach_fetch_enabled":true,"standardized_nudges_misinfo":true,"tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled":true,"longform_notetweets_rich_text_read_enabled":true,"longform_notetweets_inline_media_enabled":true,"responsive_web_grok_image_annotation_enabled":true,"responsive_web_grok_imagine_annotation_enabled":true,"responsive_web_grok_community_note_auto_translation_is_enabled":false,"responsive_web_enhance_cards_enabled":false}'
USER_TWEETS_FEATURES = '{"rweb_video_screen_enabled":false,"payments_enabled":false,"profile_label_improvements_pcf_label_in_post_enabled":true,"responsive_web_profile_redirect_enabled":false,"rweb_tipjar_consumption_enabled":true,"verified_phone_label_enabled":false,"creator_subscriptions_tweet_preview_api_enabled":true,"responsive_web_graphql_timeline_navigation_enabled":true,"responsive_web_graphql_skip_user_profile_image_extensions_enabled":false,"premium_content_api_read_enabled":false,"communities_web_enable_tweet_community_results_fetch":true,"c9s_tweet_anatomy_moderator_badge_enabled":true,"responsive_web_grok_analyze_button_fetch_trends_enabled":false,"responsive_web_grok_analyze_post_followups_enabled":true,"responsive_web_jetfuel_frame":true,"responsive_web_grok_share_attachment_enabled":true,"articles_preview_enabled":true,"responsive_web_edit_tweet_api_enabled":true,"graphql_is_translatable_rweb_tweet_is_translatable_enabled":true,"view_counts_everywhere_api_enabled":true,"longform_notetweets_consumption_enabled":true,"responsive_web_twitter_article_tweet_consumption_enabled":true,"tweet_awards_web_tipping_enabled":false,"responsive_web_grok_show_grok_translated_post":false,"responsive_web_grok_analysis_button_from_backend":true,"creator_subscriptions_quote_tweet_preview_enabled":false,"freedom_of_speech_not_reach_fetch_enabled":true,"standardized_nudges_misinfo":true,"tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled":true,"longform_notetweets_rich_text_read_enabled":true,"longform_notetweets_inline_media_enabled":true,"responsive_web_grok_image_annotation_enabled":true,"responsive_web_grok_imagine_annotation_enabled":true,"responsive_web_grok_community_note_auto_translation_is_enabled":false,"responsive_web_enhance_cards_enabled":false}fieldToggles={"withArticlePlainText":false}'
TWEET_DETAIL_FEATURES = '{"rweb_video_screen_enabled":false,"payments_enabled":false,"profile_label_improvements_pcf_label_in_post_enabled":true,"responsive_web_profile_redirect_enabled":false,"rweb_tipjar_consumption_enabled":true,"verified_phone_label_enabled":false,"creator_subscriptions_tweet_preview_api_enabled":true,"responsive_web_graphql_timeline_navigation_enabled":true,"responsive_web_graphql_skip_user_profile_image_extensions_enabled":false,"premium_content_api_read_enabled":false,"communities_web_enable_tweet_community_results_fetch":true,"c9s_tweet_anatomy_moderator_badge_enabled":true,"responsive_web_grok_analyze_button_fetch_trends_enabled":false,"responsive_web_grok_analyze_post_followups_enabled":true,"responsive_web_jetfuel_frame":true,"responsive_web_grok_share_attachment_enabled":true,"articles_preview_enabled":true,"responsive_web_edit_tweet_api_enabled":true,"graphql_is_translatable_rweb_tweet_is_translatable_enabled":true,"view_counts_everywhere_api_enabled":true,"longform_notetweets_consumption_enabled":true,"responsive_web_twitter_article_tweet_consumption_enabled":true,"tweet_awards_web_tipping_enabled":false,"responsive_web_grok_show_grok_translated_post":false,"responsive_web_grok_analysis_button_from_backend":true,"creator_subscriptions_quote_tweet_preview_enabled":false,"freedom_of_speech_not_reach_fetch_enabled":true,"standardized_nudges_misinfo":true,"tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled":true,"longform_notetweets_rich_text_read_enabled":true,"longform_notetweets_inline_media_enabled":true,"responsive_web_grok_image_annotation_enabled":true,"responsive_web_grok_imagine_annotation_enabled":true,"responsive_web_grok_community_note_auto_translation_is_enabled":false,"responsive_web_enhance_cards_enabled":false}&fieldToggles={"withArticleRichContentState":true,"withArticlePlainText":false,"withGrokAnalyze":false,"withDisallowedReplyControls":false}'
USER_FEATURES = '{"hidden_profile_subscriptions_enabled":true,"payments_enabled":false,"profile_label_improvements_pcf_label_in_post_enabled":true,"responsive_web_profile_redirect_enabled":false,"rweb_tipjar_consumption_enabled":true,"verified_phone_label_enabled":false,"subscriptions_verification_info_is_identity_verified_enabled":true,"subscriptions_verification_info_verified_since_enabled":true,"highlights_tweets_tab_ui_enabled":true,"responsive_web_twitter_article_notes_tab_enabled":true,"subscriptions_feature_can_gift_premium":true,"creator_subscriptions_tweet_preview_api_enabled":true,"responsive_web_graphql_skip_user_profile_image_extensions_enabled":false,"responsive_web_graphql_timeline_navigation_enabled":true}&fieldToggles={"withAuxiliaryUserLabels":true}'
SPACE_FEATURES = '{"spaces_2022_h2_spaces_communities":true,"spaces_2022_h2_clipping":true,"creator_subscriptions_tweet_preview_api_enabled":true,"payments_enabled":false,"profile_label_improvements_pcf_label_in_post_enabled":true,"responsive_web_profile_redirect_enabled":false,"rweb_tipjar_consumption_enabled":true,"verified_phone_label_enabled":false,"premium_content_api_read_enabled":false,"communities_web_enable_tweet_community_results_fetch":true,"c9s_tweet_anatomy_moderator_badge_enabled":true,"responsive_web_grok_analyze_button_fetch_trends_enabled":false,"responsive_web_grok_analyze_post_followups_enabled":true,"responsive_web_jetfuel_frame":true,"responsive_web_grok_share_attachment_enabled":true,"articles_preview_enabled":true,"responsive_web_graphql_skip_user_profile_image_extensions_enabled":false,"responsive_web_edit_tweet_api_enabled":true,"graphql_is_translatable_rweb_tweet_is_translatable_enabled":true,"view_counts_everywhere_api_enabled":true,"longform_notetweets_consumption_enabled":true,"responsive_web_twitter_article_tweet_consumption_enabled":true,"tweet_awards_web_tipping_enabled":false,"responsive_web_grok_show_grok_translated_post":false,"responsive_web_grok_analysis_button_from_backend":true,"creator_subscriptions_quote_tweet_preview_enabled":false,"freedom_of_speech_not_reach_fetch_enabled":true,"standardized_nudges_misinfo":true,"tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled":true,"longform_notetweets_rich_text_read_enabled":true,"longform_notetweets_inline_media_enabled":true,"responsive_web_grok_image_annotation_enabled":true,"responsive_web_grok_imagine_annotation_enabled":true,"responsive_web_graphql_timeline_navigation_enabled":true,"responsive_web_grok_community_note_auto_translation_is_enabled":false,"responsive_web_enhance_cards_enabled":false}'
SEARCH_FEATURES = '{"rweb_video_screen_enabled":false,"payments_enabled":false,"profile_label_improvements_pcf_label_in_post_enabled":true,"responsive_web_profile_redirect_enabled":false,"rweb_tipjar_consumption_enabled":true,"verified_phone_label_enabled":false,"creator_subscriptions_tweet_preview_api_enabled":true,"responsive_web_graphql_timeline_navigation_enabled":true,"responsive_web_graphql_skip_user_profile_image_extensions_enabled":false,"premium_content_api_read_enabled":false,"communities_web_enable_tweet_community_results_fetch":true,"c9s_tweet_anatomy_moderator_badge_enabled":true,"responsive_web_grok_analyze_button_fetch_trends_enabled":false,"responsive_web_grok_analyze_post_followups_enabled":true,"responsive_web_jetfuel_frame":true,"responsive_web_grok_share_attachment_enabled":true,"articles_preview_enabled":true,"responsive_web_edit_tweet_api_enabled":true,"graphql_is_translatable_rweb_tweet_is_translatable_enabled":true,"view_counts_everywhere_api_enabled":true,"longform_notetweets_consumption_enabled":true,"responsive_web_twitter_article_tweet_consumption_enabled":true,"tweet_awards_web_tipping_enabled":false,"responsive_web_grok_show_grok_translated_post":false,"responsive_web_grok_analysis_button_from_backend":true,"creator_subscriptions_quote_tweet_preview_enabled":false,"freedom_of_speech_not_reach_fetch_enabled":true,"standardized_nudges_misinfo":true,"tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled":true,"longform_notetweets_rich_text_read_enabled":true,"longform_notetweets_inline_media_enabled":true,"responsive_web_grok_image_annotation_enabled":true,"responsive_web_grok_imagine_annotation_enabled":true,"responsive_web_grok_community_note_auto_translation_is_enabled":false,"responsive_web_enhance_cards_enabled":false}'


class EndpointUrl:
    TIMELINE = 'https://twitter.com/i/api/graphql/vrNCYudm0qsExOy_-N9Q1g/HomeTimeline'
    LATEST_TIMELINE = 'https://twitter.com/i/api/graphql/rgMLKEHu1RpVEnuqlnwyiQ/HomeLatestTimeline'
    USER_TWEETS = 'https://twitter.com/i/api/graphql/oRJs8SLCRNRbQzuZG93_oA/UserTweets'
    USER_TWEETS_AND_REPLIES = 'https://twitter.com/i/api/graphql/kkaJ0Mf34PZVarrxzLihjg/UserTweetsAndReplies'
    TWEET_DETAIL = 'https://twitter.com/i/api/graphql/YVyS4SfwYW7Uw5qwy0mQCA/TweetDetail'
    USER_BY_SCREEN_NAME = 'https://twitter.com/i/api/graphql/ZHSN3WlvahPKVvUxVQbg1A/UserByScreenName'
    AUDIOSPACE_BY_ID = 'https://twitter.com/i/api/graphql/pCUWlI5FNL7ROBjmBsH3Zw/AudioSpaceById'
    SEARCH_TIMELINE = 'https://twitter.com/i/api/graphql/7r8ibjHuK3MWUyzkzHNMYQ/SearchTimeline'


def replace_url_host(url: str, new_host: str) -> str:
    parsed_host = urllib.parse.urlparse(new_host)
    parsed_url = urllib.parse.urlparse(url)
    scheme = parsed_url.scheme or parsed_host.scheme or 'https://'
    new_url = urllib.parse.urlunparse((scheme, parsed_host.netloc, parsed_url.path, parsed_url.params, parsed_url.query, parsed_url.fragment))
    return new_url


def get_netloc(host: str) -> str:
    return urllib.parse.urlparse(host).netloc


def get_auth_headers(cookies) -> dict[str, Any]:
    ct0 = get_cookie_value(cookies, 'ct0') or ''
    THE_API_KEY = 'Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs=1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA'
    headers = {
        'authorization': THE_API_KEY,
        'x-csrf-token': ct0
    }
    return headers


class TwitterRateLimit(BucketRateLimit):

    def _submit_headers(self, response: HttpResponse, logger: logging.Logger) -> bool:
        headers = response.headers
        try:
            # "default" argument is set to 'NaN' to trigger ValueError, since None is disliked by typechecker
            self.limit_total = int(headers.get('x-rate-limit-limit', 'NaN'))
            self.limit_remaining = int(headers.get('x-rate-limit-remaining', 'NaN'))
            self.reset_at = int(headers.get('x-rate-limit-reset', 'NaN'))
        except ValueError:
            logger.warning(f'[{self.name}] error parsing rate limit headers: "{headers}"')
            return False
        else:
            logger.debug(f'[{self.name}] rate limit {self.limit_remaining}/{self.limit_total}, resets after {datetime.timedelta(seconds=self.delay)}')
            return True


class TwitterEndpoint(Endpoint):
    """
    Base class providing common features for Twitter endpoints

    They might use methods provided by this class for convenience,
    however, implementing them is not required.
    """
    FEATURES = TIMELINE_FEATURES
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

        details = RequestDetails(url=url, params=params, headers=headers, rate_limit=cls.rate_limit())
        return details

    @classmethod
    @abc.abstractmethod
    def prepare(cls, *args, **kwargs) -> RequestDetails:
        """Prepare a RequestDetails object based on passed arguments"""


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
        user_id = find_one(data, '$.data.user.result.rest_id')
        return str(user_id) if user_id is not None else None


class TweetDetailEndpoint(TwitterEndpoint):
    URL = EndpointUrl.TWEET_DETAIL
    FEATURES = TWEET_DETAIL_FEATURES

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

        details = RequestDetails(url=url, params=params, headers=headers, rate_limit=cls.rate_limit())
        return details


class LatestTimelineEndpoint(TwitterEndpoint):
    URL = EndpointUrl.LATEST_TIMELINE
    FEATURES = TIMELINE_FEATURES

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
    FEATURES = TIMELINE_FEATURES

    @staticmethod
    def get_base_variables(is_continuation: bool = False):
        return {"includePromotedContent": True, "latestControlAvailable": True, "withCommunity": True}


class UserTweetsEndpoint(TwitterEndpoint):
    URL = EndpointUrl.USER_TWEETS
    FEATURES = USER_TWEETS_FEATURES

    @staticmethod
    def get_base_variables(is_continuation: bool = False):
        return {'includePromotedContent': True, 'withQuickPromoteEligibilityTweetFields': True, 'withVoice': True, 'withV2Timeline': True}

    @classmethod
    def prepare(cls, host: str, cookies, user_id: str, continuation: Optional[str] = None, count: int = 20) -> RequestDetails:
        variables = cls.get_variables(user_id=user_id, count=count, continuation=continuation)
        return cls.prepare_for(host, cookies, variables)


class UserTweetsRepliesEndpoint(UserTweetsEndpoint):
    URL = EndpointUrl.USER_TWEETS_AND_REPLIES
    FEATURES = USER_TWEETS_FEATURES

    @staticmethod
    def get_base_variables(is_continuation: bool = False):
        return {'includePromotedContent': True, 'withCommunity': True, 'withVoice': True, 'withV2Timeline': True}


class SearchQueryType(str, enum.Enum):
    LATEST = 'Latest'
    TOP = 'Top'
    MEDIA = 'Media'


class SearchTimelineEndpoint(TwitterEndpoint):
    URL = EndpointUrl.SEARCH_TIMELINE
    FEATURES = SEARCH_FEATURES

    @classmethod
    def prepare(cls, host: str, cookies, raw_query: str, query_type: SearchQueryType, continuation: Optional[str] = None, count: int = 20) -> RequestDetails:
        variables = {'rawQuery': raw_query, 'count': count, 'querySource': 'typed_query', 'product': query_type.value}
        if continuation:
            variables.update({'cursor': continuation})
        variables_text = json.dumps(variables)
        return cls.prepare_for(host, cookies, variables_text)


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

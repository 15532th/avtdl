import datetime
import logging
from http.cookies import SimpleCookie
from typing import Callable, Dict, List

import pytest

import avtdl
from avtdl.core.request import Delay, EndpointState, HttpRateLimit, HttpResponse, MaybeHttpResponse, NoResponse, \
    RateLimit
from avtdl.plugins.discord.webhook import DiscordRateLimit
from avtdl.plugins.twitter.endpoints import TwitterRateLimit

BASE_DELAY = 600
MAX_AGE_DELAY = 900
RETRY_AFTER_DELAY = 1200
SHORT_RETRY_AFTER_DELAY = 12
BASE_BACKOFF_DELAY = int(Delay.get_next(BASE_DELAY))
NEXT_BASE_BACKOFF_DELAY = int(Delay.get_next(BASE_BACKOFF_DELAY))

TIMESTAMP_NOW = 1750000000
RATE_LIMIT_DELAY = 1000
RATE_LIMIT_RESET = TIMESTAMP_NOW + RATE_LIMIT_DELAY
DEFAULT_BACKOFF_DELAY = int(Delay.get_next(RateLimit.DEFAULT_DELAY))
NEXT_DEFAULT_BACKOFF_DELAY = int(Delay.get_next(DEFAULT_BACKOFF_DELAY))


def mocked_utcnow():
    return datetime.datetime.fromtimestamp(TIMESTAMP_NOW, tz=datetime.timezone.utc)


@pytest.fixture
def mock_utcnow(monkeypatch):
    monkeypatch.setattr(avtdl.core.request, 'utcnow', mocked_utcnow)


def prepare_response(status: int, headers: Dict[str, str]) -> HttpResponse:
    endpoint_state = EndpointState()
    endpoint_state.update(headers)
    return HttpResponse(
        logging.getLogger('test'),
        text='test body',
        url='https://example.com',
        ok=status < 400,
        has_content=status < 300,
        status=status,
        reason='No reason provided',
        headers=headers,
        request_headers={},
        cookies=SimpleCookie(),
        endpoint_state=endpoint_state,
        content_encoding='utf8'
    )


def prepare_no_response() -> MaybeHttpResponse:
    return NoResponse(
        logging.getLogger('test'),
        e=Exception('network error'),
        url='https://example.com'
    )


def check_rate_limit_delay(rate_limit: RateLimit, responses: List[HttpResponse]) -> int:
    """Apply responses to rate_limit and return resulting current_delay"""
    for response in responses:
        rate_limit.submit_response(response, response.logger)
    return rate_limit.delay - 1


def check_for_responses(rate_limit: RateLimit, responses: List[Callable[[], HttpResponse]], expected_delay: int):
    response_instances = [response_factory() for response_factory in responses]
    actual_delay = check_rate_limit_delay(rate_limit, response_instances)
    if actual_delay != expected_delay:
        actual_delay = check_rate_limit_delay(rate_limit, response_instances)
    assert actual_delay == expected_delay


def prepare_200() -> HttpResponse:
    return prepare_response(200, {})


def prepare_200_with_max_age() -> HttpResponse:
    return prepare_response(200, {'Cache-Control': f'max-age={MAX_AGE_DELAY}'})


def prepare_304() -> HttpResponse:
    return prepare_response(304, {})


def prepare_304_with_max_age() -> HttpResponse:
    return prepare_response(304, {'Cache-Control': f'max-age={MAX_AGE_DELAY}'})


def prepare_403() -> HttpResponse:
    return prepare_response(403, {})


def prepare_429() -> HttpResponse:
    return prepare_response(429, {'Retry-After': str(RETRY_AFTER_DELAY)})

def prepare_short_429() -> HttpResponse:
    return prepare_response(429, {'Retry-After': str(SHORT_RETRY_AFTER_DELAY)})


def prepare_500() -> HttpResponse:
    return prepare_response(500, {})


class TestHttpRateLimit:
    testcases = {
        '200': ([prepare_200], BASE_DELAY),
        '200 with max age': ([prepare_200_with_max_age], MAX_AGE_DELAY),
        'consecutive 200': ([prepare_200, prepare_200], BASE_DELAY),
        '304': ([prepare_304], BASE_DELAY),
        '304 with max age': ([prepare_304_with_max_age], MAX_AGE_DELAY),
        '304 does not change delay': ([prepare_200, prepare_304], BASE_DELAY),
        'consecutive 304': ([prepare_304, prepare_304], BASE_DELAY),
        '429': ([prepare_429], RETRY_AFTER_DELAY),
        '429 with short Retry-After': ([prepare_short_429], BASE_DELAY),
        '500': ([prepare_500], BASE_BACKOFF_DELAY),
        '200 after 500': ([prepare_500, prepare_200], BASE_DELAY),
        '304 after 500': ([prepare_500, prepare_304], BASE_DELAY),
        'consecutive error responses': ([prepare_500, prepare_403], NEXT_BASE_BACKOFF_DELAY),
        'network error': ([prepare_no_response], BASE_BACKOFF_DELAY),
        '200 after network error': ([prepare_no_response, prepare_200], BASE_DELAY),
        '304 after network error': ([prepare_no_response, prepare_304], BASE_DELAY),
        '500 after network error': ([prepare_no_response, prepare_500], NEXT_BASE_BACKOFF_DELAY),
        'consecutive network errors': ([prepare_no_response, prepare_no_response], NEXT_BASE_BACKOFF_DELAY),
    }

    @staticmethod
    def rate_limit() -> RateLimit:
        return HttpRateLimit(name='test', base_delay=BASE_DELAY)

    @pytest.mark.parametrize('responses, expected_delay', testcases.values(), ids=testcases.keys())
    def test(self, mock_utcnow, responses: List[Callable[[], HttpResponse]], expected_delay: int):
        check_for_responses(self.rate_limit(), responses, expected_delay)


class TestLongHttpRateLimit:
    testcases = {
        'limit longer than max-age is not adjusted': ([prepare_200_with_max_age], 14400),
        'long limit not affected by retry-after': ([prepare_429], 14400),
        'limit longer than HIGHEST_UPDATE_INTERVAL is not adjusted on bad response': ([prepare_403], 14400),
        'limit longer than HIGHEST_UPDATE_INTERVAL is not adjusted on network error': ([prepare_no_response], 14400),
    }

    @staticmethod
    def rate_limit() -> RateLimit:
        return HttpRateLimit(name='test', base_delay=14400)

    @pytest.mark.parametrize('responses, expected_delay', testcases.values(), ids=testcases.keys())
    def test(self, mock_utcnow, responses: List[Callable[[], HttpResponse]], expected_delay: int):
        check_for_responses(self.rate_limit(), responses, expected_delay)


def prepare_twitter_bucket(status: int, full: bool, reset_at: int) -> HttpResponse:
    return prepare_response(status, {
        'x-rate-limit-limit': '50',
        'x-rate-limit-remaining': '48' if full else '0',
        'x-rate-limit-reset': str(reset_at)
    })


def twitter_200_bucket_full() -> HttpResponse:
    return prepare_twitter_bucket(200, True, RATE_LIMIT_RESET)


def twitter_200_bucket_empty() -> HttpResponse:
    return prepare_twitter_bucket(200, False, RATE_LIMIT_RESET)


def twitter_404_bucket_full() -> HttpResponse:
    return prepare_twitter_bucket(404, True, RATE_LIMIT_RESET)


def twitter_404_bucket_empty() -> HttpResponse:
    return prepare_twitter_bucket(404, False, RATE_LIMIT_RESET)


class TestTwitterRateLimit:
    testcases = {
        'success response with limit available': ([twitter_200_bucket_full], 0),
        'success response with limit reached': ([twitter_200_bucket_empty], RATE_LIMIT_DELAY),
        'error response with limit available': ([twitter_404_bucket_full], DEFAULT_BACKOFF_DELAY),
        'error response with limit reached': ([twitter_404_bucket_empty], RATE_LIMIT_DELAY),
        '429 with Retry-After and no limit headers': ([prepare_429], RETRY_AFTER_DELAY),
        '500 without limit headers': ([prepare_500], DEFAULT_BACKOFF_DELAY),
        'network error': ([prepare_no_response], DEFAULT_BACKOFF_DELAY),
        '200 after 500': ([prepare_500, twitter_200_bucket_full], 0),
        'consecutive 500': ([prepare_500, prepare_500], NEXT_DEFAULT_BACKOFF_DELAY),
        '200 after network error': ([prepare_no_response, twitter_200_bucket_full], 0),
        '500 after network error': ([prepare_no_response, prepare_500], NEXT_DEFAULT_BACKOFF_DELAY),
        'consecutive network errors': ([prepare_no_response, prepare_no_response], NEXT_DEFAULT_BACKOFF_DELAY),
    }

    @staticmethod
    def rate_limit() -> RateLimit:
        return TwitterRateLimit(name='test')

    @pytest.mark.parametrize('responses, expected_delay', testcases.values(), ids=testcases.keys())
    def test(self, mock_utcnow, responses: List[Callable[[], HttpResponse]], expected_delay: int):
        check_for_responses(self.rate_limit(), responses, expected_delay)


def prepare_discord_bucket(status: int, full: bool, reset_at: int, bucket: str = 'test') -> HttpResponse:
    return prepare_response(status, {
        'X-RateLimit-Limit': '5',
        'X-RateLimit-Remaining': '4' if full else '0',
        'X-RateLimit-Reset': str(reset_at),
        'X-RateLimit-Bucket': bucket
    })
def discord_200_bucket_full() -> HttpResponse:
    return prepare_discord_bucket(200, True, RATE_LIMIT_RESET)


def discord_200_bucket_empty() -> HttpResponse:
    return prepare_discord_bucket(200, False, RATE_LIMIT_RESET)



class TestDiscordRateLimit:
    testcases = {
        'success response with limit available': ([discord_200_bucket_full], 0),
        'success response with limit reached': ([discord_200_bucket_empty], RATE_LIMIT_DELAY),
        '429 with Retry-After and no limit headers': ([prepare_429], RETRY_AFTER_DELAY),
        '500 without limit headers': ([prepare_500], DEFAULT_BACKOFF_DELAY),
        'network error': ([prepare_no_response], DEFAULT_BACKOFF_DELAY),
        '200 after 500': ([prepare_500, discord_200_bucket_full], 0),
        'consecutive 500': ([prepare_500, prepare_500], NEXT_DEFAULT_BACKOFF_DELAY),
        '200 after network error': ([prepare_no_response, discord_200_bucket_full], 0),
        '500 after network error': ([prepare_no_response, prepare_500], NEXT_DEFAULT_BACKOFF_DELAY),
        'consecutive network errors': ([prepare_no_response, prepare_no_response], NEXT_DEFAULT_BACKOFF_DELAY),
    }

    @staticmethod
    def rate_limit() -> RateLimit:
        return DiscordRateLimit(name='test')

    @pytest.mark.parametrize('responses, expected_delay', testcases.values(), ids=testcases.keys())
    def test(self, mock_utcnow, responses: List[Callable[[], HttpResponse]], expected_delay: int):
        check_for_responses(self.rate_limit(), responses, expected_delay)

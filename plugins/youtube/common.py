import asyncio
import json
import logging
import re
import time
from collections import defaultdict
from hashlib import sha1
from http import cookies
from json import JSONDecodeError
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import parse_qs, unquote, urlparse

import aiohttp
import lxml.html
from jsonpath import JSONPath

from core.utils import request, request_raw


def find_all(data: Union[dict, list], jsonpath: str, cache={}) -> list:
    if jsonpath not in cache:
        cache[jsonpath] = JSONPath(jsonpath)
    parser = cache[jsonpath]
    return parser.parse(data)


def find_one(data: Union[dict, list], jsonpath: str) -> Optional[Any]:
    result = find_all(data, jsonpath)
    return result[0] if result else None


def get_initial_data(page: str) -> dict:
    try:
        return get_initial_data_fast(page)
    except (ValueError, JSONDecodeError):
        return get_initial_data_slow(page)


def get_initial_data_fast(page: str) -> dict:
    re_initial_data = 'var ytInitialData = ([^;]*);'
    match = re.search(re_initial_data, page)
    if match is None:
        raise ValueError(f'Failed to find ytInitialData on the page')
    raw_data = match.groups()[0]
    data = json.loads(raw_data)
    return data


def get_initial_data_slow(page: str) -> dict:
    anchor = 'var ytInitialData = {'
    pos_start = page.find(anchor)
    if pos_start == -1:
        raise ValueError(f'Failed to find initial data on page')
    pos_start += len(anchor) - 1
    position = pos_start

    re_parenthesses = re.compile('[{}]')
    parenthesses_values = defaultdict(int, {'{': 1, '}': -1})
    parentheses = 0
    while True:
        parentheses += parenthesses_values[page[position]]
        if parentheses == 0:
            raw_data = page[pos_start:position + 1]
            response = json.loads(raw_data)
            return response
        position_match = re_parenthesses.search(page, position + 1)
        try:
            position = position_match.start()
        except AttributeError:
            raise ValueError(f'Failed to find matching set of parentheses after initial data')


def thumbnail_url(video_id: str) -> str:
    return f'https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg'


def video_url(video_id: str) -> str:
    return f'https://www.youtube.com/watch?v={video_id}'


def get_continuation_token(data: Union[dict, list]) -> Optional[str]:
    token = find_one(data, '$..continuationCommand.token')
    return token


def extract_keys(page: str, keys: List[str], anchor: str = '') -> Tuple[Dict[str, list], dict]:
    pos_start = page.find(anchor)
    if pos_start == -1:
        raise ValueError(f'Failed to find anchor on page')
    pos_start += len(anchor)

    items = defaultdict(list)

    def append_search(obj):
        for k in keys:
            if k in obj:
                items[k].append(obj[k])
        return obj

    decoder = json.JSONDecoder(object_hook=append_search)
    page = page[pos_start:]
    data, pos_end = decoder.raw_decode(page)
    return items, data


CLIENT_VERSION = '2.20231023.04.02'


def get_auth_header(sapisid: str) -> str:
    timestamp = str(int(time.time()))
    sapisidhash = sha1(' '.join([timestamp, sapisid, 'https://www.youtube.com']).encode()).hexdigest()
    return f'SAPISIDHASH {timestamp}_{sapisidhash}'


def prepare_next_page_request(initial_page_data: dict, continuation_token, cookies=None, client_version=None) -> Tuple[str, dict, dict]:
    BROWSE_ENDPOINT = 'https://www.youtube.com/youtubei/v1/browse?key=AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8'
    cookies = cookies or {}

    response_context = find_one(initial_page_data, '$.responseContext')
    if response_context is not None:
        session_index = find_one(response_context, '$.webResponseContextExtensionData.ytConfigData.sessionIndex') or ''
        visitor_data = find_one(response_context, '$..visitorData') or ''
    else:
        session_index = ''
        visitor_data = ''

    if client_version is None:
        client_version = find_one(initial_page_data, '$..serviceTrackingParams..params[?(@.key=="client.version")].value')
        if client_version is None:
            client_version = CLIENT_VERSION

    headers = {
        'X-Goog-AuthUser': session_index,
        'X-Origin': 'https://www.youtube.com',
        'X-Youtube-Client-Name': '1',
        'Content-Type': 'application/json'
    }
    if 'SAPISID' in cookies:
        headers['Authorization'] = get_auth_header(cookies['SAPISID'])

    post_body = {
        'context': {
            'client': {'clientName': 'WEB', 'clientVersion': client_version, 'visitorData': visitor_data}},
        'continuation': continuation_token
    }
    return BROWSE_ENDPOINT, headers, post_body


def parse_navigation_endpoint(run: dict) -> str:
    """Parse url from 'navigationEndpoint' item"""
    url = find_one(run, '$..url')
    if url is None:
        raise ValueError(f'no url in navigationEndpoint "{run}"')
    if url.startswith('https://www.youtube.com/redirect'):
        parsed_url = urlparse(url)
        redirect_url = parse_qs(parsed_url.query)['q'][0]
        url = unquote(redirect_url)
    elif url.startswith('/hashtag'):
        url = run['text']
    elif url.startswith('/'):
        site = 'https://www.youtube.com'
        url = site + url
    return url


async def submit_consent(url: str, session: aiohttp.ClientSession, logger: logging.Logger) -> Optional[str]:
    consent_page = await request(url, session)
    if consent_page is None:
        logger.debug(f'requesting personalization settings url {url} failed')
        return None
    root = lxml.html.fromstring(consent_page)
    root.make_links_absolute(url)
    [form] = root.xpath("//form[button[@value='false']]") or [None]
    if not isinstance(form, lxml.html.FormElement):
        logger.debug(f'unable to locate form with confirmation button on page {url}')
        return None
    try:
        submit_button = form.xpath('.//button')[0]
        submit_name = submit_button.attrib['name']
        submit_value = submit_button.attrib['value']
    except (IndexError, KeyError, TypeError) as e:
        logger.debug(f'failed to extract values from confirmation button ({type(e)}: {e}) on page {url}')
        return None
    data = {i.name: i.value if i.value is not None else False for i in form.inputs}
    data[submit_name] = submit_value
    response = await request_raw(form.action, session, method='POST', data=data)
    if response is None:
        logger.debug(f'submitting confirmation to "{url}" failed. Raw data that was submitted: {data}')
    target_page = await response.text() if response else None
    return target_page


def find_consent_url(page: str) -> Optional[str]:
    CONSENT_URL_PATTERN = 'consent.youtube.com'

    if page.find(CONSENT_URL_PATTERN) == -1:
        return None
    initial_data = get_initial_data(page)
    url = find_one(initial_data, '$..feedNudgeRenderer..primaryButton..url')
    return url


async def handle_consent(page: str, session: aiohttp.ClientSession, logger: Optional[logging.Logger] = None) -> bool:
    """
    Take Youtube page that might contain popup asking to accept cookies,
    if the popup is present try to submit it and return response,
    if there is none or error happened return original page.

    Return value indicates if reload of page is required
    (meaning page asked for consent and it was successfully submitted)
    """
    if logger is None:
        logger = logging.getLogger()
    logger = logger.getChild('cookies_consent')

    consent_url = find_consent_url(page)
    if consent_url is None:
        logger.debug(f'page is not asking to accept cookies')
        return False
    redirect_page_text = await submit_consent(consent_url, session, logger)
    if redirect_page_text is None:
        logger.debug(f'failed to submit cookies consent')
        return False
    for morsel in session.cookie_jar:
        if isinstance(morsel, cookies.Morsel):
            if morsel.key == 'SOCS':
                logger.debug(f'cookie indicating cookies usage consent was set successfully')
                break
    return True


async def main():
    logging.basicConfig(level=logging.DEBUG)
    url = 'https://consent.youtube.com/dl?continue=https://www.youtube.com/?cbrd%3D1&gl=FR&hl=ru&cm=6&pc=yt&src=4&oyh=1'
    url = 'https://youtube.com'
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as request:
            page = await request.text()
        await handle_consent(page, session, logging.getLogger('main'))


if __name__ == '__main__':
    asyncio.run(main())

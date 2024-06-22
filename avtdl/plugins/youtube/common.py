import datetime
import json
import logging
import re
import time
from collections import defaultdict
from hashlib import sha1
from html import unescape
from http import cookies
from json import JSONDecodeError
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import parse_qs, unquote, urlparse

import aiohttp
import lxml.html
from pydantic import BaseModel

from avtdl.core.utils import find_one, get_cookie_value, request


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


def get_auth_header(sapisid: str) -> str:
    timestamp = str(int(time.time()))
    sapisidhash = sha1(' '.join([timestamp, sapisid, 'https://www.youtube.com']).encode()).hexdigest()
    return f'SAPISIDHASH {timestamp}_{sapisidhash}'


def get_innertube_context(page: str) -> Optional[dict]:
    anchor = '"INNERTUBE_CONTEXT":'
    _, context = extract_keys(page, [], anchor)
    return context


def get_session_index(page: dict) -> str:
    session_index = find_one(page, '$..responseContext..sessionIndex')
    session_index = '' if session_index is None else str(session_index)
    return session_index


def get_utc_offset() -> int:
    offset = datetime.datetime.now(datetime.timezone.utc).astimezone().utcoffset()
    if offset is None:
        # should never happen since astimezone() returns tz-aware object
        return 0
    return offset // datetime.timedelta(minutes=1)


class NextPageContext(BaseModel):
    """Values from first or current page required to request next continuation page"""
    innertube_context: Optional[dict]
    session_index: str
    continuation_token: Optional[str] = None


CLIENT_VERSION = '2.20231023.04.02'

def prepare_next_page_request(innertube_context: Optional[dict], continuation_token, cookies=None, session_index: str = '') -> Tuple[str, dict, dict]:
    BROWSE_ENDPOINT = 'https://www.youtube.com/youtubei/v1/browse?key=AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8'
    cookies = cookies or {}
    innertube_context = innertube_context or {}

    visitor_data = find_one(innertube_context, '$..visitorData') or ''
    client_version = find_one(innertube_context, '$..clientVersion') or CLIENT_VERSION
    hl = find_one(innertube_context, '$..hl') or 'en'
    timezone = find_one(innertube_context, '$..timeZone') or ''
    original_url = find_one(innertube_context, '$.client.originalUrl') or 'https://youtube.com'

    headers = {
        'X-Goog-AuthUser': session_index,
        'X-Origin': 'https://www.youtube.com',
        'X-Youtube-Client-Name': '1',
        'X-Youtube-Client-Version': client_version,
        'Content-Type': 'application/json'
    }
    sapisid = get_cookie_value(cookies, 'SAPISID')
    if sapisid is not None:
        headers['Authorization'] = get_auth_header(sapisid)

    post_body = {
        'context': {
            'client': {
                'clientName': 'WEB',
                'clientVersion': client_version,
                'visitorData': visitor_data,
                'hl': hl,
                # only reason timeZone might be present is "PREF" cookie being set in cookies file
                'timeZone': timezone,
                # Disabled until it gets better testing:
                # 'utcOffsetMinutes': get_utc_offset() if not timezone else ''
                'originalUrl': original_url
            }
        },
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
    data = {}
    for i in form.inputs:
        # must accept enabling watch history to get videos on main page
        if i.type == 'radio':
            # when submitting the form, radiobutton choice is converted
            # to string with bool value, "true" meaning consent
            value = 'true'
        else:
            value = i.value
        data[i.name] = value
    data[submit_name] = submit_value
    response = await request(form.action, session, method='POST', data=data)
    if response is None:
        logger.debug(f'submitting confirmation to "{url}" failed. Raw data that was submitted: {data}')
    return response


def find_consent_url(page: str) -> Optional[str]:
    has_consent = page.find('consent.youtube.com') > -1
    if not has_consent:
        return None
    try:
        initial_data = get_initial_data(page)
        url = find_one(initial_data, '$..feedNudgeRenderer..primaryButton..url')
        return url
    except ValueError:
        CONSENT_URL_PATTERN = r'"(https://consent.youtube.com/dl\?continue[^"]+)"'
        consent_match = re.findall(CONSENT_URL_PATTERN, page)
        if not consent_match:
            return None
        url = unquote(unescape(consent_match[0]))
        return url


async def handle_consent(page: str, url: str, session: aiohttp.ClientSession, logger: Optional[logging.Logger] = None) -> str:
    """
    Take Youtube page that might contain popup asking to accept cookies,
    if the popup is present try to submit it and return response,
    if there is none or error happened return original page.
    """
    if logger is None:
        logger = logging.getLogger()
    logger = logger.getChild('cookies_consent')

    consent_url = find_consent_url(page)
    if consent_url is None:
        logger.debug(f'page is not asking to accept cookies')
        return page
    redirect_page_text = await submit_consent(consent_url, session, logger)
    if redirect_page_text is None:
        logger.debug(f'failed to submit cookies consent')
        return page
    for morsel in session.cookie_jar:
        if isinstance(morsel, cookies.Morsel):
            if morsel.key == 'SOCS':
                logger.debug(f'cookie indicating cookies usage consent was set successfully')
                break
    reloaded_page = await request(url, session, logger)
    if reloaded_page is None:
        logger.debug(f'reloading original page failed, page content might be invalid this time')
        return redirect_page_text
    return reloaded_page

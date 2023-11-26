import json
import re
import time
from collections import defaultdict
from hashlib import sha1
from json import JSONDecodeError
from typing import Any, Dict, List, Optional, Tuple, Union

from jsonpath import JSONPath


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
            raw_data = page[pos_start:position+1]
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
    else:
        session_index = ''

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
            'client': {'clientName': 'WEB', 'clientVersion': client_version}},
        'continuation': continuation_token
    }
    return BROWSE_ENDPOINT, headers, post_body

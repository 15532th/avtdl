import json
import re
from collections import defaultdict
from typing import Any, Optional

from jsonpath import JSONPath


def find_all(data: Any, jsonpath: str, cache={}) -> list:
    if jsonpath not in cache:
        cache[jsonpath] = JSONPath(jsonpath)
    parser = cache[jsonpath]
    return parser.parse(data)


def find_one(data: Any, jsonpath: str) -> Optional[Any]:
    result = find_all(data, jsonpath)
    return result[0] if result else None


def get_initial_data(page: str) -> dict:
    try:
        return get_initial_data_fast(page)
    except ValueError:
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

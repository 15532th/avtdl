import json
import re
from typing import Any, Optional

from jsonpath_ng import ext as jsonpath_ng


def find_all(data: Any, jsonpath: str, cache={}) -> list:
    if jsonpath not in cache:
        cache[jsonpath] = jsonpath_ng.parse(jsonpath)
    parser = cache[jsonpath]
    return [item.value for item in parser.find(data)]


def find_one(data: Any, jsonpath: str) -> Optional[Any]:
    result = find_all(data, jsonpath)
    return result[0] if result else None


def get_initial_data(page: str) -> dict:
    re_initial_data = 'var ytInitialData = ({.*?});'
    match = re.search(re_initial_data, page)
    if match is None:
        raise ValueError(f'Failed to find ytInitialData on the page')
    raw_data = match.groups()[0]
    data = json.loads(raw_data)
    return data


def thumbnail_url(video_id: str) -> str:
    return f'https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg'

def video_url(video_id: str) -> str:
    return f'https://www.youtube.com/watch?v={video_id}'

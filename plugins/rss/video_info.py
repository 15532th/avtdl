import datetime
import json
import re
import urllib.request
from typing import Optional, Dict, Any, List

import aiohttp
from pydantic import BaseModel, Field


class VideoFormat(BaseModel):
    itag: int
    url: str = Field(repr=False)
    mime: str
    quality: str

class VideoInfo(BaseModel):
    url: str
    title: str
    published: str
    uploaded: str
    author: str
    channel_id: str
    video_id: str
    summary: str = Field(repr=False)
    views: int
    length: int

    scheduled: Optional[datetime.datetime] = None
    live_start: Optional[datetime.datetime] = None
    live_end: Optional[datetime.datetime] = None

    is_unlisted: bool
    is_adult: bool
    is_livestream: bool
    is_upcoming: bool

    playability_status: str
    playability_reason: Optional[str] = None

    formats: Optional[List[VideoFormat]] = []


def get_video_page(url: str) -> str:
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = resp.read()
        return data.decode('utf8')

async def aget_video_page(url: str, session: Optional[aiohttp.ClientSession] = None) -> str:
    if session is None:
        async with aiohttp.request('GET', url) as response:
            text = await response.text()
    else:
        async with session.get(url) as response:
            text = await response.text()
    return text

def get_initial_player_response(page: str) -> dict:
    re_response = 'var ytInitialPlayerResponse = ({.*?});'
    match = re.search(re_response, page)
    if match is None:
        raise ValueError(f'Failed to find InitialPlayerResponse on page')
    raw_data = match.groups()[0]
    data = json.loads(raw_data)
    return data

def get_embedded_player_response(page: str) -> dict:
    pos_start = page.find('{"embedded_player_response"')
    if pos_start == -1:
        raise ValueError(f'Failed to find embedded_player_response on page')
    parentheses = 1
    for position in range(pos_start+1, len(page)):
        if page[position] == '{':
            parentheses += 1
        if page[position] == '}':
            parentheses -= 1
        if parentheses == 0:
            raw_data = page[pos_start:position+1]
            response_data = json.loads(raw_data).get('embedded_player_response', '')
            response = json.loads(response_data)
            return response
    else:
        raise ValueError(f'Failed to find closing parenthesis for embedded_player_response')

def rename_keys(input_dict: Dict[str, Any], key_mapping: Dict[str, str]) -> Dict[str, Any]:
    output_dict = {}
    for data_key, input_key in key_mapping.items():
        value = input_dict.get(input_key)
        if value is not None:
            output_dict[data_key] = value
    return output_dict

def parse_playability_status(player_response: dict) -> Dict[str, Any]:
    playability_status = player_response.get('playabilityStatus')
    if playability_status is None:
        return {}

    info = rename_keys(playability_status, {'playability_status': 'status', 'playability_reason': 'reason'})

    try:
        date = playability_status["liveStreamability"]["liveStreamabilityRenderer"]["offlineSlate"]["liveStreamOfflineSlateRenderer"]["scheduledStartTime"]
        date = datetime.datetime.fromtimestamp(int(date), tz=datetime.timezone.utc)
    except (KeyError, TypeError, ValueError):
        pass
    else:
        info['scheduled'] = date

    return info

def parse_video_details(player_response: dict) -> Dict[str, Any]:
    video_details = player_response.get('videoDetails')
    if video_details is None:
        return {}
    key_mapping = {
        'title': 'title',
        'author': 'author',
        'channel_id': 'channelId',
        'video_id': 'videoId',
        'summary': 'shortDescription',
        'views': 'viewCount',
        'length': 'lengthSeconds',
        'is_livestream': 'isLiveContent',
    }
    info = rename_keys(video_details, key_mapping)
    info['is_upcoming'] = video_details.get('isUpcoming', False)
    return info

def parse_microformat(player_response: dict) -> Dict[str, Any]:
    microformat = player_response.get('microformat', {}).get('playerMicroformatRenderer')
    if microformat is None:
        return {}
    key_mapping = {
        'published': 'publishDate',
        'uploaded': 'uploadDate',
        'author': 'ownerChannelName',
        'views': 'viewCount',
        'length': 'lengthSeconds',
        'is_unlisted': 'isUnlisted',
    }
    info = rename_keys(microformat, key_mapping)
    if microformat.get('title') is not None:
        info['title'] = microformat.get('title', {}).get('simpleText')
    if microformat.get('description') is not None:
        info['summary'] = microformat.get('description', {}).get('simpleText')
    info['is_adult'] = not microformat.get('isFamilySafe')
    info['is_livestream'] = microformat.get('liveBroadcastDetails') is not None

    live_details = microformat.get('liveBroadcastDetails')
    if live_details is not None:
        if live_details.get('endTimestamp') is not None: # live ended
            info['live_start'] = live_details.get('startTimestamp')
            info['live_end'] = live_details.get('endTimestamp')
        elif live_details.get('isLiveNow') == True: # live is live
            info['live_start'] = live_details.get('startTimestamp')
        else: # live is scheduled
            date = live_details.get('startTimestamp')
            try:
                date = datetime.datetime.fromisoformat(date)
                info['scheduled'] = date
            except (ValueError, TypeError):
                pass

    return info

def parse_video_formats(player_response: dict) -> List[VideoFormat]:
    formats = player_response.get('streamingData')
    if formats is None:
        return []
    formats_list = []
    formats_list.extend(formats.get('formats', []))
    formats_list.extend(formats.get('adaptiveFormats', []))

    parsed_formats = []
    for item in formats_list:
        try:
            itag = item['itag']
            url = item['url']
            mime = item['mimeType']
            quality = item.get('qualityLabel') or item['quality']
            parsed_formats.append(VideoFormat(itag=itag, url=url, mime=mime, quality=quality))
        except (TypeError, KeyError):
            continue
    return parsed_formats


def parse_player_response(player_response: dict) -> Dict[str, Any]:
    info = {}
    info.update(parse_video_details(player_response))
    info.update(parse_microformat(player_response))
    info.update(parse_playability_status(player_response))
    info['formats'] = parse_video_formats(player_response)
    return info

def get_video_info(url: str) -> VideoInfo:
    page = get_video_page(url)
    response = get_initial_player_response(page)
    data = parse_player_response(response)
    data['url'] = url
    info = VideoInfo(**data)
    return info

async def aget_video_info(url: str, session: Optional[aiohttp.ClientSession] = None) -> VideoInfo:
    page = await aget_video_page(url, session)
    response = get_initial_player_response(page)
    data = parse_player_response(response)
    data['url'] = url
    info = VideoInfo(**data)
    return info

import datetime
import enum
from dataclasses import dataclass
from textwrap import shorten
from typing import Any, Dict, List, Optional, Sequence

import aiohttp
import dateutil.parser
from pydantic import Field

from avtdl.core import utils
from avtdl.core.config import Plugins
from avtdl.core.interfaces import MAX_REPR_LEN, Record
from avtdl.core.monitors import BaseFeedMonitor, BaseFeedMonitorConfig, BaseFeedMonitorEntity
from avtdl.core.utils import Fmt


@Plugins.register('rplay', Plugins.kind.ASSOCIATED_RECORD)
class RplayRecord(Record):
    """Represents event of a RPLAY live starting"""
    url: str
    """livestream url"""
    title: str
    """stream title"""
    description: str
    """stream description"""
    thumbnail_url: Optional[str]
    """url of the stream thumbnail"""
    start: datetime.datetime
    """time of the stream start"""
    user_id: str
    """user's oid"""
    creator_id: str
    """creatorOid"""
    name: str
    """visible name of the user"""
    avatar_url: str
    """link to the user's avatar"""
    restream_platform: Optional[str] = None
    """for restream, platform stream is being hosted on"""
    restream_key: Optional[str]
    """for restream, unique id of the stream on the source platform"""

    def __str__(self):
        restream = get_restream_url(self.restream_platform, self.restream_key)
        restream = f' (restream from {restream})\n' if restream else ''
        text = f'[{self.name}] live since {Fmt.date(self.start)}\n{self.title}\n{self.url}\n{restream}'
        return text

    def __repr__(self):
        title = shorten(self.title, MAX_REPR_LEN)
        return f'RplayRecord(creator_id={self.creator_id}, start={self.start}, title={title})'

    def get_uid(self) -> str:
        return f'{self.creator_id}:{int(self.start.timestamp() * 1000)}'

    def discord_embed(self) -> dict:
        return {
            'title': self.title,
            'url': self.url,
            'description': get_restream_url(self.restream_platform, self.restream_key) or None,
            'image': {'url': self.thumbnail_url},
            'color': None,
            'author': {'name': self.name, 'url': None, 'icon_url': self.avatar_url},
            'timestamp': self.start.isoformat(),
            'footer': {'text': None},
            'fields': []
        }


@Plugins.register('rplay', Plugins.kind.ACTOR_CONFIG)
class RplayMonitorConfig(BaseFeedMonitorConfig):
    pass


@Plugins.register('rplay', Plugins.kind.ACTOR_ENTITY)
class RplayMonitorEntity(BaseFeedMonitorEntity):
    update_interval: int = 300
    """how often the monitored channel will be checked, in seconds"""
    url: str = Field(exclude=True, default=None)
    """no url is needed"""
    adjust_update_interval: bool = Field(exclude=True, default=True)
    """rplay api endpoints doesn't use caching headers"""
    latest_live_start: datetime.datetime = Field(exclude=True, default=None)
    """internal variable to persist state between updates. Used to distinguish between different livestreams of the user"""


@Plugins.register('rplay', Plugins.kind.ACTOR)
class RplayMonitor(BaseFeedMonitor):
    """
    """

    def __init__(self, conf: RplayMonitorConfig, entities: Sequence[RplayMonitorEntity]):
        super().__init__(conf, entities)
        self.nickname_cache: Dict[str, str] = {}

    async def get_records(self, entity: RplayMonitorEntity, session: aiohttp.ClientSession) -> Sequence[RplayRecord]:
        r = RplayUrl.livestreams()
        data = await self.request_json(r.url, entity, session, headers=r.headers, params=r.params)
        if data is None:
            return []
        records = self.parse_livestreams(data)

        await self.update_nicknames_cache(session, entity, records)
        self.update_nicknames(records)

        return records

    def parse_livestreams(self, data: List[dict]):
        if not isinstance(data, List):
            self.logger.warning(
                f'unexpected response from /livestreams endpoint, not a list of records. Raw response: {data}')
            return []
        records = []
        for live in data:
            try:
                record = parse_livestream(live)
                records.append(record)
            except Exception as e:
                self.logger.warning(f'failed to parse record: {type(e)} {e}. Raw data: "{data}"')
        return records

    async def update_nicknames_cache(self, session: aiohttp.ClientSession, entity: RplayMonitorEntity,
                                     records: List[RplayRecord]):
        creator_oids = [r.creator_id for r in records if r.creator_id not in self.nickname_cache]
        if not creator_oids:
            return
        r = RplayUrl.bulkgetusers(creator_oids)
        data = await utils.request_json(r.url, session, self.logger, headers=r.headers, params=r.params)
        if data is None:
            self.logger.warning(f'[{entity.name}] failed to update nickname cache: failed to get users info')
            return
        try:
            for oid, info in data.items():
                if 'nickname' in info:
                    self.nickname_cache[oid] = info['nickname']
        except Exception as e:
            self.logger.warning(f'[{entity.name}] failed to update nickname cache: {type(e)} {e}')

    def update_nicknames(self, records: List[RplayRecord]):
        for record in records:
            if record.creator_id in self.nickname_cache:
                record.name = self.nickname_cache[record.creator_id]


def get_avatar_url(creator_oid) -> str:
    url = f'https://pb.rplay.live/profilePhoto/{creator_oid}'
    return url


def live_thumbnail_url(creator_oid: str) -> str:
    return f'https://pb.rplay.live/liveChannelThumbnails/{creator_oid}'


def get_restream_url(platform: Optional[str], restream_key: Optional[str]) -> Optional[str]:
    if platform is None or restream_key is None:
        return None
    if platform == 'twitch':
        return f'https://twitch.tv/{restream_key}'
    if platform == 'youtube':
        return f'https://www.youtube.com/watch?v={restream_key}'
    return None


def parse_livestream(item: dict) -> RplayRecord:
    """parse a single item from a list returned by /live/livestreams endpoint"""
    creator_oid = item['creatorOid']
    record = RplayRecord(

        url=f'https://rplay.live/live/{creator_oid}/',
        title=item['title'],
        description=item['description'],
        thumbnail_url=live_thumbnail_url(creator_oid),
        start=dateutil.parser.parse(item['streamStartTime']),
        user_id=item['_id'],
        creator_id=creator_oid,
        name=item['creatorNickname'],
        avatar_url=get_avatar_url(creator_oid),
        restream_platform=item.get('streamState'),
        restream_key=item.get('multiPlatformKey')
    )
    return record


class StreamState(str, enum.Enum):
    OFFLINE = 'offline'
    LIVE = 'live'
    TWITCH = 'twitch'
    YOUTUBE = 'youtube'


def parse_play(item: dict) -> Optional[RplayRecord]:
    """parse a single item from a list returned by /live/play/ endpoint"""
    state = item['streamState']
    if state == StreamState.OFFLINE:
        return None
    elif state == StreamState.LIVE:
        restream_platform = None
        restream_key = None
    elif state == StreamState.TWITCH:
        restream_platform = state
        restream_key = item['twitchLogin']
    elif state == StreamState.YOUTUBE:
        restream_platform = state
        restream_key = item['liveStreamId']
    else:
        raise ValueError(f'unexpected stream state: {state}')

    creator_oid = item['creatorOid']
    record = RplayRecord(

        url=f'https://rplay.live/live/{creator_oid}/',
        title=item['title'],
        description=item['description'],
        thumbnail_url=live_thumbnail_url(creator_oid),
        start=dateutil.parser.parse(item['streamStartTime']),
        user_id=item['_id'],
        creator_id=creator_oid,
        name=item['creatorMetadata']['nickname'],
        avatar_url=get_avatar_url(creator_oid),
        restream_platform=restream_platform,
        restream_key=restream_key
    )
    return record


@dataclass
class RequestDetails:
    url: str
    params: Optional[Dict[str, Any]] = None
    headers: Optional[Dict[str, Any]] = None


class RplayUrl:

    @staticmethod
    def livestreams(oid: str = '') -> RequestDetails:
        url = f'https://api.rplay.live/live/livestreams'
        params = {'creatorOid': oid, 'lang': 'en'}
        return RequestDetails(url=url, params=params)

    @staticmethod
    def play(oid: str, key: str = '') -> RequestDetails:
        """
        'streamState': 'offline', 'live' | 'twitch' | 'youtube'
        'liveStreamId' - youtube video_id or ""
        'twitchLogin' - twitch username or ""
        'streamStartTime': '2024-07-23T11:29:29.000Z'
        '_id', 'creatorOid', 'title', 'description'
        'creatorMetadata': {
            'nickname',
            'channelImage' - channel banner url,
            'customUrl' - custom channel name, "/c:creator",
            'published', 'publishedClips', 'playlists', 'communityPosts', 'pinnedPost' - list of contentOid
        }
        """
        url = f'https://api.rplay.live/live/play'
        params = {'creatorOid': oid, 'key': key, 'lang': 'en'}
        return RequestDetails(url=url, params=params)

    @staticmethod
    def getuser(oid: str) -> RequestDetails:
        url = f'https://api.rplay.live/account/getuser'
        params = {'userOid': oid, 'filter[]': ['_id', 'nickname', 'creatorTags'], 'lang': 'en'}
        return RequestDetails(url=url, params=params)

    @staticmethod
    def subscriptions(oid: str) -> RequestDetails:
        url = f'https://api.rplay.live/account/getuser'
        params = {'userOid': oid, 'filter[]': ['_id', 'nickname', 'subscribingTo'], 'lang': 'en'}
        return RequestDetails(url=url, params=params)

    @staticmethod
    def bulkgetusers(oids: List[str]) -> RequestDetails:
        url = f'https://api.rplay.live/account/bulkgetusers'
        params = {'users': '|'.join(oids),
                  'toGrab': '|'.join(['_id', 'nickname', 'lastPubDate', 'creatorTags', 'isLive']), 'lang': 'en'}
        return RequestDetails(url=url, params=params)

    @staticmethod
    def content(content_oid) -> RequestDetails:
        url = f'https://api.rplay.live/content'
        params = {'contentOid': content_oid, 'status': 'published'}
        return RequestDetails(url=url, params=params)


def fetch_json(r: RequestDetails):
    import requests
    try:
        response = requests.get(r.url, params=r.params, headers=r.headers)
        return response.json()
    except Exception as e:
        return None


if __name__ == '__main__':

    r = RplayUrl.livestreams()
    livestreams_all = fetch_json(r)

    oids = [x['creatorOid'] for x in livestreams_all]

    r = RplayUrl.bulkgetusers(oids)
    bulk = fetch_json(r)

    for oid in oids[:1]:
        r = RplayUrl.livestreams(oid)
        livestreams_one = fetch_json(r)

        r = RplayUrl.getuser(oid)
        user_data = fetch_json(r)

        r = RplayUrl.subscriptions(oid)
        subscriptions = fetch_json(r)

    for oid in oids:
        r = RplayUrl.play(oid)
        live_data = fetch_json(r)
        record = parse_play(live_data)
        ...
    ...

import datetime
from dataclasses import dataclass
from textwrap import shorten
from typing import Any, Dict, List, Optional, Sequence

import aiohttp
import dateutil.parser
from pydantic import Field

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
    start: datetime.datetime
    """time of the stream start"""
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
        since = '\nsince ' + Fmt.date(self.start) if self.start else ''
        return f'{self.url}\n{self.title}{since}'

    def __repr__(self):
        title = shorten(self.title, MAX_REPR_LEN)
        return f'RplayRecord(user_id={self.user_id}, start={self.start_timestamp}, title={title})'

    def discord_embed(self) -> dict:
        return {
            'title': self.title,
            'description': self.url,
            'color': None,
            'author': {'name': self.name, 'url': self.url, 'icon_url': self.avatar_url},
            'timestamp': self.start.isoformat() if self.start else None,
            'footer': {'text': self.description},
            'fields': []
        }


@Plugins.register('rplay', Plugins.kind.ACTOR_CONFIG)
class RplayMonitorConfig(BaseFeedMonitorConfig):
    pass


@Plugins.register('rplay', Plugins.kind.ACTOR_ENTITY)
class RplayMonitorEntity(BaseFeedMonitorEntity):
    update_interval: int = 120
    """how often the monitored channel will be checked, in seconds"""
    adjust_update_interval: bool = Field(exclude=True, default=True)
    """rplay api endpoints doesn't use caching headers"""
    latest_live_start: datetime.datetime = Field(exclude=True, default=None)
    """internal variable to persist state between updates. Used to distinguish between different livestreams of the user"""


@Plugins.register('rplay', Plugins.kind.ACTOR)
class RplayMonitor(BaseFeedMonitor):
    """
    """

    async def get_records(self, entity: RplayMonitorEntity, session: aiohttp.ClientSession) -> Sequence[RplayRecord]:
        r = RplayUrl.livestreams()
        data = await self.request_json(r.url, entity, session, headers=r.headers, params=r.params)
        if data is None:
            return []
        records = self.parse_livestreams(data)
        return records

    def parse_livestreams(self, data: List[dict]):
        if not isinstance(data, List):
            self.logger.warning(f'unexpected response from /livestreams endpoint, not a list of records. Raw response: {data}')
            return []
        records = []
        for live in data:
            try:
                oid = live['_id']
                creator_oid = live['creatorOid']
                start = dateutil.parser.parse(live['streamStartTime'])

                record = RplayRecord(

                    url=f'https://rplay.live/play/{oid}/',
                    title=live['title'],
                    description=live['description'],
                    start=start,
                    creator_id=live['creatorOid'],
                    name=live['creatorNickname'],
                    avatar_url=get_avatar_url(creator_oid),
                    restream_platform=live.get('streamState'),
                    restream_key=live.get('multiPlatformKey')
                )
                records.append(record)
            except Exception as e:
                self.logger.warning(f'failed to parse record: {type(e)} {e}. Raw data: "{data}"')
        return records


def get_avatar_url(creator_oid):
    ts = int(datetime.datetime.now(tz=datetime.timezone.utc).timestamp() * 1000)
    url = f'https://pb.rplay.live/profilePhoto/{creator_oid}?time={ts}'
    return url


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
        params = {'users': '|'.join(oids), 'toGrab': '|'.join(['_id', 'nickname', 'lastPubDate', 'creatorTags', 'isLive']), 'lang': 'en'}
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
    oids = []

    r = RplayUrl.livestreams()
    livestreams_all = fetch_json(r)

    r = RplayUrl.bulkgetusers(oids)
    bulk = fetch_json(r)

    for oid in oids:
        r = RplayUrl.livestreams(oid)
        livestreams_one = fetch_json(r)

        r = RplayUrl.getuser(oid)
        user_data = fetch_json(r)

        r = RplayUrl.subscriptions(oid)
        subscriptions = fetch_json(r)

        r = RplayUrl.play(oid)
        live_data = fetch_json(r)
        ...
    ...


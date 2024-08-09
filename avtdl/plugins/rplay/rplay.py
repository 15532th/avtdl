import asyncio
import base64
import datetime
import enum
import hashlib
import hmac
import json
import urllib.parse
from dataclasses import dataclass
from textwrap import shorten
from typing import Any, Dict, List, Optional, Sequence

import aiohttp
import dateutil.parser
from pydantic import Field, FilePath, model_validator

from avtdl.core import utils
from avtdl.core.config import Plugins
from avtdl.core.interfaces import MAX_REPR_LEN, Record
from avtdl.core.monitors import BaseFeedMonitor, BaseFeedMonitorConfig, BaseFeedMonitorEntity
from avtdl.core.utils import Fmt


@Plugins.register('rplay', Plugins.kind.ASSOCIATED_RECORD)
@Plugins.register('rplay.user', Plugins.kind.ASSOCIATED_RECORD)
class RplayRecord(Record):
    """Ongoing livestream on RPLAY"""
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
    """creatorOid, unique id used in channel and livestream urls"""
    name: str
    """visible name of the user"""
    avatar_url: str
    """link to the user's avatar"""
    restream_platform: Optional[str] = None
    """for restream, platform the stream is being hosted on"""
    restream_url: Optional[str] = None
    """for restream, url of the stream on the source platform"""
    playlist_url: Optional[str] = None
    """for native stream, link to the underlying hls playlist if was retrieved successfully.
    Might be invalid even when present in case of insufficient permissions or network error"""

    def __str__(self):
        restream = f'\n(restream from {self.restream_url})\n' if self.restream_url else ''
        playlist = f'\n({self.playlist_url})\n' if self.playlist_url else ''
        text = f'[{self.name}] live since {Fmt.date(self.start)}\n{self.title}\n{self.url}{restream}{playlist}'
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
            'description': self.restream_url or self.playlist_url or None,
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
    creators: Optional[List[str]] = None
    """list of IDs of the users to monitor. Entity with no IDs will report on every user going live"""
    update_interval: int = 300
    """how often the monitored channel will be checked, in seconds"""
    selected_for_update: bool = Field(exclude=True, default=False)
    """internal variable, used to mark one entity that actually makes network request to update queues"""
    url: str = Field(exclude=True, default=None)
    """url is not used, all streams are checked through the same api endpoing"""
    cookies_file: Optional[FilePath] = Field(exclude=True, default=None)
    """cookies are not used to log in"""
    adjust_update_interval: bool = Field(exclude=True, default=True)
    """rplay api endpoints doesn't use caching headers"""


@Plugins.register('rplay', Plugins.kind.ACTOR)
class RplayMonitor(BaseFeedMonitor):
    """
    Monitor livestreams on RPLAY

    Monitors users with `creator_oid` listed in `creators`, produces a record
    when any of them starts a livestream. When `creators` list is not provided,
    every livestream on the site will generate a record. `creator_oid` is the
    unique part of the user's home or livestream url.
    For example, `creator_oid` is `6596e71c04a7ea2fd7c36ae7`
    for the following urls:

    - `https://rplay.live/creatorhome/6596e71c04a7ea2fd7c36ae7`
    - `https://rplay.live/live/6596e71c04a7ea2fd7c36ae7`

    This monitor checks all creators with a single request,
    however, because of that it does not try to retrieve the
    direct `playlist url` for livestreams, and therefore does
    not support providing login credentials.
    """

    def __init__(self, conf: RplayMonitorConfig, entities: Sequence[RplayMonitorEntity]):
        super().__init__(conf, entities)
        self.nickname_cache: Dict[str, str] = {}
        self.consumers: Dict[str, asyncio.Queue] = {}
        for entity in self.entities.values():
            self.consumers[entity.name] = asyncio.Queue()

        # new data are only fetched in a single entity with the shortest update interval
        # it allows to avoid creating and monitoring a dedicated task for performing update
        entity_for_update = min(entities, key=lambda entity: entity.update_interval)
        entity_for_update.selected_for_update = True

    async def get_records(self, entity: RplayMonitorEntity, session: aiohttp.ClientSession) -> Sequence[RplayRecord]:
        if entity.selected_for_update:
            await self.update_records(entity, session)

        records = []
        queue = self.consumers[entity.name]
        while not queue.empty():
            records.append(queue.get_nowait())
        return records

    async def update_records(self, entity: RplayMonitorEntity, session: aiohttp.ClientSession) -> None:
        r = RplayUrl.livestreams()
        data = await self.request_json(r.url, entity, session, headers=r.headers, params=r.params)
        if data is None:
            return
        records = self.parse_livestreams(data)

        # nickname field in data from /livestreams endpoint is empty,
        # get them from /bulkgetusers instead
        await self.update_nicknames_cache(session, entity, records)
        self.update_nicknames(records)

        for record in records:
            for name, queue in self.consumers.items():
                creators = self.entities[name].creators
                if creators is None or record.creator_id in creators:
                    await queue.put(record)

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


@Plugins.register('rplay.user', Plugins.kind.ACTOR_CONFIG)
class RplayUserMonitorConfig(BaseFeedMonitorConfig):
    login: Optional[str] = None
    """rplay login of the account used for monitoring"""
    password: Optional[str] = None
    """rplay password of the account used for monitoring"""
    user_id: Optional[str] = None
    """userOid of the account used for monitoring. When absent can be retrieved automatically by performing a login request"""

    @model_validator(mode='after')
    def check_invariants(self):
        if (self.login and not self.password) or (not self.login and self.password):
            raise ValueError('provide either both login and password or none of them')
        if self.user_id and not self.login:
            raise ValueError('when user_id is set,  credentials must also be provided')
        return self


@Plugins.register('rplay.user', Plugins.kind.ACTOR_ENTITY)
class RplayUserMonitorEntity(RplayMonitorEntity):
    creator_oid: str
    """ID of the user to monitor"""


@Plugins.register('rplay.user', Plugins.kind.ACTOR)
class RplayUserMonitor(BaseFeedMonitor):
    """
    Monitor livestreams on RPLAY channel

    Monitors a user with given `creator_oid`, produces record
    when the user starts a livestream. `creator_oid` is the
    unique part of the user's home or livestream url.
    For example, `creator_oid` is `6596e71c04a7ea2fd7c36ae7`
    for the following urls:

    - `https://rplay.live/creatorhome/6596e71c04a7ea2fd7c36ae7`
    - `https://rplay.live/live/6596e71c04a7ea2fd7c36ae7`

    When producing a record, this plugin will generate `playlist_url`
    for the stream. If credentials are provided in the `config` section,
    it will try to update it with the key required to access livestreams
    limited to subscribers. Resulting `playlist_url` might still be
    invalid if the update failed or account does not have permissions
    to view the stream.
    """

    def __init__(self, conf: RplayUserMonitorConfig, entities: Sequence[RplayUserMonitorEntity]):
        super().__init__(conf, entities)
        self.own_user: Optional['User'] = None

    async def get_records(self, entity: RplayUserMonitorEntity, session: aiohttp.ClientSession) -> Sequence[RplayRecord]:
        r = RplayUrl.play(entity.creator_oid)
        data = await self.request_json(r.url, entity, session, headers=r.headers, params=r.params)
        if data is None:
            return []
        try:
            record = parse_play(data)
        except Exception as e:
            self.logger.warning(f'failed to parse record: {type(e)} {e}. Raw data: "{data}"')
            return []
        if record is None:
            return []
        record.playlist_url = await self.get_playlist_url(session, record)
        return [record]

    async def get_playlist_url(self, session: aiohttp.ClientSession, record: RplayRecord) -> Optional[str]:
        if record.restream_url is not None:
            return None

        own_user = await self.get_own_user(session)
        if own_user is not None:
            r = RplayUrl.key2(own_user)
            key2 = await utils.request(r.url, session, self.logger, r.method, r.params, r.data, r.headers, retry_times=3, retry_multiplier=3)
            if key2 is None:
                key2 = ''
        else:
            key2 = ''
        r = RplayUrl.playlist(record.creator_id, key2=key2)
        return r.url_with_query

    async def get_own_user(self, session: aiohttp.ClientSession) -> Optional['User']:
        if self.conf.login is None:
            return None
        if self.own_user is None:
            token = User.token_for(self.conf.login, self.conf.password)
            if self.conf.user_id is not None:
                self.logger.debug(f'using userOid from configuration')
                user_oid = self.conf.user_id
            else:
                r = RplayUrl.login(token)
                user_info = await utils.request_json(r.url, session, self.logger, r.method, r.params, r.data, r.headers)
                if user_info is None:
                    self.logger.debug(f'failed to log in')
                    return None
                if not 'oid' in user_info:
                    self.logger.debug(f'user oid is absent in login response. Raw response: {user_info}')
                    return None
                user_oid = user_info['oid']
                self.logger.info(f'successfully logged in, user_id is "{user_oid}"')
            self.own_user = User(user_oid, token)
        return self.own_user


def parse_livestream(item: dict) -> RplayRecord:
    """parse a single item from a list returned by /live/livestreams endpoint"""
    creator_oid = item['creatorOid']

    restream_platform = item.get('streamState')
    restream_key = item.get('multiPlatformKey')
    restream_url = get_restream_url(restream_platform, restream_key)

    record = RplayRecord(

        url=get_livestream_url(creator_oid),
        title=item['title'],
        description=item['description'],
        thumbnail_url=live_thumbnail_url(creator_oid),
        start=dateutil.parser.parse(item['streamStartTime']),
        user_id=item['_id'],
        creator_id=creator_oid,
        name=item['creatorNickname'],
        avatar_url=get_avatar_url(creator_oid),
        restream_platform=restream_platform,
        restream_url=restream_url
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
    restream_url = get_restream_url(restream_platform, restream_key)

    creator_oid = item['creatorOid']
    record = RplayRecord(
        url=get_livestream_url(creator_oid),
        title=item['title'],
        description=item['description'],
        thumbnail_url=live_thumbnail_url(creator_oid),
        start=dateutil.parser.parse(item['streamStartTime']),
        user_id=item['_id'],
        creator_id=creator_oid,
        name=item['creatorMetadata']['nickname'],
        avatar_url=get_avatar_url(creator_oid),
        restream_platform=restream_platform,
        restream_url=restream_url
    )
    return record


def get_avatar_url(creator_oid: str) -> str:
    return f'https://pb.rplay.live/profilePhoto/{creator_oid}'


def get_livestream_url(creator_oid: str) -> str:
    return f'https://rplay.live/live/{creator_oid}/'


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


class JWTAuth:

    @staticmethod
    def _dict_to_b64url(data: dict) -> str:
        data_json = json.dumps(data).replace(' ', '')
        data_b64url = base64.urlsafe_b64encode(data_json.encode())
        return data_b64url.decode().strip('=')

    @staticmethod
    def _hmac_signature_b64url(message: str, secret: str) -> str:
        signature = hmac.new(secret.encode(), msg=message.encode(), digestmod='sha256').digest()
        return base64.urlsafe_b64encode(signature).decode().strip('=')

    @classmethod
    def generate_token(cls, eml: str, dat: Optional[datetime.datetime] = None) -> str:
        header = {'alg': 'HS256', 'typ': 'JWT'}
        header_encoded = cls._dict_to_b64url(header)

        dat = dat or datetime.datetime.now()
        dat = dat.astimezone()
        dat_text = dat.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
        iat = int(dat.timestamp())
        if dat.utcoffset() is not None:
            # timestamp is calculated from local time as if it was UTC
            iat += int(dat.utcoffset().total_seconds())

        payload = {'eml': eml, 'dat': dat_text, 'iat': iat}
        payload_encoded = cls._dict_to_b64url(payload)

        return f'{header_encoded}.{payload_encoded}'

    @classmethod
    def sign_token(cls, token: str, secret: str) -> str:
        secret = hashlib.sha256(secret.encode()).digest().hex()
        signature = cls._hmac_signature_b64url(token, secret)
        return f'{token}.{signature}'

    @classmethod
    def new(cls, eml: str, psw: str, dat: Optional[datetime.datetime] = None) -> str:
        """generate new signed token"""
        token = cls.generate_token(eml, dat)
        signed_token = cls.sign_token(token, psw)
        return signed_token


@dataclass
class User:
    user_oid: str
    token: str

    @staticmethod
    def token_for(email: str, passwd: str) -> str:
        return JWTAuth.new(email, passwd)

    def get_auth_header(self) -> Dict[str, str]:
        return {'Authorization': self.token}


@dataclass
class RequestDetails:
    url: str
    method: str = 'GET'
    params: Optional[Dict[str, Any]] = None
    data: Optional[Any] = None
    headers: Optional[Dict[str, Any]] = None

    @property
    def url_with_query(self) -> str:
        if self.params is None:
            return self.url
        parsed = urllib.parse.urlparse(self.url)
        with_query = parsed._replace(query=urllib.parse.urlencode(self.params))
        url = urllib.parse.urlunparse(with_query)
        return url


class RplayUrl:

    @staticmethod
    def livestreams(creator_oid: str = '') -> RequestDetails:
        url = f'https://api.rplay.live/live/livestreams'
        params = {'creatorOid': creator_oid, 'lang': 'en'}
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
    def getuser_by_oid(creator_oid: str) -> RequestDetails:
        """get user info and live status by userOid"""
        url = f'https://api.rplay.live/account/getuser'
        params = {'userOid': creator_oid, 'filter[]': ['_id', 'nickname', 'creatorTags'], 'lang': 'en'}
        return RequestDetails(url=url, params=params)

    @staticmethod
    def getuser_by_name(cursom_url: str) -> RequestDetails:
        """get user info and live status by custom channel url"""
        url = f'https://api.rplay.live/account/getuser'
        params = {'customUrl': cursom_url, 'filter[]': ['_id', 'nickname', 'creatorTags'], 'lang': 'en'}
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
    def content(content_oid, user: Optional[User] = None) -> RequestDetails:
        url = f'https://api.rplay.live/content'
        params = {'contentOid': content_oid, 'status': 'published', 'withContentMetadata': True, 'requestCanView': True, 'lang': 'en'}
        headers = {}
        if user is not None:
            params.update({'requestorOid': user.user_oid, 'loginType': 'plax'})
            headers.update(user.get_auth_header())
        return RequestDetails(url=url, params=params, headers=headers)

    @staticmethod
    def playlist(creator_oid: str, key: str = '', key2: str = '') -> RequestDetails:
        url = 'https://api.rplay.live/live/stream/playlist.m3u8'
        params = {'creatorOid': creator_oid, 'key': key, 'key2': key2}
        return RequestDetails(url=url, params=params)

    @staticmethod
    def key2(user: User) -> RequestDetails:
        """response is the key as a plaintext"""
        url = f'https://api.rplay.live/live/key2'
        params = {'requestorOid': user.user_oid, 'loginType': 'plax'}
        headers = user.get_auth_header()
        return RequestDetails(url=url, params=params, headers=headers)

    @staticmethod
    def login(token: str) -> RequestDetails:
        url = f'https://api.rplay.live/account/login'
        data = {'checkAdmin': None, 'lang': 'en', 'loginType': None, 'token': token}
        headers = {'Content-Type': 'application/json'}
        return RequestDetails(url=url, method='POST', data=json.dumps(data), headers=headers)

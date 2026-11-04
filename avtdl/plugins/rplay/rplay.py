import asyncio
import base64
import datetime
import enum
import hashlib
import hmac
import json
import logging
import time
import urllib.parse
from collections import Counter
from dataclasses import dataclass
from textwrap import shorten
from typing import Dict, List, Mapping, Optional, Sequence

import dateutil.parser
from pydantic import Field, FilePath, PositiveFloat, model_validator

from avtdl.core.config import Plugins
from avtdl.core.formatters import Fmt
from avtdl.core.interfaces import MAX_REPR_LEN, Record
from avtdl.core.monitors import BaseFeedMonitor, BaseFeedMonitorConfig, BaseFeedMonitorEntity
from avtdl.core.request import HttpClient, RequestDetails, RetrySettings
from avtdl.core.runtime import RuntimeContext
from avtdl.core.utils import JSONType, try_parse_date, with_prefix


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

    def as_embed(self) -> dict:
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
    update_interval: PositiveFloat = 300
    """how often the monitored channel will be checked, in seconds"""
    selected_for_update: bool = Field(exclude=True, default=False)
    """internal variable, used to mark one entity that actually makes network request to update queues"""
    url: str = Field(exclude=True, default='')
    """url is not used, all streams are checked through the same api endpoint"""
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

    def __init__(self, conf: RplayMonitorConfig, entities: Sequence[RplayMonitorEntity], ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)
        self.entities: Mapping[str, RplayMonitorEntity]  # type: ignore
        self.nickname_cache: Dict[str, str] = {}
        self.consumers: Dict[str, asyncio.Queue] = {}
        for entity in self.entities.values():
            self.consumers[entity.name] = asyncio.Queue()

        # new data are only fetched in a single entity with the shortest update interval
        # it allows to avoid creating and monitoring a dedicated task for performing update
        entity_for_update = min(entities, key=lambda entity: entity.update_interval)
        entity_for_update.selected_for_update = True

    async def get_records(self, entity: RplayMonitorEntity, client: HttpClient) -> Sequence[RplayRecord]:
        if entity.selected_for_update:
            await self.update_records(entity, client)

        records = []
        queue = self.consumers[entity.name]
        while not queue.empty():
            records.append(queue.get_nowait())
        return records

    async def update_records(self, entity: RplayMonitorEntity, client: HttpClient) -> None:
        r = RplayUrl.livestreams()
        data = await self.request_json(r.url, entity, client, headers=r.headers, params=r.params)
        if data is None:
            return
        if not isinstance(data, List):
            self.logger.warning(
                f'unexpected response from /livestreams endpoint, not a list of records. Raw response: {data}')
            return
        records = self.parse_livestreams(data)

        # nickname field in data from /livestreams endpoint is empty,
        # get them from /bulkgetusers instead
        await self.update_nicknames_cache(client, entity, records)
        self.update_nicknames(records)

        for record in records:
            for name, queue in self.consumers.items():
                creators = self.entities[name].creators
                if creators is None or record.creator_id in creators:
                    await queue.put(record)

    def parse_livestreams(self, data: list) -> List[RplayRecord]:
        records = []
        for live in data:
            try:
                record = parse_livestream(live)
                records.append(record)
            except Exception as e:
                self.logger.warning(f'failed to parse record: {type(e)} {e}. Raw data: "{data}"')
        return records

    async def update_nicknames_cache(self, client: HttpClient, entity: RplayMonitorEntity,
                                     records: List[RplayRecord]):
        creator_oids = [r.creator_id for r in records if r.creator_id not in self.nickname_cache]
        if not creator_oids:
            return
        r = RplayUrl.bulkgetusers(creator_oids)
        response = await client.request(r.url, headers=r.headers, params=r.params)
        if response.ok and not response.has_content:
            self.logger.debug(f'[{entity.name}] got {response.status} updating nickname cache')
            return
        if not response.has_json():
            self.logger.warning(f'[{entity.name}] failed to update nickname cache: failed to get users info. Raw response: {response!r}')
            return
        try:
            data = response.json()
            if not isinstance(data, dict):
                self.logger.warning(f'[{entity.name}] failed to update nickname cache: unexpected response "{data}"')
                return
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
    login: str
    """rplay login of the account used for monitoring"""
    password: str
    """rplay password of the account used for monitoring"""
    user_id: Optional[str] = None
    """userOid of the account used for monitoring. Normally is retrieved automatically by performing a login request"""
    refresh_token: Optional[str] = None
    """refresh token is used to request access token. Normally is retrieved automatically by performing a login request"""

    @model_validator(mode='after')
    def check_invariants(self):
        if self.user_id is None and not self.refresh_token is None or self.user_id is not None and self.refresh_token is None:
            raise ValueError('"user_id" and "refresh_token" must be both present when used and both absent otherwise')
        return self

@Plugins.register('rplay.user', Plugins.kind.ACTOR_ENTITY)
class RplayUserMonitorEntity(RplayMonitorEntity):
    creators: None = Field(default=None, exclude=True)
    """Not used for this monitor"""
    creator_oid: str
    """ID of the user to monitor"""


@Plugins.register('rplay.user', Plugins.kind.ACTOR)
class RplayUserMonitor(BaseFeedMonitor):
    """
    Monitor livestreams on RPLAY channel

    Requires account credentials in order to work.
    When "user_id" and "refresh_token" provided,
    they are used instead of the login/password pair.

    Monitors a user with given `creator_oid`, produces record
    when the user starts a livestream. `creator_oid` is the
    unique part of the user's home or livestream url.
    For example, `creator_oid` is `6596e71c04a7ea2fd7c36ae7`
    for the following urls:

    - `https://rplay.live/creatorhome/6596e71c04a7ea2fd7c36ae7`
    - `https://rplay.live/live/6596e71c04a7ea2fd7c36ae7`

    When producing a record, this plugin will generate `playlist_url`
    for the stream and try to update it with the key required to access
    livestreams limited to subscribers. Resulting `playlist_url` might
    still be invalid if the update failed or user account does not have
    permissions to view the stream.
    """

    def __init__(self, conf: RplayUserMonitorConfig, entities: Sequence[RplayUserMonitorEntity], ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)
        self.conf: RplayUserMonitorConfig
        self.own_user: Optional['User'] = None

    async def get_records(self, entity: RplayUserMonitorEntity, client: HttpClient) -> Sequence[RplayRecord]:
        own_user = await self.get_own_user(client)
        if own_user is None:
            self.logger.warning(f'[{entity.name}] update cancelled because login failed. If the failure persists, verify the login credentials in the monitor config')
            return []
        r = RplayUrl.play(entity.creator_oid, own_user)
        data = await self.request_json(r.url, entity, client, headers=r.headers, params=r.params)
        if data is None:
            return []
        if not isinstance(data, dict):
            self.logger.warning(f'failed to parse record: unexpected format. Raw data: "{data}"')
            return []
        try:
            record = parse_play(data)
        except Exception as e:
            self.logger.warning(f'failed to parse record: {type(e)} {e}. Raw data: "{data}"')
            return []
        if record is None:
            return []
        record.playlist_url = await self.get_playlist_url(client, record)
        return [record]

    async def get_playlist_url(self, client: HttpClient, record: RplayRecord) -> Optional[str]:
        if record.restream_url is not None:
            return None
        key2 = await self.get_key2(client) or ''
        r = RplayUrl.playlist(record.creator_id, key2=key2)
        return url_with_query(r)

    async def get_key2(self, client: HttpClient) -> Optional[str]:
        own_user = await self.get_own_user(client)
        if own_user is None:
            return None
        r = RplayUrl.key2(own_user)
        retry_settings = RetrySettings(retry_times=3, retry_multiplier=3)
        raw_key2 = await client.request_text(r.url, method=r.method, params=r.params, data=r.data, headers=r.headers,
                                        settings=retry_settings)
        if raw_key2 is None:
            return None
        try:
            key2 = json.loads(raw_key2)['authKey']
        except (TypeError, KeyError, json.JSONDecodeError) as e:
            self.logger.warning(f'failed to process key2 "{raw_key2}": {type(e)}:{e}')
            return None
        return key2

    async def get_own_user(self, client: HttpClient) -> Optional['User']:
        if self.own_user is None:
            if self.conf.user_id is None or self.conf.refresh_token is None:
                # fresh login using login/password config values
                r = RplayUrl.login(self.conf.login, self.conf.password)
                user_info = await client.request_json_endpoint(self.logger, r)
                if user_info is None:
                    self.logger.warning(f'failed to log in')
                    return None
                if not isinstance(user_info, dict):
                    self.logger.warning(f'unexpected user_info format: {user_info}')
                    return None
                try:
                    user_oid = user_info['user']['_id']
                    token = user_info['token']
                    refresh_token = user_info['refreshToken']
                except Exception:
                    self.logger.warning(f'failed to parse login response: {user_info}')
                    return None
                self.logger.info(f'successfully logged in, user_id is "{user_oid}"')
            else:
                # user_id and refresh_token config values are provided, use them instead of login/password
                user_oid = self.conf.user_id
                refresh_token = self.conf.refresh_token
                token = await self.refresh_auth_token(client, user_oid, refresh_token)
            self.own_user = User(user_oid, token, refresh_token)
        elif self.own_user.token_expired():
            token = await self.refresh_auth_token(client, self.own_user.user_oid, self.own_user.refresh_token)
            if token is None:
                return None
            self.own_user.token = token
        return self.own_user

    async def refresh_auth_token(self, client: HttpClient, user_id, refresh_token) -> Optional[str]:
        r = RplayUrl.refresh_token(user_id, refresh_token)
        access_token = await client.request_json_endpoint(self.logger, r)
        if access_token is None:
            self.logger.warning(f'failed to refresh access token')
            return None
        if not isinstance(access_token, dict):
            self.logger.warning(f'unexpected access token format: {access_token}')
            return None
        try:
            token = access_token['accessToken']
        except Exception:
            self.logger.warning(f'failed to parse access token: {access_token}')
            return None
        try:
            JWTAuth.decode_token(token)
        except Exception as e:
            self.logger.warning(f'failed to decode access token: {token}: {e}')
            return None
        self.logger.info(f'successfully refreshed access token')
        return token


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


@Plugins.register('rplay.notifications', Plugins.kind.ACTOR_CONFIG)
class RplayNotificationsMonitorConfig(RplayUserMonitorConfig):
        pass

@Plugins.register('rplay.notifications', Plugins.kind.ACTOR_ENTITY)
class RplayNotificationsMonitorEntity(RplayMonitorEntity):
    creators: None = Field(default=None, exclude=True)
    """Not used for this monitor"""


@Plugins.register('rplay.notifications', Plugins.kind.ASSOCIATED_RECORD)
class RplayNotification(Record):
    url: str
    """notification url (livestream or channel url)"""
    uid: str
    """NotificationId"""
    creator_id: str
    """creatorOid"""
    content_id: str
    """contentOid"""
    name: str
    """Visible name of the creator"""
    notification_type: str
    """livestream, contentPublished, recordingPublished"""
    published: datetime.datetime
    """time when notification was sent"""

    def __str__(self) -> str:
        return f'Notification: {self.notification_type} ({self.content_id}) by {self.name} ({self.creator_id}) at {self.published}'

    def __repr__(self) -> str:
        return f'RplayNotification(uid={self.uid}, type={self.notification_type}, name={self.name}, published={self.published})'

    def get_uid(self) -> str:
        return f'{self.creator_id}:{int(self.published.timestamp() * 1000)}'

    def as_embed(self) -> dict:
        avatar_url = get_avatar_url(self.creator_id)
        return {
            'title': f'Notification: {self.notification_type}',
            'url': self.url,
            'description': None,
            'color': None,
            'author': {'name': self.name, 'url': None, 'icon_url': avatar_url},
            'timestamp': self.published.isoformat(),
            'footer': {'text': None},
            'fields': []
        }


@Plugins.register('rplay.notifications', Plugins.kind.ACTOR)
class RplayNotificationsMonitor(RplayUserMonitor):
    """
    Monitor livestreams notifications of RPLAY account

    Requires account credentials in order to work.
    When "user_id" and "refresh_token" provided,
    they are used instead of the login/password pair.

    Monitors account's notifications, produces record
    when livestream notification is received.
    """

    async def get_records(self, entity: RplayNotificationsMonitorEntity, client: HttpClient) -> Sequence[RplayRecord]:
        raise NotImplementedError('get_records should never get called on RplayNotificationMonitor because get_new_records is overwritten')

    async def get_new_records(self, entity: RplayNotificationsMonitorEntity, client: HttpClient) -> Sequence[Record]:
        logger = with_prefix(self.logger, f'[{entity.name}]')
        own_user = await self.get_own_user(client)
        if own_user is None:
            logger.warning(f'update cancelled because login failed. If the failure persists, verify the login credentials in the monitor config')
            return []
        r = RplayUrl.get_own_info(own_user)
        data = await self.request_json(r.url, entity, client, headers=r.headers, params=r.params)
        if data is None:
            return []
        try:
            notifications = parse_own_info(data, logger)
        except Exception as e:
            logger.warning(f'failed to parse notifications: {type(e)} {e}.')
            logger.debug(f' Raw data:\n"{data}"')
            return []
        new_notifications = self.filter_new_records(notifications, entity)
        logger.debug(f'got {len(notifications)} notifications ({len(new_notifications)} new)')

        enriched_records = await self.enrich_notification_records(entity, client, new_notifications, logger) # type: ignore
        return [*new_notifications, *enriched_records]

    async def enrich_notification_records(self, entity: RplayNotificationsMonitorEntity, client: HttpClient,
                                          new_notifications: List[RplayNotification], logger: logging.Logger
                                          ) -> List[RplayRecord]:
        content_oids = list(Counter(n.content_id for n in new_notifications if n.notification_type=='livestream').keys())
        if not content_oids:
            return []
        r = RplayUrl.bulkcontent(content_oids)
        data = await self.request_json(r.url, entity, client, method=r.method, headers=r.headers, params=r.params, data=r.data, data_json=r.data_json)
        if data is None:
            return []
        if not data:
            logger.debug(f'content metadata is empty for livestream notifications')
        try:
            records = parse_bulk_content(data, logger)
        except Exception as e:
            logger.warning(f'failed to parse content info: {type(e)} {e}.')
            logger.debug(f' Raw data:\n"{data}"')
            return []
        # for record in records:
        #     if self.record_is_new(record, entity):
        #         record.playlist_url = await self.get_playlist_url(client, record)
        return records


def parse_own_info(data: JSONType, logger: logging.Logger) -> List[RplayNotification]:
    """Parse notifications from account own information"""
    if not isinstance(data, dict):
        raise ValueError(f'unexpected response: expected dict got {type(data)}')
    notifications_items = data.get('notifications')
    if notifications_items is None:
        raise ValueError(f'unexpected response: no notifications present')
    notifications = []
    for notification_item in notifications_items:
        try:
            creator_oid = notification_item.get('creatorOid')
            published_text = notification_item.get('date')
            published = try_parse_date(published_text, datetime.timezone.utc)
            notification_type = notification_item.get('type')
            assert isinstance(notification_type, str), f'unexpected notification type: {notification_type}'
            assert isinstance(published, datetime.datetime), f'failed to parse notification timestamp: {published_text}'
            if notification_type == 'livestream':
                url = get_livestream_url(creator_oid)
            else:
                url = get_channel_url(creator_oid)
            notification = RplayNotification(
                url=url,
                uid=notification_item.get('notificationId'),
                content_id=notification_item.get('contentOid'),
                creator_id=creator_oid,
                name=notification_item.get('nickname'),
                notification_type=notification_type,
                published=published
            )
        except Exception as e:
            logger.warning(f'failed to parse notification: {e}')
            logger.debug(f'raw notification data:\n{notification_item}')
            continue
        notifications.append(notification)
    return notifications


def parse_bulk_content(data: JSONType, logger: logging.Logger) -> List[RplayRecord]:
    if not isinstance(data, dict):
        raise ValueError(f'unexpected response: expected dict got {type(data)}')
    records = []
    for content_oid, content_item in data.items():
        try:
            record = parse_content(content_item)
        except Exception as e:
            logger.warning(f'failed to parse content item: {e}')
            logger.debug(f'raw item:\n{content_item}')
            continue
        records.append(record)
    return records

def parse_content(item: dict) -> RplayRecord:
    """parse a single item from a list returned by /content/bulk endpoint"""
    creator_oid = item['creatorOid']

    record = RplayRecord(

        url=get_livestream_url(creator_oid),
        title=item['title'],
        description=item['introText'],
        thumbnail_url=live_thumbnail_url(creator_oid),
        start=dateutil.parser.parse(item['publishedAt']),
        user_id=item['creatorInfo']['_id'],
        creator_id=creator_oid,
        name=item['nickname'],
        avatar_url=get_avatar_url(creator_oid),
        restream_platform=None,
        restream_url=None
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


def get_channel_url(creator_oid: str) -> str:
    return f'https://rplay.live/creatorhome/{creator_oid}/'


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
    def decode_token(cls, token: str) -> dict:
        try:
            parts = token.split('.')
            if len(parts) != 3:
                raise ValueError('Invalid token format. Expected 3 parts separated by "."')
            payload = parts[1]

            padding = 4 - len(payload) % 4
            if padding != 4:
                payload += '=' * padding

            decoded = base64.urlsafe_b64decode(payload)
            return json.loads(decoded)
        except Exception as e:
            raise ValueError(f'Failed to decode JWT: {e}')

    @classmethod
    def generate_token(cls, eml: str, dat: Optional[datetime.datetime] = None) -> str:
        header = {'alg': 'HS256', 'typ': 'JWT'}
        header_encoded = cls._dict_to_b64url(header)

        dat = dat or datetime.datetime.now()
        dat = dat.astimezone()
        dat_text = dat.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
        iat = int(dat.timestamp())
        utcoffset = dat.utcoffset()
        if utcoffset is not None:
            # timestamp is calculated from local time as if it was UTC
            iat += int(utcoffset.total_seconds())

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
    refresh_token: str

    def get_auth_header(self) -> Dict[str, str]:
        return {'Authorization': self.token}

    def token_expired(self) -> bool:
        """
        Check if token has expired.
        Raises if it couldn't be decoded or missing "exp" field.
        """
        payload = JWTAuth.decode_token(self.token)
        return payload['exp'] - time.time() < 240


def url_with_query(r: RequestDetails) -> str:
    """url plus params"""
    if r.params is None:
        return r.url
    parsed = urllib.parse.urlparse(r.url)
    with_query = parsed._replace(query=urllib.parse.urlencode(r.params))
    url = urllib.parse.urlunparse(with_query)
    return url


def are_valid_oids(oids: List[str]) -> bool:
    """check all passed strings are 24 characters long and only contain alphanumeric characters"""
    return all(len(s) == 24 and s.isalnum() and s.islower() for s in oids)

class RplayUrl:

    @staticmethod
    def livestreams(creator_oid: str = '') -> RequestDetails:
        url = f'https://api.rplay.live/live/livestreams'
        params = {'creatorOid': creator_oid, 'lang': 'en'}
        return RequestDetails(url=url, params=params)

    @staticmethod
    def play(oid: str, user: User, key: str = '') -> RequestDetails:
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
        params = {'creatorOid': oid, 'key': key, 'lang': 'en', 'requestorOid': user.user_oid}
        params.update({'requestorOid': user.user_oid, 'loginType': 'rplay'})
        headers = user.get_auth_header()
        return RequestDetails(url=url, params=params, headers=headers)

    @staticmethod
    def getuser_by_oid(creator_oid: str) -> RequestDetails:
        """get user info and live status by userOid"""
        url = f'https://api.rplay.live/account/getuser'
        params = {'userOid': creator_oid, 'filter[]': ['_id', 'nickname', 'creatorTags'], 'lang': 'en'}
        return RequestDetails(url=url, params=params)

    @staticmethod
    def get_own_info(user: User) -> RequestDetails:
        url = f'https://api.rplay.live/account/getuser'
        params = {
            'userOid': user.user_oid,
            'options': '{"includeLoginFields":true,"includeGrabFilter":true}',
            'lang': 'en',
            'requestorOid': user.user_oid,
            'platformType': 'rplay',
            'loginType': 'rplay',
        }
        headers = {'Content-Type': 'application/json', 'Referer': 'https://rplay.live/'}
        headers.update(user.get_auth_header())
        return RequestDetails(url=url, params=params, headers=headers)

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
        params = {'contentOid': content_oid,
                  'status': 'published',
                  'withContentMetadata': True,
                  'requestCanView': True,
                  'lang': 'en'}
        headers = {}
        if user is not None:
            params.update({'requestorOid': user.user_oid, 'loginType': 'rplay'})
            headers.update(user.get_auth_header())
        return RequestDetails(url=url, params=params, headers=headers)

    @staticmethod
    def bulkcontent(content_oids: List[str]) -> RequestDetails:
        assert are_valid_oids(content_oids), f'unexpected data in content_oid list: "{content_oids}"'
        url = 'https://api2.rplay.live/content/bulk'
        params= {'omitComments': 'true'}
        data = {
            'requestFormQueue': content_oids,
            'includeDeletedContents': False,
            'loginType': None,
            'status': 'published'
        }
        headers = {'Content-Type': 'application/json', 'Referer': 'https://rplay.live/', 'Origin': 'https://rplay.live/'}
        return RequestDetails(url=url, method='POST', params=params, data_json=data, headers=headers)

    @staticmethod
    def playlist(creator_oid: str, key: str = '', key2: str = '') -> RequestDetails:
        url = 'https://api.rplay.live/live/stream/playlist.m3u8'
        params = {'creatorOid': creator_oid, 'key': key, 'key2': key2}
        return RequestDetails(url=url, params=params)

    @staticmethod
    def key2(user: User) -> RequestDetails:
        """response is the key as a plaintext"""
        url = f'https://api.rplay.live/live/key2'
        params = {'requestorOid': user.user_oid, 'loginType': 'rplay'}
        headers = user.get_auth_header()
        return RequestDetails(url=url, params=params, headers=headers)

    @staticmethod
    def login(username: str, password: str) -> RequestDetails:
        url = f'https://api.rplay.live/rplay/account/login'
        data = {'accountType': 'plax', 'checkAdmin': None, 'email': username, 'lang': 'en', 'loginType': None, 'password': password, 'platformType': 'rplay'}

        headers = {'Content-Type': 'application/json', 'Referer': 'https://rplay.live/'}
        return RequestDetails(url=url, method='POST', data=json.dumps(data), headers=headers)

    @staticmethod
    def refresh_token(user_oid: str, refresh_token: str) -> RequestDetails:
        url = f'https://api.rplay.live/rplay/account/refresh-token'
        data = {'requestorOid': user_oid}

        headers = {'Content-Type': 'application/json', 'Referer': 'https://rplay.live/', 'refresh-token': refresh_token}
        return RequestDetails(url=url, method='POST', data=json.dumps(data), headers=headers)

    @staticmethod
    def page2() -> RequestDetails:
        url = 'https://api2.rplay.live/content/page2'
        params = {
            'componentOids[]': '66e481283d5f0a8e4608c40c',
            'liveDataRequest': 'true',
            'forceFetch': 'false',
            'lang': 'en',
            'platformType': 'rplay',
        }
        headers = {'Content-Type': 'application/json', 'Referer': 'https://rplay.live/', 'platform-type': 'rplay'}
        return RequestDetails(url=url, method='POST', params=params, headers=headers)

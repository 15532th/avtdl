import datetime
import json
import logging
from textwrap import shorten
from typing import Optional, Sequence, Tuple

from dateutil import parser as dateutil_parser
from pydantic import Field, PositiveFloat

from avtdl.core.actors import ActorConfig
from avtdl.core.config import Plugins
from avtdl.core.interfaces import MAX_REPR_LEN, Record
from avtdl.core.monitors import HttpTaskMonitor, HttpTaskMonitorEntity
from avtdl.core.request import HttpClient
from avtdl.core.utils import JSONType, with_prefix


@Plugins.register('twitch', Plugins.kind.ASSOCIATED_RECORD)
class TwitchRecord(Record):
    """Represents event of a user going live on Twitch"""
    url: str
    """channel url"""
    username: str
    """username value from configuration entity"""
    title: str
    """stream title"""
    start: datetime.datetime
    """timestamp of the stream start"""
    avatar_url: Optional[str] = None
    """link to the user's avatar"""
    game: Optional[str] = None
    """game name, if present"""

    def __str__(self):
        return f'{self.url}\n{self.title}'

    def __repr__(self):
        title = shorten(self.title, MAX_REPR_LEN)
        return f'TwitchRecord(username={self.username}, title="{title}")'

    def get_uid(self) -> str:
        return self.url

    def as_embed(self) -> dict:
        return {
            'title': self.title,
            'description': self.url,
            'color': None,
            'author': {'name': self.username, 'url': self.url, 'icon_url': self.avatar_url},
            'timestamp': self.start.isoformat(),
            'footer': {'text': self.game},
            'fields': []
        }


@Plugins.register('twitch', Plugins.kind.ACTOR_ENTITY)
class TwitchMonitorEntity(HttpTaskMonitorEntity):
    username: str
    """Twitch username of a monitored channel"""
    update_interval: PositiveFloat = 300
    """how often the user will be checked for being live, in seconds"""
    most_recent_stream: Optional[str] = Field(exclude=True, default=None)
    """internal variable to persist state between updates. Used to keep last id to detect if the current livestream is the same as from the previous update"""


@Plugins.register('twitch', Plugins.kind.ACTOR_CONFIG)
class TwitchMonitorConfig(ActorConfig):
    pass


@Plugins.register('twitch', Plugins.kind.ACTOR)
class TwitchMonitor(HttpTaskMonitor):
    """
    Monitor for twitch.tv

    Monitors twitch.tv user with given username, produces a record when it goes live.
    For user `https://www.twitch.tv/username` username would be `username`.
    """
    async def get_new_records(self, entity: TwitchMonitorEntity,
                              client: HttpClient) -> Sequence[TwitchRecord]:
        record = await self.check_channel(entity, client)
        return [record] if record else []

    async def check_channel(self, entity: TwitchMonitorEntity, client: HttpClient) -> Optional[TwitchRecord]:
        parser = Parse(with_prefix(self.logger, f'[{entity.name}] '))

        body = OperationBody.use_live(entity.username)
        response = await self._get_channel_status(entity, client, body)
        stream_id, start = parser.use_live(response)
        if stream_id is None:
            self.logger.debug(f'[{entity.name}] user {entity.username} is not live')
            return None
        body = OperationBody.use_live_broadcast(entity.username)
        response = await self._get_channel_status(entity, client, body)
        title, game = parser.use_live_broadcast(response)

        if stream_id == entity.most_recent_stream:
            self.logger.debug(f'[{entity.name}] user {entity.username} is live with stream {entity.most_recent_stream}, but record was already created')
            return None
        self.logger.debug(f'[{entity.name}] user {entity.username} is live with stream {stream_id}, producing record')
        entity.most_recent_stream = stream_id

        channel_url = f'https://twitch.tv/{entity.username}/'
        record = TwitchRecord(url=channel_url, username=entity.username, title=title, avatar_url=None, start=start, game=game)
        return record

    async def _get_channel_status(self, entity: TwitchMonitorEntity, client: HttpClient, body: JSONType) -> Optional[JSONType]:
        api_url = 'https://gql.twitch.tv/gql'
        headers = {'Client-Id': 'kimne78kx3ncx6brgo4mv6wki5h1ko', 'Content-Type': 'application/json'}
        data = await self.request_json(api_url, entity, client, method='POST', headers=headers, data=body)
        return data


class OperationBody:

    @staticmethod
    def channel_avatar(username: str) -> str:
        body = [{
            'operationName': 'ChannelAvatar',
            'variables': {
                'channelLogin': username,
                'includeIsDJ': True
            },
            'extensions': {
                'persistedQuery': {
                    'version': 1,
                    'sha256Hash': '12575ab92ea9444d8bade6de529b288a05073617f319c87078b3a89e74cd783a'
                }
            }
        }]
        return json.dumps(body)

    @staticmethod
    def use_live(username: str) -> str:
        body = [{
            'operationName': 'UseLive',
            'variables': {'channelLogin': username},
            'extensions': {
                'persistedQuery': {
                    'version': 1,
                    'sha256Hash': '639d5f11bfb8bf3053b424d9ef650d04c4ebb7d94711d644afb08fe9a0fad5d9'
                }
            }
        }]
        return json.dumps(body)

    @staticmethod
    def use_live_broadcast(username: str) -> str:
        body = [{
            'operationName': 'UseLiveBroadcast',
            'variables': {'channelLogin': username},
            'extensions': {
                'persistedQuery': {
                    'version': 1,
                    'sha256Hash': '0b47cc6d8c182acd2e78b81c8ba5414a5a38057f2089b1bbcfa6046aae248bd2'
                }
            }
        }]
        return json.dumps(body)

    @staticmethod
    def home_shelf_videos(username: str) -> str:
        body = [{
            'operationName': 'HomeShelfVideos',
            'variables': {'channelLogin': username, 'first': 1},
            'extensions': {
                'persistedQuery': {
                    'version': 1,
                    'sha256Hash': '376aee6b71edb57fd47b3e3648eb9edbb6ab57bd7e65fe37ee93ae79d1ceb7cc'
                }
            }
        }]
        return json.dumps(body)

    @staticmethod
    def stream_metadata(username: str) -> str:
        body = [{
            'operationName': 'StreamMetadata',
            'variables': {'channelLogin': username},
            'extensions': {
                'persistedQuery': {
                    'version': 1,
                    'sha256Hash': 'a647c2a13599e5991e175155f798ca7f1ecddde73f7f341f39009c14dbf59962'
                }
            }
        }]
        return json.dumps(body)


class Parse:

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def use_live(self, response: JSONType) -> Tuple[Optional[str], Optional[datetime.datetime]]:
        if response is None:
            return None, None
        try:
            info: dict = response[0]['data']['user']  # type: ignore
            stream_info = info['stream']
            if stream_info is None:
                return None, None
            stream_id = stream_info['id']
            start_text = stream_info['createdAt']
            start = dateutil_parser.parse(start_text)
        except (TypeError, IndexError, KeyError) as e:
            self.logger.warning(f'failed to parse response: {type(e)} {e}')
            self.logger.debug(f'raw response: {response}')
            return None, None
        return stream_id, start

    def use_live_broadcast(self, response: JSONType) -> Tuple[Optional[str], Optional[str]]:
        if response is None:
            return None, None
        try:
            stream_info: dict = response[0]['data']['user']['lastBroadcast']  # type: ignore
            title = str(stream_info['title'])
            game_info = stream_info.get('game') or {}
            game = game_info.get('name', None)
        except (TypeError, IndexError, KeyError) as e:
            self.logger.warning(f'failed to parse response: {type(e)} {e}')
            self.logger.debug(f'raw response: {response}')
            return None, None
        return title, game

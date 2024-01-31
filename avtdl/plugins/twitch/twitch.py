import json
from textwrap import shorten
from typing import Optional, Sequence

import aiohttp
from pydantic import Field

from avtdl.core.config import Plugins
from avtdl.core.interfaces import ActorConfig, MAX_REPR_LEN, Record
from avtdl.core.monitors import HttpTaskMonitor, HttpTaskMonitorEntity


@Plugins.register('twitch', Plugins.kind.ASSOCIATED_RECORD)
class TwitchRecord(Record):
    """Represents event of a user going live on Twitch"""
    url: str
    """channel url"""
    username: str
    """username value from configuration entity"""
    title: str
    """stream title"""

    def __str__(self):
        return f'{self.url}\n{self.title}'

    def __repr__(self):
        title = shorten(self.title, MAX_REPR_LEN)
        return f'TwitchRecord(username={self.username}, title="{title}")'


@Plugins.register('twitch', Plugins.kind.ACTOR_ENTITY)
class TwitchMonitorEntity(HttpTaskMonitorEntity):
    username: str
    """Twitch username of a monitored channel"""
    update_interval: int = 300
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
    async def get_new_records(self, entity: TwitchMonitorEntity, session: aiohttp.ClientSession) -> Sequence[TwitchRecord]:
        record = await self.check_channel(entity, session)
        return [record] if record else []

    async def check_channel(self, entity: TwitchMonitorEntity, session: aiohttp.ClientSession) -> Optional[TwitchRecord]:
        response = await self._get_channel_status(entity, session)
        if response is None:
            return None
        try:
            stream_info = response[0]['data']['user']['stream']
            title = response[0]['data']['user']['lastBroadcast']['title']
            stream_id = response[0]['data']['user']['lastBroadcast']['id']
        except (TypeError, IndexError, KeyError) as e:
            self.logger.debug(f'[{entity.name}] failed to parse response: {type(e)} {e}. Raw response: {response}')
            return None
        if stream_info is None:
            self.logger.debug(f'[{entity.name}] user {entity.username} is not live')
            return None
        if stream_id == entity.most_recent_stream:
            self.logger.debug(f'[{entity.name}] user {entity.username} is live with stream {entity.most_recent_stream}, but record was already created')
            return None
        entity.most_recent_stream = stream_id

        channel_url = f'https://twitch.tv/{entity.username}/'
        record = TwitchRecord(url=channel_url, username=entity.username, title=title)
        return record

    @staticmethod
    def _prepare_body(username: str) -> str:
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

    async def _get_channel_status(self, entity: TwitchMonitorEntity, session: aiohttp.ClientSession) -> Optional[dict]:
        api_url = 'https://gql.twitch.tv/gql'
        headers = {'Client-Id': 'kimne78kx3ncx6brgo4mv6wki5h1ko', 'Content-Type': 'application/json'}
        body = self._prepare_body(entity.username)
        response = await self.request_raw(api_url, entity, session, method='POST', headers=headers, data=body)
        if response is None:
            return None
        try:
            data = await response.json()
            return data
        except Exception as e:
            self.logger.debug(f'failed to decode response: {e}')
            return None

import json
from json import JSONDecodeError
from textwrap import shorten
from typing import Optional, Sequence

import aiohttp
import pydantic
from pydantic import Field

from avtdl.core.config import Plugins
from avtdl.core.interfaces import ActorConfig, MAX_REPR_LEN, Record
from avtdl.core.monitors import HttpTaskMonitor, HttpTaskMonitorEntity


@Plugins.register('fc2', Plugins.kind.ASSOCIATED_RECORD)
class FC2Record(Record):
    """Represents event of a stream going live on FC2"""
    name: str = ''
    """name of the config entity for this user"""
    url: str
    """url of the user stream"""
    user_id: str
    """unique for the given user/channel part of the stream url"""
    title: str
    """stream title"""
    info: str
    """stream description"""
    start: str
    """timestamp of the stream start"""
    avatar_url: str
    """link to the user's avatar"""
    login_only: bool
    """whether logging in is required to view current livestream"""

    def __str__(self):
        return f'{self.url}\n{self.title}'

    def __repr__(self):
        title = shorten(self.title, MAX_REPR_LEN)
        return f'FC2Record(user_id={self.user_id}, start={self.start}, title={title})'

    def discord_embed(self) -> dict:
        return {
            'title': self.title,
            'description': self.url,
            'color': None,
            'author': {'name': self.name, 'url': self.url, 'icon_url': self.avatar_url},
            'footer': self.info,
            'fields': []
        }


@Plugins.register('fc2', Plugins.kind.ACTOR_CONFIG)
class FC2MonitorConfig(ActorConfig):
    pass


@Plugins.register('fc2', Plugins.kind.ACTOR_ENTITY)
class FC2MonitorEntity(HttpTaskMonitorEntity):
    user_id: str
    """user id, numeric part at the end of livestream url"""
    update_interval: int = 120
    """how often the monitored channel will be checked, in seconds"""
    adjust_update_interval: bool = Field(exclude=True, default=True)
    """does nothing since fc2 does not use caching headers on the endpoint used to check live status"""
    latest_live_start: str = Field(exclude=True, default='')
    """internal variable to persist state between updates. Used to distinguish between different livestreams of the user"""


@Plugins.register('fc2', Plugins.kind.ACTOR)
class FC2Monitor(HttpTaskMonitor):
    """
    Monitor for live.fc2.com

    Monitors fc2.com user with given id, produces a record when it goes live.
    For user `https://live.fc2.com/24374512/`, user id would be `24374512`.

    Since the endpoint used for monitoring does not provide the user's nickname,
    the name of the configuration entity is used instead.
    """


    async def get_new_records(self, entity: FC2MonitorEntity, session: aiohttp.ClientSession) -> Sequence[FC2Record]:
        record = await self.check_channel(entity, session)
        return [record] if record else []

    async def check_channel(self, entity: FC2MonitorEntity, session: aiohttp.ClientSession) -> Optional[FC2Record]:
        data = await self.get_metadata(entity, session)
        if data is None:
            return None
        try:
            record = self.parse_metadata(data)
            if record is None:
                return None
        except (KeyError, TypeError, JSONDecodeError, pydantic.ValidationError) as e:
            self.logger.warning(f'FC2Monitor for {entity.name}: failed to parse channel info. Raw response: {data}')
            return None
        if record.start == entity.latest_live_start:
            self.logger.debug(f'FC2Monitor for {entity.name}: user {entity.user_id} is live since {entity.latest_live_start}, but record was already created')
            return None
        entity.latest_live_start = record.start
        record.name = entity.name
        return record

    async def get_metadata(self, entity: FC2MonitorEntity, session: aiohttp.ClientSession) -> Optional[str]:
        url = 'https://live.fc2.com/api/memberApi.php'
        data = {'channel': 1, 'streamid': entity.user_id}
        text = await self.request(url, entity, session, method='POST', data=data)
        return text

    @staticmethod
    def parse_metadata(raw_data: str) -> Optional[FC2Record]:
        data = json.loads(raw_data)
        data = data['data']['channel_data']
        is_live = data['is_publish']
        if not is_live:
            return None
        start = str(data['start'])
        title = data['title']
        info = data['info']
        avatar_url = data['image']
        login_only = data['login_only']

        channel_id = str(data['channelid'])
        channel_url = f'https://live.fc2.com/{channel_id}/'

        return FC2Record(url=channel_url,
                         title=title,
                         user_id=channel_id,
                         start=start,
                         info=info,
                         avatar_url=avatar_url,
                         login_only=login_only)

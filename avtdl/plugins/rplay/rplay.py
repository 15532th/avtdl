import datetime
from json import JSONDecodeError
from textwrap import shorten
from typing import List, Optional, Sequence

import aiohttp
import pydantic
from pydantic import Field

from avtdl.core.config import Plugins
from avtdl.core.interfaces import ActorConfig, MAX_REPR_LEN, Record
from avtdl.core.monitors import HttpTaskMonitor, HttpTaskMonitorEntity
from avtdl.core.utils import Fmt


@Plugins.register('rplay', Plugins.kind.ASSOCIATED_RECORD)
class RplayRecord(Record):
    """Represents event of a RPLAY live starting"""
    url: str
    """livestream url"""
    user_id: str
    """creatorOid"""
    title: str
    """stream title"""
    description: str
    """stream description"""
    start: Optional[datetime.datetime]
    """time of the stream start"""
    name: str
    """visible name of the user"""
    avatar_url: str
    """link to the user's avatar"""
    login_only: bool
    """whether logging in is required to view current livestream"""

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
class RplayMonitorConfig(ActorConfig):
    pass


@Plugins.register('rplay', Plugins.kind.ACTOR_ENTITY)
class RplayMonitorEntity(HttpTaskMonitorEntity):
    user_id: str
    """user id, numeric part at the end of livestream url"""
    update_interval: int = 120
    """how often the monitored channel will be checked, in seconds"""
    adjust_update_interval: bool = Field(exclude=True, default=True)
    """rplay api endpoints doesn't use caching headers"""
    latest_live_start: datetime.datetime = Field(exclude=True, default=None)
    """internal variable to persist state between updates. Used to distinguish between different livestreams of the user"""


@Plugins.register('rplay', Plugins.kind.ACTOR)
class RplayMonitor(HttpTaskMonitor):
    """
    """

    async def get_new_records(self, entity: RplayMonitorEntity, session: aiohttp.ClientSession) -> Sequence[
        RplayRecord]:
        record = await self.check_channel(entity, session)
        return [record] if record else []

    async def check_channel(self, entity: RplayMonitorEntity, session: aiohttp.ClientSession) -> Optional[RplayRecord]:
        data = await self.get_metadata(entity, session)
        if data is None:
            return None
        try:
            record = self.parse_metadata(data)
            if record is None:
                return None
        except (KeyError, TypeError, JSONDecodeError, pydantic.ValidationError) as e:
            self.logger.warning(f'RplayMonitor for {entity.name}: failed to parse channel info. Raw response: {data}')
            return None
        if record.start == entity.latest_live_start:
            self.logger.debug(
                f'RplayMonitor for {entity.name}: user {entity.user_id} is live since {entity.latest_live_start}, but record was already created')
            return None
        self.logger.debug(
            f'RplayMonitor for {entity.name}: user {entity.user_id} is live since {record.start_timestamp}, producing record')
        entity.latest_live_start = record.start
        record.name = entity.name
        return record

    async def get_metadata(self, entity: RplayMonitorEntity, session: aiohttp.ClientSession) -> Optional[str]:
        text = await self.request(url, entity, session, method='POST', data=data)
        return text

    @staticmethod
    def parse_metadata(raw_data: str) -> Optional[RplayRecord]:
        ...


class RplayUrl:

    @staticmethod
    def livestreams(oid: str = '') -> str:
        return f'https://api.rplay.live/live/livestreams?creatorOid={oid}&lang=en'

    @staticmethod
    def live(oid: str, key: str = '') -> str:
        return f'https://api.rplay.live/live/play?creatorOid={oid}&key={key}&lang=en'

    @staticmethod
    def getuser(oid: str) -> str:
        return f'https://api.rplay.live/account/getuser?userOid={oid}' \
               '&filter[]=_id' \
               '&filter[]=nickname' \
               '&filter[]=creatorTags' \
               '&lang=en'

    @staticmethod
    def subscriptions(oid: str) -> str:
        return f'https://api.rplay.live/account/getuser?userOid={oid}&filter[]=subscribingTo&lang=en'

    @staticmethod
    def bulkgetusers(oids: List[str]) -> str:
        return f'https://api.rplay.live/account/bulkgetusers?users={"|".join(oids)}&toGrab=_id|nickname|isLive&lang=en'


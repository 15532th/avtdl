import json
from json import JSONDecodeError
from textwrap import shorten
from typing import Optional, Sequence

import aiohttp
import pydantic
from pydantic import Field

from core.config import Plugins
from core.interfaces import ActorConfig, LivestreamRecord, MAX_REPR_LEN
from core.monitors import HttpTaskMonitor, HttpTaskMonitorEntity


class FC2Record(LivestreamRecord):

    user_id: str
    title: str
    start: str

    def __str__(self):
        f'{self.url}\n{self.title}'

    def __repr__(self):
        title = shorten(self.title, MAX_REPR_LEN)
        return f'FC2Record(user_id={self.user_id}, start={self.start}, title={title})'

@Plugins.register('fc2', Plugins.kind.ACTOR_ENTITY)
class FC2MonitorEntity(HttpTaskMonitorEntity):
    user_id: str
    update_interval: int = 300
    latest_live_start: str = Field(exclude=True, default='')

@Plugins.register('fc2', Plugins.kind.ACTOR_CONFIG)
class FC2MonitorConfig(ActorConfig):
    pass

@Plugins.register('fc2', Plugins.kind.ACTOR)
class FC2Monitor(HttpTaskMonitor):

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
                self.logger.debug(f'FC2Monitor for {entity.name}: channel {entity.user_id} is not live')
                return None
        except (KeyError, TypeError, JSONDecodeError, pydantic.ValidationError) as e:
            self.logger.warning(f'FC2Monitor for {entity.name}: failed to parse channel info. Raw response: {data}')
            return None
        if record.start == entity.latest_live_start:
            self.logger.debug(f'FC2Monitor for {entity.name}: user {entity.user_id} is live since {entity.latest_live_start}, but record was already created')
            return None
        entity.latest_live_start = record.start
        return record

    async def get_metadata(self, entity: FC2MonitorEntity, session: aiohttp.ClientSession):
        url = 'https://live.fc2.com/api/memberApi.php'
        data = {'channel': 1, 'streamid': entity.user_id}
        response = await self.request(url, entity, session, method='POST', data=data)
        text = await response.text() if response is not None else None
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

        channel_id = str(data['channelid'])
        channel_url = f'https://live.fc2.com/{channel_id}/'

        return FC2Record(url=channel_url, title=title, user_id=channel_id, start=start)




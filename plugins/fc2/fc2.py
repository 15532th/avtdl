import json
from json import JSONDecodeError
from typing import Sequence, Optional

import aiohttp
import pydantic
from pydantic import PrivateAttr

from core.config import Plugins
from core.interfaces import ActorConfig, Record, HttpTaskMonitorEntity, HttpTaskMonitor


class FC2Record(Record):
    user_id: str
    start: str

@Plugins.register('fc2', Plugins.kind.ACTOR_ENTITY)
class FC2MonitorEntity(HttpTaskMonitorEntity):
    user_id: str
    update_interval: int = 300
    latest_live_start: PrivateAttr = None

@Plugins.register('fc2', Plugins.kind.ACTOR_CONFIG)
class FC2MonitorConfig(ActorConfig):
    pass

@Plugins.register('fc2', Plugins.kind.ACTOR)
class FC2Monitor(HttpTaskMonitor):

    async def get_new_records(self, entity: FC2MonitorEntity, session: aiohttp.ClientSession) -> Sequence[FC2Record]:
        record = await self.check_channel(entity, session)
        return [record] if record else []

    async def check_channel(self, entity: FC2MonitorEntity, session: aiohttp.ClientSession) -> Optional[FC2Record]:
        try:
            data = await self.get_metadata(entity, session)
        except Exception as e:
            self.logger.exception(f'FC2Monitor for {entity.name}: failed to check if channel {entity.user_id} is live: {e}')
            return None
        try:
            record = self.parse_metadata(data)
            if record is None:
                return None
        except (KeyError, TypeError, JSONDecodeError, pydantic.ValidationError) as e:
            self.logger.exception(f'FC2Monitor for {entity.name}: failed to parse channel info. Raw response: {data}')
            return None
        if record.start == entity.latest_live_start:
            self.logger.debug(f'FC2Monitor for {entity.name}: user {entity.user_id} is live since {entity.latest_live_start}, but record was already created')
            return None
        entity.latest_live_start = record.start
        return record

    @staticmethod
    async def get_metadata(entity: FC2MonitorEntity, session: aiohttp.ClientSession):
        url = 'https://live.fc2.com/api/memberApi.php'
        params = {'channel': 1, 'streamid': entity.user_id}
        async with session.post(url, data=params) as r:
            data = await r.text()
            return data

    @staticmethod
    def parse_metadata(data: str) -> Optional[FC2Record]:
        data = json.loads(data)
        data = data['data']['channel_data']
        is_live = data['is_publish']
        if not is_live:
            return None
        start = str(data['start'])
        title = data['title']

        channel_id = str(data['channelid'])
        channel_url = f'https://live.fc2.com/{channel_id}/'

        return FC2Record(url=channel_url, title=title, user_id=channel_id, start=start)




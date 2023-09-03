from typing import Sequence, Optional

import aiohttp
from pydantic import PrivateAttr

from core.config import Plugins
from core.interfaces import ActorConfig, Record, HttpTaskMonitorEntity, HttpTaskMonitor


class TwitcastRecord(Record):
    user_id: str
    movie_id: str

@Plugins.register('twitcast', Plugins.kind.ACTOR_ENTITY)
class TwitcastMonitorEntity(HttpTaskMonitorEntity):
    user_id: str
    most_recent_movie: PrivateAttr = None

@Plugins.register('twitcast', Plugins.kind.ACTOR_CONFIG)
class TwitcastMonitorConfig(ActorConfig):
    pass

@Plugins.register('twitcast', Plugins.kind.ACTOR)
class TwitcastMonitor(HttpTaskMonitor):

    async def get_new_records(self, entity: TwitcastMonitorEntity, session: aiohttp.ClientSession) -> Sequence[TwitcastRecord]:
        record = await self.check_channel(entity, session)
        return [record] if record else []

    async def check_channel(self, entity: TwitcastMonitorEntity, session: aiohttp.ClientSession) -> Optional[TwitcastRecord]:
        if not await self.is_live(entity, session):
            self.logger.debug(f'TwitcastMonitor for {entity.name}: user {entity.user_id} is not live')
            return None

        movie_id = await self.get_movie_id(entity, session)
        if movie_id == entity.most_recent_movie:
            self.logger.debug(f'TwitcastMonitor for {entity.name}: user {entity.user_id} is live with movie {entity.most_recent_movie}, but record was already created')
            return None
        entity.most_recent_movie = movie_id

        channel_url = f'https://twitcasting.tv/{entity.user_id}/'
        title = f'{entity.name} is live on Twitcasting at {channel_url}'
        record = TwitcastRecord(url=channel_url, title=title, user_id=entity.user_id, movie_id=movie_id)
        return record

    async def is_live(self, entity: TwitcastMonitorEntity, session: aiohttp.ClientSession) -> bool:
        url = f"https://twitcasting.tv/userajax.php?c=islive&u={entity.user_id}"
        try:
            async with session.get(url) as r:
                text = await r.text()
                return text != '0'
        except Exception as e:
            self.logger.exception(f'TwitcastMonitor for {entity.name}: failed to check if channel {entity.user_id} is live: {e}')
            return False

    async def get_movie_id(self, entity: TwitcastMonitorEntity, session: aiohttp.ClientSession) -> Optional[str]:
        url = f'https://en.twitcasting.tv/streamserver.php?target={entity.user_id}&mode=client'
        try:
            async with session.get(url) as r:
                latest_movie_info = await r.json()
        except Exception as e:
            msg = f'TwitcastMonitor for {entity.name}: failed to get current movie for {entity.user_id}: {e}'
            self.logger.exception(msg)
            return None
        try:
            movie_id = str(latest_movie_info['movie']['id'])
            return movie_id
        except (KeyError, TypeError):
            self.logger.exception(f'TwitcastMonitor for {entity.name}: failed to parse "{latest_movie_info}"')
            return None

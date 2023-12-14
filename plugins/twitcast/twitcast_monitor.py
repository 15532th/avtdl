from textwrap import shorten
from typing import Optional, Sequence

import aiohttp
from pydantic import Field

from core.config import Plugins
from core.interfaces import ActorConfig, LivestreamRecord, MAX_REPR_LEN
from core.monitors import HttpTaskMonitor, HttpTaskMonitorEntity


class TwitcastRecord(LivestreamRecord):

    user_id: str
    movie_id: str
    movie_url: str
    title: str

    def __str__(self):
        return f'{self.url}\n{self.title} ({self.movie_id})'

    def __repr__(self):
        title = shorten(self.title, MAX_REPR_LEN)
        return f'TwitcastRecord(user_id={self.user_id}, movie_id={self.movie_id}, title="{title}")'

@Plugins.register('twitcast', Plugins.kind.ACTOR_ENTITY)
class TwitcastMonitorEntity(HttpTaskMonitorEntity):
    user_id: str
    update_interval: int = 300
    most_recent_movie: Optional[str] = Field(exclude=True, default=None)

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
            return None

        movie_id = await self.get_movie_id(entity, session)
        if movie_id is None:
            self.logger.warning(f'[{entity.name}] failed to get movie id, will report this record again if it was temporarily error, will never report new records if it is permanent')
            movie_id = 'movie id is unknown'
        if movie_id == entity.most_recent_movie:
            self.logger.debug(f'[{entity.name}] user {entity.user_id} is live with movie {entity.most_recent_movie}, but record was already created')
            return None
        entity.most_recent_movie = movie_id

        channel_url = f'https://twitcasting.tv/{entity.user_id}/'
        movie_url = f'{channel_url}/movie/{movie_id}'
        title = f'{entity.name} is live on Twitcasting'
        record = TwitcastRecord(url=channel_url, user_id=entity.user_id, movie_id=movie_id, movie_url=movie_url, title=title)
        return record

    async def is_live(self, entity: TwitcastMonitorEntity, session: aiohttp.ClientSession) -> bool:
        url = f"https://twitcasting.tv/userajax.php?c=islive&u={entity.user_id}"
        text = await self.request(url, entity, session)
        if text is None:
            self.logger.warning(f'[{entity.name}] failed to check if channel {entity.user_id} is live')
            return False
        return text != '0'

    async def get_movie_id(self, entity: TwitcastMonitorEntity, session: aiohttp.ClientSession) -> Optional[str]:
        url = f'https://en.twitcasting.tv/streamserver.php?target={entity.user_id}&mode=client'
        response = await self.request_raw(url, entity, session)
        if response is None:
            return None
        try:
            latest_movie_info = await response.json()
        except Exception as e:
            msg = f'[{entity.name}] failed to get current movie for {entity.user_id}: {e}'
            self.logger.warning(msg)
            return None
        try:
            movie_id = str(latest_movie_info['movie']['id'])
            return movie_id
        except (KeyError, TypeError):
            self.logger.warning(f'[{entity.name}] failed to parse "{latest_movie_info}"')
            return None

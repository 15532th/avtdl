from textwrap import shorten
from typing import Optional, Sequence

import aiohttp
from pydantic import Field

from avtdl.core.config import Plugins
from avtdl.core.interfaces import ActorConfig, MAX_REPR_LEN, Record
from avtdl.core.monitors import HttpTaskMonitor, HttpTaskMonitorEntity


@Plugins.register('twitcast', Plugins.kind.ASSOCIATED_RECORD)
class TwitcastRecord(Record):
    """Represents an event of a user going live on Twitcasting"""
    user_id: str
    """unique part of the channel url"""
    movie_id: str
    """unique id for the current livestream"""
    url: str
    """user (channel) url"""
    movie_url: str
    """current livestream url"""
    title: str
    """livestream title"""
    thumbnail_url: str
    """link to the stream thumbnail"""

    def __str__(self):
        return f'{self.url}\n{self.title} ({self.movie_id})'

    def __repr__(self):
        title = shorten(self.title, MAX_REPR_LEN)
        return f'TwitcastRecord(user_id={self.user_id}, movie_id={self.movie_id}, title="{title}")'

    def get_uid(self) -> str:
        return self.movie_id

    def discord_embed(self) -> dict:
        return {
            'title': self.movie_url,
            'description': None,
            'color': None,
            'author': {'name': self.user_id, 'url': self.url},
            'image': {'url': self.thumbnail_url},
            'fields': []
        }

@Plugins.register('twitcast', Plugins.kind.ACTOR_ENTITY)
class TwitcastMonitorEntity(HttpTaskMonitorEntity):
    user_id: str
    """user id that should be monitored"""
    update_interval: float = 60
    """how often the user will be checked for being live, in seconds"""
    adjust_update_interval: bool = Field(exclude=True, default=True)
    """does nothing since Twitcasting does not use caching headers on endpoint used to check live status"""
    most_recent_movie: Optional[str] = Field(exclude=True, default=None)
    """internal variable to persist state between updates. Used to keep movie_id to detect if the current live is the same as from the last update"""


@Plugins.register('twitcast', Plugins.kind.ACTOR_CONFIG)
class TwitcastMonitorConfig(ActorConfig):
    pass

@Plugins.register('twitcast', Plugins.kind.ACTOR)
class TwitcastMonitor(HttpTaskMonitor):
    """
    Monitor for twitcasting.tv

    Monitors twitcasting.tv user with a given id, produces a record when it goes live.
    For user `https://twitcasting.tv/c:username` user id would be `c:username`.

    Streams on Twitcasting might be set to be visible only for members of a specific group.
    For monitoring such streams it is necessarily to provide login cookies of
    an account being a member of the group. Password-protected and age-restricted streams
    do not require that.

    Rate limits for the endpoint used to check if user is live are likely relatively high,
    but it is better to keep `update_interval` big enough for combined amount of updates
    for all monitored users to not exceed one request per second.
    """


    async def get_new_records(self, entity: TwitcastMonitorEntity, session: aiohttp.ClientSession) -> Sequence[TwitcastRecord]:
        record = await self.check_channel(entity, session)
        return [record] if record else []

    async def check_channel(self, entity: TwitcastMonitorEntity, session: aiohttp.ClientSession) -> Optional[TwitcastRecord]:
        if not await self.is_live(entity, session):
            return None

        movie_id = await self.get_movie_id(entity, session)
        if movie_id is None:
            self.logger.warning(f'[{entity.name}] failed to get movie id, will report this record again if it was a temporary error, will never report new records if it is permanent')
            movie_id = 'movie id is unknown'
        if movie_id == entity.most_recent_movie:
            self.logger.debug(f'[{entity.name}] user {entity.user_id} is live with movie {entity.most_recent_movie}, but record was already created')
            return None
        self.logger.debug(f'[{entity.name}] user {entity.user_id} is live with movie {movie_id}, producing record')
        entity.most_recent_movie = movie_id

        channel_url = f'https://twitcasting.tv/{entity.user_id}'
        movie_url = f'{channel_url}/movie/{movie_id}'
        thumbnail_url = f'{channel_url}/thumb/{movie_id}'
        title = f'{entity.name} is live on Twitcasting'
        record = TwitcastRecord(url=channel_url, user_id=entity.user_id, movie_id=movie_id, movie_url=movie_url, title=title, thumbnail_url=thumbnail_url)
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

import asyncio
import logging
from collections import defaultdict
from pathlib import Path
from typing import Sequence, Optional

import aiohttp
from pydantic import PrivateAttr

from core import utils
from core.config import Plugins
from core.interfaces import TaskMonitor, TaskMonitorEntity, ActorConfig, Record


class TwitcastRecord(Record):
    user_id: str
    movie_id: str

@Plugins.register('twitcast', Plugins.kind.ACTOR_ENTITY)
class TwitcastMonitorEntity(TaskMonitorEntity):
    name: str
    user_id: str
    cookies_file: Optional[Path] = None
    update_interval: int = 300

    cookies: PrivateAttr = None
    most_recent_movie: PrivateAttr = None
    queue: PrivateAttr = None
    update_request: PrivateAttr = None
    update_completed: PrivateAttr = None

    def model_post_init(self, __context) -> None:
        self.cookies = utils.load_cookies(self.cookies_file) if self.cookies_file else None


@Plugins.register('twitcast', Plugins.kind.ACTOR_CONFIG)
class TwitcastMonitorConfig(ActorConfig):
    pass

@Plugins.register('twitcast', Plugins.kind.ACTOR)
class TwitcastMonitor(TaskMonitor):

    def __init__(self, conf: TwitcastMonitorConfig, entities: Sequence[TwitcastMonitorEntity]):
        super().__init__(conf, entities)
        self.grouped_tasks = {}

    async def get_new_records(self, entity: TwitcastMonitorEntity) -> Sequence[TwitcastRecord]:
        queue = entity.queue
        entity.update_completed.clear()
        entity.update_request.set()
        logging.debug(f'get_new_records for {entity.name}: set update_request')
        await entity.update_completed.wait()
        logging.debug(f'get_new_records for {entity.name}: checking queue for new records')
        new_records = [await queue.get() for _ in range(queue.qsize())]
        logging.debug(f'get_new_records for {entity.name}: got {len(new_records)} records on update')
        return new_records

    @classmethod
    async def session_task_for(cls, entities: Sequence[TwitcastMonitorEntity]):
        async with aiohttp.ClientSession(cookies=entities[0].cookies) as session:
            tasks = [asyncio.create_task(cls.monitor_task_for(entity, session)) for entity in entities]
            await asyncio.wait(tasks, return_when=asyncio.ALL_COMPLETED)

    @classmethod
    async def monitor_task_for(cls, entity: TwitcastMonitorEntity, session: aiohttp.ClientSession):
            while True:
                await entity.update_request.wait()
                logging.debug(f'monitor_task_for {entity.name} got update request')
                entity.update_request.clear()

                record = await cls.check_channel(entity, session)
                if record is not None:
                    await entity.queue.put(record)
                    logging.debug(f'monitor_task_for {entity.name} added new record ({record.movie_id=})')
                entity.update_completed.set()

    @classmethod
    async def check_channel(cls, entity: TwitcastMonitorEntity, session: aiohttp.ClientSession) -> Optional[TwitcastRecord]:
        if not await cls.is_live(entity, session):
            logging.debug(f'TwitcastMonitor for {entity.name}: user {entity.user_id} is not live')
            return None

        movie_id = await cls.get_movie_id(entity, session)
        if movie_id == entity.most_recent_movie:
            logging.debug(f'TwitcastMonitor for {entity.name}: user {entity.user_id} is live with movie {entity.most_recent_movie}, but record was already created')
            return None
        entity.most_recent_movie = movie_id

        channel_url = f'https://twitcasting.tv/{entity.user_id}/'
        title = f'{entity.name} is live on Twitcasting at {channel_url}'
        record = TwitcastRecord(url=channel_url, title=title, user_id=entity.user_id, movie_id=movie_id)
        return record

    @staticmethod
    async def is_live(entity: TwitcastMonitorEntity, session: aiohttp.ClientSession) -> bool:
        url = f"https://twitcasting.tv/userajax.php?c=islive&u={entity.user_id}"
        try:
            async with session.get(url) as r:
                text = await r.text()
                return text != '0'
        except Exception as e:
            logging.exception(f'TwitcastMonitor for {entity.name}: failed to check if channel {entity.user_id} is live: {e}')
            return False

    @staticmethod
    async def get_movie_id(entity: TwitcastMonitorEntity, session: aiohttp.ClientSession) -> Optional[str]:
        url = f'https://en.twitcasting.tv/streamserver.php?target={entity.user_id}&mode=client'
        try:
            async with session.get(url) as r:
                latest_movie_info = await r.json()
        except Exception as e:
            logging.exception(
                f'TwitcastMonitor for {entity.name}: failed to get current movie for {entity.user_id}: {e}')
            return None
        try:
            movie_id = str(latest_movie_info['movie']['id'])
            return movie_id
        except (KeyError, TypeError):
            logging.exception(f'TwitcastMonitor for {entity.name}: failed to parse "{latest_movie_info}"')
            return None

    async def run(self):
        for entity in self.entities.values():
            entity.queue = asyncio.Queue()
            entity.update_request = asyncio.Event()
            entity.update_completed = asyncio.Event()

        grouped_entities = defaultdict(list)
        for entity in self.entities.values():
            grouped_entities[str(entity.cookies_file)].append(entity)
        for cookie_file_name, entities in grouped_entities.items():
            self.grouped_tasks[cookie_file_name] = asyncio.create_task(self.session_task_for(entities))
        await super().run()


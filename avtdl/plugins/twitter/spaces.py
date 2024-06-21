import asyncio
from typing import Optional, Sequence, Set

import aiohttp
from pydantic import FilePath

from avtdl.core import utils
from avtdl.core.interfaces import Action, ActionEntity, ActorConfig, Record
from avtdl.core.plugins import Plugins
from avtdl.core.utils import SessionStorage, find_matching_field_value, monitor_tasks_set
from avtdl.plugins.twitter.endpoints import AudioSpaceEndpoint, LiveStreamEndpoint
from avtdl.plugins.twitter.extractors import TwitterSpaceRecord, find_space_id, parse_space, space_url_by_id

Plugins.register('twitter.space', Plugins.kind.ASSOCIATED_RECORD)(TwitterSpaceRecord)

SPACE_URL_PATTERN = '/i/spaces/'


@Plugins.register('twitter.space', Plugins.kind.ACTOR_CONFIG)
class TwitterSpaceConfig(ActorConfig):
    pass


@Plugins.register('twitter.space', Plugins.kind.ACTOR_ENTITY)
class TwitterSpaceEntity(ActionEntity):
    cookies_file: FilePath
    """path to a text file containing cookies in Netscape format"""
    url: str = 'https://twitter.com'
    """Twitter domain name"""


@Plugins.register('twitter.space', Plugins.kind.ACTOR)
class TwitterSpace(Action):
    """
    Retrieve Twitter Space metadata from tweet

    Take a record, coming from a Twitter monitor, check if it
    has a link to a Twitter Space, and if so try to retrieve
    additional information on the space, such as title and start time.

    Produces a TwitterSpaceRecord if currently processed record
    comes from a Twitter monitor, contains a link to a Space and
    the metadata was retrieved successfully.
    """

    def __init__(self, conf: TwitterSpaceConfig, entities: Sequence[TwitterSpaceEntity]):
        super().__init__(conf, entities)
        self.sessions = SessionStorage(self.logger)
        self.tasks: Set[asyncio.Task] = set()

    def handle(self, entity: TwitterSpaceEntity, record: Record):
        space_id = get_space_id(record)
        if space_id is None:
            return None
        task = asyncio.create_task(self.handle_space(entity, space_id))
        self.tasks.add(task)

    async def run(self) -> None:
        self.sessions.run()
        await monitor_tasks_set(self.tasks)

    async def handle_space(self, entity: TwitterSpaceEntity, space_id: str):
        session = self.sessions.get_session(entity.cookies_file)
        space = await self.fetch_space(session, entity, space_id)
        if space is None:
            return
        if not is_upcoming(space):
            space.media_url = await self.fetch_media_url(session, entity, space) or space.media_url
        self.on_record(entity, space)

    async def fetch_space(self, session: aiohttp.ClientSession, entity: TwitterSpaceEntity, space_id: str) -> Optional[TwitterSpaceRecord]:
        r = AudioSpaceEndpoint.prepare(entity.url, session.cookie_jar, space_id)
        data = await utils.request_json(r.url, session, self.logger, params=r.params, headers=r.headers, retry_times=3, retry_delay=5)
        if data is None:
            self.logger.warning(f'[{entity.name}] failed to retrieve metadata for {space_url_by_id(space_id)}')
            return None
        try:
            space = parse_space(data)
        except ValueError as e:
            self.logger.warning(f'[{entity.name}] failed to parse Space metadata for "{space_url_by_id(space_id)}": {e}')
            return None
        return space

    async def fetch_media_url(self, session: aiohttp.ClientSession, entity: TwitterSpaceEntity, space: TwitterSpaceRecord) -> Optional[str]:
        r = LiveStreamEndpoint.prepare(entity.url, session.cookie_jar, space.media_key)
        data = await utils.request_json(r.url, session, self.logger, params=r.params, headers=r.headers, retry_times=3, retry_delay=5)
        if data is None:
            self.logger.debug(f'[{entity.name}] failed to retrieve media url for {space.url}')
            return None
        source = data.get('source') or {}
        media_url = source.get('location') or source.get('noRedirectPlaybackUrl') or None
        if media_url is None:
            self.logger.debug(f'[{entity.name}] failed to parse media url for {space.url}. Raw data: "{data}"')
            return None
        return media_url


def get_space_id(record: Record) -> Optional[str]:
    field = find_matching_field_value(record, pattern=SPACE_URL_PATTERN)
    if field is None:
        return None
    space_id = find_space_id(str(field))
    return space_id


def is_upcoming(space: TwitterSpaceRecord) -> bool:
    return space.scheduled is not None and space.started is None

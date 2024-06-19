import asyncio
import datetime
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
    emit_upcoming: bool = True
    """whether record should be produced for spaces that are scheduled to start in the future"""
    emit_on_start: bool = True
    """if enabled, a record is produced when upcoming space starts"""  # if it hasn't started on time should do polling like ytarchive does
    emit_on_end: bool = True
    """if enabled, a record is produced when upcoming space ends"""  # if it hasn't started on time should do polling like ytarchive does


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

    UPCOMING_SPACE_POLL_INTERVAL = 5
    ONGOING_SPACE_POLL_INTERVAL = 600

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
        if is_upcoming(space):
            if entity.emit_upcoming:
                self.on_record(entity, space)
        else:
            space.media_url = await self.fetch_media_url(session, entity, space) or space.media_url
            self.on_record(entity, space)

        if (is_upcoming(space) and (entity.emit_on_start or entity.emit_on_end)) or (
                is_ongoing(space) and entity.emit_on_end):
            task = asyncio.create_task(self.handle_upcoming(session, entity, space))
            self.tasks.add(task)

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

    async def handle_upcoming(self, session: aiohttp.ClientSession, entity: TwitterSpaceEntity, space: TwitterSpaceRecord):
        while True:
            if is_unknown(space):
                self.logger.warning(f'[{entity.name}] space is not upcoming, ongoing or ended: {space!r}')
                break
            if has_ended(space):
                break
            if is_ongoing(space):
                space.media_url = await self.fetch_media_url(session, entity, space) or space.media_url
                if entity.emit_on_start:
                    self.on_record(entity, space)
                if entity.emit_on_end:
                    await self.handle_ongoing(session, entity, space)
                break
            if space.scheduled is None:
                self.logger.warning(f'[{entity.name}] upcoming space has no scheduled field: {space!r}')
                break
            until_start = (datetime.datetime.now(tz=datetime.timezone.utc) - space.scheduled).total_seconds()
            until_start = max(until_start, self.UPCOMING_SPACE_POLL_INTERVAL)
            await asyncio.sleep(until_start)
            if until_start > self.UPCOMING_SPACE_POLL_INTERVAL:
                # first update at presumable a starting time,
                space = await self.fetch_space(session, entity, space.uid) or space
            media_url = await self.fetch_media_url(session, entity, space)
            if media_url is not None:
                space.media_url = media_url
                self.on_record(entity, space)
                break

    async def handle_ongoing(self, session: aiohttp.ClientSession, entity: TwitterSpaceEntity, space: TwitterSpaceRecord):
        ...



def get_space_id(record: Record) -> Optional[str]:
    field = find_matching_field_value(record, pattern=SPACE_URL_PATTERN)
    if field is None:
        return None
    space_id = find_space_id(str(field))
    return space_id


def is_upcoming(space: TwitterSpaceRecord) -> bool:
    return space.scheduled is not None and space.started is None


def is_ongoing(space: TwitterSpaceRecord) -> bool:
    return space.started is not None and space.ended is None


def has_ended(space: TwitterSpaceRecord) -> bool:
    return space.ended is not None


def is_unknown(space: TwitterSpaceRecord) -> bool:
    return space.scheduled is None and space.started is None and space.ended is None
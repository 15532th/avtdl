import asyncio
import datetime
import urllib.parse
from enum import Enum
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
        if SpaceState.is_upcoming(space):
            await self.handle_upcoming(session, entity, space)
            if entity.emit_on_end:
                await self.handle_ended(session, entity, space)
            return
        elif SpaceState.is_ongoing(space):
            await self.handle_ongoing(session, entity, space)
            return
        elif SpaceState.has_ended(space):
            await self.handle_ended(session, entity, space)
            return
        else:
            self.logger.warning(f'[{entity.name}] space {space.url} state is unknown, aborting processing. {space.model_dump()}')
            return


    async def handle_upcoming(self, session: aiohttp.ClientSession, entity: TwitterSpaceEntity, space: TwitterSpaceRecord):
        if entity.emit_upcoming:
            self.on_record(entity, space)
        assert space.scheduled is not None, f'upcoming space has no scheduled: {space.model_dump()}'
        if not entity.emit_on_start and not entity.emit_on_end:
            return
        while True: # waiting until start
            delay = (datetime.datetime.now(tz=datetime.timezone.utc) - space.scheduled)
            delay_seconds = delay.total_seconds()
            if delay_seconds < 5:
                break
            self.logger.debug(f'[{entity.name}] space {space.url} starts at {space.scheduled}, sleeping for {delay}')
            await asyncio.sleep(delay_seconds)
            space = await self.fetch_space(session, entity, space.uid) or space
        self.logger.debug(f'[{entity.name}] space {space.url} should start now, fetching media_url')
        for attempt in range(300):
            media_url = await self.fetch_media_url(session, entity, space)
            if media_url is not None:
                space.media_url = media_url
                if entity.emit_on_start:
                    self.on_record(entity, space)
                break
            self.logger.debug(f'[{entity.name}] upcoming space {space.url} got no media_url on {attempt} attempt, will try again')
            await asyncio.sleep(30)
        else:
            self.logger.debug(f'[{entity.name}] failed to fetch media_url for ongoing space {space.url}, giving up')

    async def handle_ongoing(self, session: aiohttp.ClientSession, entity: TwitterSpaceEntity, space: TwitterSpaceRecord):
        self.logger.debug(f'[{entity.name}] space {space.url} is ongoing since {space.started}, fetching live media_url')
        for attempt in range(100):
            media_url = await self.fetch_media_url(session, entity, space)
            if media_url is not None:
                space.media_url = media_url
                if StreamUrlType.is_live(media_url):
                    self.logger.debug(f'[{entity.name}] {space.url} got livestream media_url: "{media_url}"')
                    if entity.emit_on_start:
                        self.on_record(entity, space)
                    if entity.emit_on_end:
                        await self.handle_ended(session, entity, space)
                elif StreamUrlType.is_replay(media_url):
                    self.logger.debug(f'[{entity.name}] {space.url} got replay media_url: "{media_url}"')
                    if entity.emit_on_end:
                        self.on_record(entity, space)
                else:
                    self.logger.warning(f'[{entity.name}] {space.url} got media_url with unknown type: "{media_url}"')
                break
            self.logger.debug(f'[{entity.name}] supposedly ongoing space {space.url} got no media_url on {attempt} attempt, will try again')
            await asyncio.sleep(300)
        else:
            self.logger.debug(f'[{entity.name}] failed to fetch media_url for ongoing space {space.url}, giving up')


    async def handle_ended(self, session: aiohttp.ClientSession, entity: TwitterSpaceEntity, space: TwitterSpaceRecord):
        self.logger.debug(f'[{entity.name}] space {space.url} has ended at {space.ended}, fetching replay media_url')
        for attempt in range(10):
            media_url = await self.fetch_media_url(session, entity, space)
            if media_url is not None:
                space.media_url = media_url
                if StreamUrlType.is_live(media_url):
                    self.logger.debug(f'[{entity.name}] {space.url} on {attempt} attempt got livestream media_url, will try again later. media_url: "{media_url}"')
                elif StreamUrlType.is_replay(media_url):
                    self.logger.debug(f'[{entity.name}] {space.url} on {attempt} attempt got replay media_url, done. media_url: "{media_url}"')
                    self.on_record(entity, space)
                    break
                else:
                    self.logger.warning(f'[{entity.name}] {space.url} on {attempt} attempt got media_url with unexpected type: "{media_url}"')
                    break
            self.logger.debug(f'[{entity.name}] on {attempt} attempt failed to fetch media_url for ended space {space.url}, will try again')
            await asyncio.sleep(300)
        else:
            self.logger.debug(f'[{entity.name}] failed to fetch media_url for ended space {space.url}, giving up')


    async def fetch_space(self, session: aiohttp.ClientSession, entity: TwitterSpaceEntity, space_id: str) -> Optional[TwitterSpaceRecord]:
        self.logger.debug(f'[{entity.name}] fetch space {space_id}')
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
        self.logger.debug(f'[{entity.name}] fetch media url for space {space.url}')
        r = LiveStreamEndpoint.prepare(entity.url, session.cookie_jar, space.media_key)
        data = await utils.request_json(r.url, session, self.logger, params=r.params, headers=r.headers, retry_times=0)
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


class SpaceState(str, Enum):
    UPCOMING = 'upcoming'
    ONGOING = 'ongoing'
    ENDED = 'ended'
    UNKNOWN = 'unknown'

    @classmethod
    def from_space_record(cls, record: TwitterSpaceRecord) -> 'SpaceState':
        if record.ended is not None:
            return cls.ENDED
        elif record.started is not None:
            return cls.ONGOING
        elif record.scheduled is not None:
            return cls.UPCOMING
        else:
            return cls.UNKNOWN

    @staticmethod
    def is_upcoming(space: TwitterSpaceRecord) -> bool:
        return space.scheduled is not None and space.started is None

    @staticmethod
    def is_ongoing(space: TwitterSpaceRecord) -> bool:
        return space.started is not None and space.ended is None

    @staticmethod
    def has_ended(space: TwitterSpaceRecord) -> bool:
        return space.ended is not None

    @staticmethod
    def is_unknown(space: TwitterSpaceRecord) -> bool:
        return space.scheduled is None and space.started is None and space.ended is None


class StreamUrlType(str, Enum):
    LIVE = 'live'
    REPLAY = 'replay'
    UNKNOWN = 'unknown'

    @classmethod
    def from_url(cls, url: str) -> 'StreamUrlType':
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        types = query.get('type', [])
        if 'replay' in types:
            return StreamUrlType.REPLAY
        if 'live' in types:
            return StreamUrlType.LIVE
        return StreamUrlType.UNKNOWN

    @classmethod
    def is_live(cls, url: str) -> bool:
        return cls.from_url(url) == cls.LIVE

    @classmethod
    def is_replay(cls, url: str) -> bool:
        return cls.from_url(url) == cls.REPLAY
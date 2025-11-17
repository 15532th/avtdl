import asyncio
import datetime
import json
import logging
import re
import urllib.parse
from enum import Enum
from typing import Callable, Optional, Sequence

from pydantic import FilePath

from avtdl.core.actions import TaskAction, TaskActionConfig, TaskActionEntity
from avtdl.core.db import BaseDbConfig, RecordDB
from avtdl.core.interfaces import Record
from avtdl.core.plugins import Plugins
from avtdl.core.request import DataResponse, Delay, HttpClient, NoResponse
from avtdl.core.runtime import RuntimeContext, TaskStatus
from avtdl.core.utils import find_matching_field_value
from avtdl.plugins.twitter.endpoints import AudioSpaceEndpoint, LiveStreamEndpoint
from avtdl.plugins.twitter.extractors import TwitterSpaceRecord, find_space_id, parse_media_url, parse_space, \
    space_url_by_id

Plugins.register('twitter.space', Plugins.kind.ASSOCIATED_RECORD)(TwitterSpaceRecord)

SPACE_URL_PATTERN = '/i/spaces/'


@Plugins.register('twitter.space', Plugins.kind.ACTOR_CONFIG)
class TwitterSpaceConfig(TaskActionConfig, BaseDbConfig):
    pass


@Plugins.register('twitter.space', Plugins.kind.ACTOR_ENTITY)
class TwitterSpaceEntity(TaskActionEntity):
    cookies_file: FilePath
    """path to a text file containing cookies in Netscape format"""
    url: str = 'https://twitter.com'
    """Twitter domain name"""
    emit_immediately: bool = True
    """whether record should be produced immediately, regardless of media_url presence.
    When disabled, only records with valid media_url are produced, depending on the following settings"""
    emit_on_live: bool = True
    """if enabled, a record is produced when livestream media_url becomes available"""
    emit_on_archive: bool = True
    """if enabled, a record is produced when archive media_url becomes available"""
    emit_on_end: bool = False
    """if enabled, a record is produced when the space ends, even if there is no archive"""


@Plugins.register('twitter.space', Plugins.kind.ACTOR)
class TwitterSpace(TaskAction):
    """
    Retrieve Twitter Space metadata from tweet

    Take a record (normally coming from a Twitter monitor), check if it
    has a link to a Twitter Space, and if so try to retrieve
    additional information on the space, such as title and start time.

    Produces a TwitterSpaceRecord if currently processed record
    contains a link to a Space and the metadata was retrieved successfully.

    It is possible to produce additional records with updated metadata at the
    beginning and/or at the end of the space by toggling the `emit_*` settings.
    However, a single state change should only produce one record. For example,
    if a space has already ended before the first update, only a single record is
    produced with all `emit_*` options enabled.
    """

    CHECK_UPCOMING_DELAY = 30.0
    CHECK_LIVE_DELAY = 300.0
    CHECK_ENDED_DELAY = 30.0
    MAX_SEQUENTIAL_CHECKS = 100

    def __init__(self, conf: TwitterSpaceConfig, entities: Sequence[TwitterSpaceEntity], ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)
        self.db = RecordDB(conf.db_path, logger=self.logger.getChild('db'))

    async def handle_record_task(self, logger: logging.Logger, client: HttpClient,
                                 entity: TwitterSpaceEntity, record: Record, info: TaskStatus):
        space_id = get_space_id(record)
        if space_id is None:
            return
        info = TaskStatus(self.conf.name, entity.name, 'starting', record)
        await self.handle_space(entity, space_id, record, client, logger, info)

    async def handle_space(self, entity: TwitterSpaceEntity, space_id: str, source_record: Record,
                           client: HttpClient, logger: logging.Logger, info: TaskStatus):
        should_emit_something: bool = entity.emit_immediately
        should_emit_live_url: bool = entity.emit_on_live
        should_emit_replay_url: bool = entity.emit_on_archive
        should_emit_on_end: bool = entity.emit_on_end

        def handle_update_result(space: TwitterSpaceRecord) -> bool:
            nonlocal should_emit_something
            nonlocal should_emit_live_url
            nonlocal should_emit_replay_url
            nonlocal should_emit_on_end

            if space.chain is None:
                space.chain = source_record.chain
            if space.origin is None:
                space.origin = source_record.origin

            if should_emit_replay_url:
                if space.media_url is not None and StreamUrlType.is_replay(space.media_url):
                    self.on_record(entity, space)
                    should_emit_replay_url = False
                    should_emit_live_url = False
                    should_emit_something = False
                    logger.debug(f'task emit_on_archive successfully completed for {space.url}')
                elif not space.recording_enabled:
                    should_emit_replay_url = False
                    logger.debug(f'task emit_on_archive for {space.url} is cancelled: recording is disabled')
            if should_emit_on_end:
                if space.ended is not None:
                    self.on_record(entity, space)
                    should_emit_on_end = False
                    should_emit_live_url = False
                    should_emit_something = False
                    logger.debug(f'task emit_on_end successfully completed for {space.url}')
            if should_emit_live_url:
                if space.media_url is not None and StreamUrlType.is_live(space.media_url):
                    self.on_record(entity, space)
                    should_emit_live_url = False
                    should_emit_something = False
                    logger.debug(f'task emit_on_live successfully completed for {space.url}')
                elif space.ended is not None:
                    should_emit_live_url = False
                    logger.debug(f'task emit_on_live for {space.url} is cancelled: space ended')
            if should_emit_something:
                self.on_record(entity, space)
                should_emit_something = False
                logger.debug(f'task emit_immediately completed for {space.url}')
            done = not (should_emit_something or should_emit_live_url or should_emit_on_end or should_emit_replay_url)
            if done:
                logger.debug(f'all tasks completed for {space.url}')
            return done

        space = await self.fetch_space(logger, client, entity, space_id)
        if space is None:
            return
        if self.db.record_exists(space, entity.name):
            logger.debug(f'space {space_id} has already been processed')
            return
        info.set_status('initial update', space)
        space.media_url = await self.wait_for_any_url(logger, client, entity, space) or None
        space.master_url = await get_static_playlist_url(client, space.media_url, logger)
        self.db.store_records([space], entity.name)

        done = handle_update_result(space)
        if done:
            return

        while True:
            if SpaceState.is_upcoming(space):
                info.set_status('waiting for space to start', space)
                media_url = await self.wait_for_live(logger, client, entity, space)
            elif SpaceState.is_ongoing(space):
                info.set_status('waiting for space to end', space)
                media_url = await self.wait_for_replay(logger, client, entity, space)
                if media_url == '':
                    logger.debug(f'media url unavailable for running space {space.url}, updating space metadata to see if it ended')
            elif SpaceState.has_ended(space):
                info.set_status('handling ended space', space)
                media_url = await self.wait_for_any_url(logger, client, entity, space)
                if media_url == '':
                    logger.debug(f'space {space.url} has ended at {space.ended} and media url is unavailable. The space likely does not have archive at this point')
                    break
            else:
                logger.warning(f'space {space.url} state is unknown, aborting. {space.model_dump()}')
                break

            info.set_status('updating space status', space)
            old_media_url = space.media_url
            old_master_url = space.master_url

            space = await self.fetch_space(logger, client, entity, space_id) or space

            if media_url:
                space.media_url = media_url
                space.master_url = await get_static_playlist_url(client, space.media_url, logger)
            else:
                space.media_url = old_media_url
                space.master_url = old_master_url
            self.db.store_records([space], entity.name)

            done = handle_update_result(space)
            if done:
                info.set_status(f'all done', space)
                break

    async def wait_for_live(self, logger: logging.Logger, client: HttpClient,
                            entity: TwitterSpaceEntity, space: TwitterSpaceRecord) -> Optional[str]:
        assert space.scheduled is not None, f'upcoming space has no scheduled: {space.model_dump()}'
        while True:  # waiting until start
            delay = (space.scheduled - datetime.datetime.now(tz=datetime.timezone.utc))
            delay_seconds = delay.total_seconds()
            if delay_seconds < self.CHECK_UPCOMING_DELAY:
                break
            logger.debug(f'space {space.url} starts at {space.scheduled}, sleeping for {delay}')
            await asyncio.sleep(delay_seconds)
            return None

        logger.debug(f'space {space.url} should start now, fetching media_url')

        def is_done(media_url: str) -> bool:
            return media_url != ''

        return await self.wait_for_media_url(logger, client, entity, space, self.CHECK_UPCOMING_DELAY, self.MAX_SEQUENTIAL_CHECKS, is_done)

    async def wait_for_replay(self, logger: logging.Logger, client: HttpClient, entity: TwitterSpaceEntity, space: TwitterSpaceRecord):
        logger.debug(f'space {space.url} is ongoing since {space.started}, fetching live media_url')

        def is_done(media_url: str) -> bool:
            if media_url == '':
                return True
            elif StreamUrlType.is_live(media_url):
                return False
            elif StreamUrlType.is_replay(media_url):
                return True
            else:
                logger.debug(f'space {space.url} got media_url with unknown type: {media_url}')
                return True

        return await self.wait_for_media_url(logger, client,
                                             entity, space,
                                             self.CHECK_LIVE_DELAY, self.MAX_SEQUENTIAL_CHECKS, is_done)

    async def wait_for_any_url(self, logger: logging.Logger, client: HttpClient,
                               entity: TwitterSpaceEntity, space: TwitterSpaceRecord):
        return await self.wait_for_media_url(logger, client,
                                             entity, space,
                                             self.CHECK_ENDED_DELAY, self.MAX_SEQUENTIAL_CHECKS, lambda _: True)

    async def wait_for_media_url(self, logger: logging.Logger, client: HttpClient,
                                 entity: TwitterSpaceEntity, space: TwitterSpaceRecord,
                                 retry_delay: float, max_attempts: int, is_done: Callable[[str], bool]) -> Optional[str]:
        base_retry_delay = retry_delay
        for attempt in range(max_attempts):
            media_url = await self.fetch_media_url(logger, client, entity, space)
            if media_url is None:
                retry_delay = Delay.get_next(retry_delay)
                logger.debug(f'{space.url} got no media_url on {attempt} attempt, retry after {retry_delay}')
            elif is_done(media_url):
                return media_url
            else:
                retry_delay = base_retry_delay
                logger.debug(f'{space.url} got no media_url of expected type on {attempt} attempt, retry after {retry_delay}')
            await asyncio.sleep(retry_delay)

        logger.debug(f'failed to fetch media_url of expected type after {max_attempts} attempts for {space.url}')
        return None

    async def fetch_space(self, logger: logging.Logger, client: HttpClient,
                          entity: TwitterSpaceEntity, space_id: str) -> Optional[TwitterSpaceRecord]:
        logger.debug(f'fetching metadata for space {space_url_by_id(space_id)}')
        request_details = AudioSpaceEndpoint.prepare(entity.url, client.cookie_jar, space_id)
        response = await client.request_endpoint(self.logger, request_details)
        if not isinstance(response, DataResponse):
            logger.warning(f'failed to retrieve metadata for {space_url_by_id(space_id)}')
            return None
        try:
            data = response.json()
        except json.JSONDecodeError as e:
            logger.warning(f'failed to parse space {space_url_by_id(space_id)}: {e}. Raw response: {response.text}')
            return None
        try:
            space = parse_space(data)
        except ValueError as e:
            logger.warning(f'failed to parse Space metadata for "{space_url_by_id(space_id)}": {e}')
            logger.debug(f'raw metadata: {data}')
            return None
        return space

    @staticmethod
    async def fetch_media_url(logger: logging.Logger, client: HttpClient,
                              entity: TwitterSpaceEntity, space: TwitterSpaceRecord) -> Optional[str]:
        """
        fetch and parse data from media_url endpoint for given space.media_key
        return the url on success, empty string on 404 response, None on any other error
        """
        logger.debug(f'fetch media url for space {space.url}')
        r = LiveStreamEndpoint.prepare(entity.url, client.cookie_jar, space.media_key)
        response = await client.request(r.url, params=r.params, headers=r.headers)
        if isinstance(response, NoResponse):
            logger.debug(f'failed to retrieve media url for {space.url}')
            return None
        elif not response.ok or  not response.has_content:
            if response.status == 404:
                logger.debug(f'no media url for {space.url}: {response.status} ({response.reason})')
                return ''
            else:
                logger.debug(
                    f'got code {response.status} ({response.reason}) while fetching media_url for {response.url}')
                return None
        else:
            text = response.text
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning(f'failed to decode json response for media_url: {e}. Raw text: "{text}"')
            return None
        media_url = parse_media_url(data)
        if media_url is None:
            logger.debug(f'failed to parse media url for {space.url}. Raw data: "{data}"')
            return None
        return media_url


def replace_url_filename(url: str, new_name: str, strip_query: bool = True) -> str:
    """Replace last element of url path with new_name"""
    parts = urllib.parse.urlparse(url)
    path_parts = parts.path.rsplit('/', 1)
    if not path_parts:
        raise ValueError(f'url has no path: {url}')
    path_parts[-1] = new_name
    new_path = '/'.join(path_parts)
    new_query = parts.query if not strip_query else ""
    updated_parts = parts._replace(path=new_path, query=new_query)
    return urllib.parse.urlunparse(updated_parts)


async def get_static_playlist_url(client: HttpClient, dynamic_playlist_url: Optional[str],
                                  logger: Optional[logging.Logger] = None) -> Optional[str]:
    """Fetch master playlist and infer latest static playlist url, return None if anything is wrong"""
    if dynamic_playlist_url is None:
        return None
    logger = logger or logging.getLogger('get_static_playlist_url')
    if re.findall(r'playlist_\d+\.m3u8', dynamic_playlist_url):
        logger.debug(f'already a static playlist url: {dynamic_playlist_url}')
        return dynamic_playlist_url
    try:
        master_playlist_url = replace_url_filename(dynamic_playlist_url, 'master_playlist.m3u8')
    except Exception as e:
        logger.warning(f'failed to update url "{dynamic_playlist_url}": {e}')
        return None
    master_playlist = await client.request_text(master_playlist_url)
    if master_playlist is None:
        return None
    try:
        static_path = master_playlist.strip().split('\n')[-1]
        static_path_name = static_path.strip().split('/')[-1]
    except Exception as e:
        logger.warning(f'failed to extract filename from playlist at "{master_playlist_url}": {e}')
        return None
    if not static_path_name.lower().endswith('m3u8'):
        logger.warning(f'unexpected playlist path: {static_path}')
        return None
    try:
        static_playlist_url = replace_url_filename(master_playlist_url, static_path_name)
    except Exception as e:
        logger.warning(f'failed to update url "{master_playlist}" with {static_path_name}: {e}')
        return None
    return static_playlist_url


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

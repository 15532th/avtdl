import asyncio
import datetime
import logging
from typing import Any, Callable, Dict, Optional, Sequence, Tuple, Union

from pydantic import Field, FilePath, PositiveInt, SerializeAsAny, field_validator

from avtdl.core.actions import TaskAction, TaskActionConfig, TaskActionEntity
from avtdl.core.cookies import load_cookies
from avtdl.core.db import BaseDbConfig, RecordDB
from avtdl.core.interfaces import Event, EventType, Record
from avtdl.core.plugins import Plugins
from avtdl.core.request import HttpClient, StateStorage
from avtdl.core.runtime import RuntimeContext, TaskStatus
from avtdl.core.utils import JSONType, utcnow
from avtdl.plugins.youtube.video_info import VideoInfo
from avtdl.plugins.youtube.youtube_feed import YoutubeVideoRecord
from avtdl.plugins.youtube.youtube_rss import YoutubeFeedRecord


@Plugins.register('youtube.live', Plugins.kind.ASSOCIATED_RECORD)
class YoutubeLiveErrorEvent(Event):
    """Produced on failure to process a livestream"""
    event_type: str = EventType.error
    """text describing the nature of event, can be used to filter classes of events, such as errors"""
    record: SerializeAsAny[Optional[Union[YoutubeFeedRecord, YoutubeVideoRecord]]] = Field(exclude=True)
    """record that was being processed when this event happened"""

    def __str__(self):
        return f'Processing stream {self.record.stream_id} failed: {self.text}\n[{self.record.name}] {self.record.title}\n{self.record.url}'


Plugins.register('youtube.live', Plugins.kind.ASSOCIATED_RECORD)(YoutubeFeedRecord)
Plugins.register('youtube.live', Plugins.kind.ASSOCIATED_RECORD)(YoutubeVideoRecord)


@Plugins.register('youtube.live', Plugins.kind.ASSOCIATED_RECORD)
class YoutubeVideoInfoRecord(VideoInfo, Record):
    """
    Youtube video or livestream metadata, retrieved from the video page
    """

    def __str__(self):
        last_line = ''
        scheduled = self.scheduled
        if scheduled:
            last_line = '\nscheduled to {}'.format(scheduled.strftime('%Y-%m-%d %H:%M'))
        elif self.is_live:
            last_line = '\n[Live]'
        if self.is_member_only:
            last_line += ' [Member only]'
        template = '{}\n{}\npublished by {}'
        return template.format(self.url, self.title, self.author) + last_line

    def __repr__(self):
        template = '{:<8} [{}] {}'
        return template.format(self.author or 'Unknown author', self.video_id, self.title[:60])

    def get_uid(self) -> str:
        return self.video_id

    def as_embed(self) -> dict:
        embed: Dict[str, Any] = {
            'title': self.title,
            # 'description': ,
            'url': self.url,
            'color': None,
            'author': {'name': self.author, 'url': self.channel_link, 'icon_url': self.avatar_url},
            'image': {'url': self.thumbnail_url},
            'fields': []
        }
        footer = ''
        if self.published_text:
            footer += self.published_text
        if self.length:
            embed['fields'].append({'name': f'[{self.length}]', 'value': '', 'inline': True})
        if self.scheduled is not None:
            scheduled = self.scheduled.strftime('%Y-%m-%d %H:%M')
            embed['fields'].append({'name': 'Scheduled:', 'value': scheduled, 'inline': True})
        if self.is_live:
            embed['fields'].append({'name': '[Live]', 'value': '', 'inline': True})
        if self.is_member_only:
            embed['fields'].append({'name': '[Member only]', 'value': '', 'inline': True})
        embed['footer'] = {'text': footer}
        return embed


@Plugins.register('youtube.live', Plugins.kind.ACTOR_CONFIG)
class YoutubeLiveConfig(TaskActionConfig, BaseDbConfig):
    pass


@Plugins.register('youtube.live', Plugins.kind.ACTOR_ENTITY)
class YoutubeLiveEntity(TaskActionEntity):
    cookies_file: FilePath
    """path to a text file containing cookies in Netscape format"""
    poll_interval: PositiveInt = 60
    """how often live status of the stream that should have started by now is updated, in seconds"""
    poll_attempts: PositiveInt = 30
    """how many times live status of the stream that should have started by now is updated before giving up"""

    @field_validator('cookies_file')
    @classmethod
    def check_cookies(cls, path: FilePath):
        try:
            load_cookies(path, raise_on_error=True)
        except Exception as e:
            raise ValueError(f'{e}') from e
        return path


@Plugins.register('youtube.live', Plugins.kind.ACTOR)
class YoutubeLive(TaskAction):
    """
    Wait for livestream on Youtube

    If incoming record comes from the "channel" or "rss" monitor and represents an ongoing or upcoming livestream,
    waits for the stream start and emits the record down the chain.
    Number and frequency of attempts is limited by `poll_attempts` and `poll_interval` settings.
    """

    def __init__(self, conf: YoutubeLiveConfig, entities: Sequence[YoutubeLiveEntity], ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)
        self.conf: YoutubeLiveConfig
        self.state_storage = StateStorage()
        self.db = RecordDB(conf.db_path, logger=self.logger.getChild('db'))

    async def request_json(self, url: str,
                           base_update_interval: float,
                           current_update_interval: float,
                           client: HttpClient, method='GET',
                           headers: Optional[Dict[str, str]] = None,
                           params: Optional[Any] = None,
                           data: Optional[Any] = None,
                           data_json: Optional[Any] = None) -> Tuple[Optional[JSONType], float]:
        '''Helper method to make http request to a json endpoint'''
        state = self.state_storage.get(url, method, params)
        response = await client.request(url, params, data, data_json, headers, method, state)
        update_interval = response.next_update_interval(base_update_interval, current_update_interval, True)
        if response.has_json():
            data = response.json()
        else:
            data = None
        return data, update_interval

    def parse_record(self, data: Optional[JSONType], parser: Callable) -> Optional[YoutubeRecord]:
        if data is None or not isinstance(data, dict):
            return None
        try:
            record = parser(data)
            return record
        except Exception as e:
            self.logger.exception(f'failed to parse stream record: {e}')
            self.logger.debug(f'raw record data: {data}')
            return None

    async def fetch_video_state(self,
                                client: HttpClient,
                                record: YoutubeRecord,
                                update_interval: float,
                                base_update_interval: float) -> Tuple[Optional[YoutubeRecord], float]:
        updated_record = None
        if record.schedule_id is not None:
            if record.scheduled is not None and record.scheduled > utcnow():
                self.logger.debug(f'stream {record.stream_id} is scheduled, using schedule_id')
                url = f'https://www.youtube.fun/api/schedules/{record.schedule_id}'
                maybe_record_data, update_interval = await self.request_json(url, base_update_interval,
                                                                             update_interval, client)
                updated_record = self.parse_record(maybe_record_data, parse_schedule_record)
        if record.schedule_id is None or updated_record is None:
            self.logger.debug(f'stream {record.stream_id} is not scheduled or schedule update failed, using stream_id')
            url = f'https://www.youtube.fun/api/streams/with-rooms?username={record.username}'
            maybe_data, update_interval = await self.request_json(url, base_update_interval, update_interval, client)
            maybe_record_data = find_stream_data(maybe_data, record.stream_id)
            updated_record = self.parse_record(maybe_record_data, parse_live_record)
            if updated_record is not None:
                if updated_record.schedule_id is not None or updated_record.scheduled is not None:
                    self.logger.warning(
                        f'record {updated_record} should be live but is scheduled: id={updated_record.schedule_id}, scheduled={updated_record.scheduled}')

        return updated_record, update_interval

    async def handle_record_task(self, logger: logging.Logger, client: HttpClient, entity: YoutubeLiveEntity,
                                 record: Record, info: TaskStatus) -> None:
        if not isinstance(record, YoutubeFeedRecord) and not isinstance(record, YoutubeVideoRecord):
            self.logger.debug(f'[{entity.name}] dropping record with unsupported type {type(record)}: {record!r}')
            return

        record: Union[YoutubeFeedRecord, YoutubeVideoRecord]

        # wait for the stream to go live, return if there is no point waiting anymore
        update_interval = float(entity.poll_interval)
        for attempt in range(entity.poll_attempts):
            await asyncio.sleep(update_interval)
            msg = f'stream {record.url} by {record.author}, attempt {attempt}: fetching live status'
            self.logger.debug(msg)
            updated_record, update_interval = await self.fetch_video_state(client, record, update_interval,
                                                                           entity.poll_interval)
            if updated_record is None:
                continue

            updated_record.origin = record.origin
            updated_record.chain = record.chain
            record = updated_record

            if record.end is not None:
                self.logger.debug(f'stream {record.stream_id} has ended at {record.end}: {record!r}')
                msg = 'stream has already ended'
                self.on_record(entity, YoutubeLiveErrorEvent(text=msg, record=record))
                return
            elif record.start is not None:
                self.logger.debug(f'stream {record.stream_id}, attempt {attempt}: {record.username} is live')
                break
            elif record.scheduled is None:
                self.logger.debug(
                    f'stream {record.stream_id} has neither start nor scheduled date, dropping record {record!r}')
                msg = 'stream has neither start nor scheduled date'
                self.on_record(entity, YoutubeLiveErrorEvent(text=msg, record=record))
                return
            else:
                self.logger.debug(f'stream {record.stream_id}, attempt {attempt}: scheduled {record.scheduled}')
                time_left = record.scheduled - datetime.datetime.now(datetime.timezone.utc)
                delay = time_left.total_seconds()
                if delay > 0:
                    msg = f'stream {record.stream_id} is scheduled to {record.scheduled}, waiting for {time_left}: {record!r}'
                    self.logger.debug(msg)
                    msg = f'waiting for stream to start in {time_left}'
                    info.set_status(msg)
                else:
                    delay = entity.poll_interval
                    msg = f'[{attempt}/{entity.poll_attempts}] waiting for stream to start, next check in {delay:.0f}'
                    info.set_status(msg, record)
                update_interval = delay

        else:
            self.logger.debug(
                f'stream {record.stream_id} is not live after {entity.poll_attempts} checks, dropping record {record!r}')
            msg = f"stream didn't go live after {entity.poll_interval * entity.poll_attempts} seconds"
            self.on_record(entity, YoutubeLiveErrorEvent(text=msg, record=record))
            return

        # stream is now live for sure, try fetching stream_url
        stream_url = await fetch_stream_url(client, self.logger, record.stream_id, auth)
        if stream_url is None:
            self.logger.warning(f'failed to fetch stream_url for stream {record.stream_id}')
            self.on_record(entity, YoutubeLiveErrorEvent(text='retrieving playlist url failed', record=record))
            return

        record.playlist_url = stream_url
        self.db.store_records([record], entity.name)
        self.on_record(entity, record)

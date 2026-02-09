import asyncio
import datetime
import logging
from typing import Any, Dict, Optional, Sequence, Tuple, TypeVar, Union

from pydantic import Field, FilePath, PositiveInt, SerializeAsAny, field_validator

from avtdl.core.actions import TaskAction, TaskActionConfig, TaskActionEntity
from avtdl.core.cookies import load_cookies
from avtdl.core.db import BaseDbConfig, RecordDB
from avtdl.core.formatters import Fmt
from avtdl.core.interfaces import Event, EventType, Record
from avtdl.core.plugins import Plugins
from avtdl.core.request import HttpClient, StateStorage
from avtdl.core.runtime import RuntimeContext, TaskStatus
from avtdl.plugins.youtube.video_info import VideoInfo, parse_video_page
from avtdl.plugins.youtube.youtube_feed import YoutubeVideoRecord
from avtdl.plugins.youtube.youtube_rss import YoutubeFeedRecord


@Plugins.register('youtube.live', Plugins.kind.ASSOCIATED_RECORD)
class YoutubeLiveErrorEvent(Event):
    """Produced on failure to process a livestream"""
    event_type: str = EventType.error
    """text describing the nature of event, can be used to filter classes of events, such as errors"""
    record: SerializeAsAny[Union[YoutubeVideoRecord, YoutubeFeedRecord, None]] = Field(exclude=True)
    """record that was being processed when this event happened"""

    def __str__(self):
        return f'Processing stream {self.record.stream_id} failed: {self.text}\n[{self.record.name}] {self.record.title}\n{self.record.url}'


Plugins.register('youtube.live', Plugins.kind.ASSOCIATED_RECORD)(YoutubeFeedRecord)
Plugins.register('youtube.live', Plugins.kind.ASSOCIATED_RECORD)(YoutubeVideoRecord)


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
            'author': {'name': self.author, 'url': self.channel_link},
            'image': {'url': self.thumbnail_url},
            'fields': []
        }
        footer = ''
        if self.published:
            embed['timestamp'] = self.published
        if self.duration:
            embed['fields'].append({'name': f'[{self.duration}]', 'value': '', 'inline': True})
        if self.scheduled is not None:
            scheduled = self.scheduled.strftime('%Y-%m-%d %H:%M')
            embed['fields'].append({'name': 'Scheduled:', 'value': scheduled, 'inline': True})
        if self.is_live:
            embed['fields'].append({'name': '[Live]', 'value': '', 'inline': True})
        embed['footer'] = {'text': footer}
        return embed


@Plugins.register('youtube.live', Plugins.kind.ACTOR_CONFIG)
class YoutubeLiveConfig(TaskActionConfig, BaseDbConfig):
    pass


@Plugins.register('youtube.live', Plugins.kind.ACTOR_ENTITY)
class YoutubeLiveEntity(TaskActionEntity):
    cookies_file: Optional[FilePath] = None
    """path to a text file containing cookies in Netscape format"""
    poll_interval: PositiveInt = 180
    """how often live status of the stream is updated after the scheduled time, in seconds"""
    poll_attempts: PositiveInt = 30
    """how many times live status of the stream that should have started by now is updated before giving up"""
    check_scheduled: bool = True
    """occasionally check live status before the scheduled time"""

    @field_validator('cookies_file')
    @classmethod
    def check_cookies(cls, path: FilePath):
        try:
            load_cookies(path, raise_on_error=True)
        except Exception as e:
            raise ValueError(f'{e}') from e
        return path


RecordType = TypeVar('RecordType', bound=Record)


def merge_models(base: RecordType, overlay: YoutubeVideoInfoRecord) -> RecordType:
    common = set(base.model_fields) & set(overlay.model_fields)
    overlay_data = overlay.model_dump(include=common, exclude_unset=True)
    merged = base.model_dump()
    merged.update(overlay_data)
    return base.model_validate(merged)


@Plugins.register('youtube.live', Plugins.kind.ACTOR)
class YoutubeLive(TaskAction):
    """
    Wait for livestream on Youtube

    If incoming record comes from the "channel" or "rss" monitor and represents an ongoing or upcoming livestream,
    waits for the stream start and emits the record down the chain. Records representing ended streams and VODs are
    emitted immediately.

    Number and frequency of attempts is limited by `poll_attempts` and `poll_interval` settings.
    """

    def __init__(self, conf: YoutubeLiveConfig, entities: Sequence[YoutubeLiveEntity], ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)
        self.conf: YoutubeLiveConfig
        self.state_storage = StateStorage()
        self.db = RecordDB(conf.db_path, logger=self.logger.getChild('db'))

    async def request(self, url: str,
                      base_update_interval: float,
                      current_update_interval: float,
                      client: HttpClient, method='GET',
                      headers: Optional[Dict[str, str]] = None,
                      params: Optional[Any] = None,
                      data: Optional[Any] = None,
                      data_json: Optional[Any] = None) -> Tuple[Optional[str], float]:
        '''Helper method to make http request to a json endpoint'''
        state = self.state_storage.get(url, method, params)
        response = await client.request(url, params, data, data_json, headers, method, state)
        update_interval = response.next_update_interval(base_update_interval, current_update_interval, True)
        if response.has_content:
            data = response.text
        else:
            data = None
        return data, update_interval

    async def fetch_video_state(self,
                                client: HttpClient,
                                video_url: str,
                                update_interval: float,
                                base_update_interval: float) -> Tuple[Optional[YoutubeVideoInfoRecord], float]:
        page, update_interval = await self.request(video_url, base_update_interval, update_interval, client)
        info = parse_video_page(page, video_url) if page is not None else None
        updated_record = YoutubeVideoInfoRecord(**info.model_dump()) if info is not None else None
        return updated_record, update_interval

    async def handle_record_task(self, logger: logging.Logger, client: HttpClient, entity: YoutubeLiveEntity,
                                 base_record: Record, info: TaskStatus) -> None:
        if not isinstance(base_record, YoutubeFeedRecord) and not isinstance(base_record, YoutubeVideoRecord):
            self.logger.debug(
                f'[{entity.name}] dropping record with unsupported type {type(base_record)}: {base_record!r}')
            return

        # wait for the stream to go live, return if there is no point waiting anymore
        update_interval = float(entity.poll_interval)
        for attempt in range(entity.poll_attempts):
            msg = f'waiting for {Fmt.duration(int(update_interval))} before fetching live status'
            info.set_status(msg, base_record)
            await asyncio.sleep(update_interval)
            msg = f'stream {base_record.url} by {base_record.author}, attempt {attempt}: fetching live status'
            self.logger.debug(msg)
            info.set_status(msg, base_record)
            record, update_interval = await self.fetch_video_state(client, base_record.url, update_interval,
                                                                   entity.poll_interval)
            if record is None:
                continue

            record.origin = base_record.origin
            record.chain = base_record.chain

            try:
                if self.db.record_has_changed(record, entity.name, {'views'}):
                    self.db.store_records([record], entity.name)
            except Exception as e:
                continue

            if record.scheduled is not None:
                self.logger.debug(f'stream {record.video_id}, attempt {attempt}: scheduled {record.scheduled}')
                time_left = record.scheduled - datetime.datetime.now(datetime.timezone.utc)
                delay = time_left.total_seconds()
                if delay > 0:
                    if entity.check_scheduled:
                        intervals = [7 * 24 * 3600, 24 * 3600, 4 * 3600, 1 * 3600, 10 * 60]
                        delay = max((i for i in intervals if delay > i), default=delay)
                    delay_dt = datetime.timedelta(seconds=delay)
                    msg = f'stream {record.video_id} is scheduled to {record.scheduled}, waiting for {delay_dt}: {record!r}'
                    self.logger.debug(msg)
                    msg = f'waiting for stream to start in {time_left}, next check after {delay_dt}'
                    info.set_status(msg)
                else:
                    delay = entity.poll_interval
                    msg = f'[{attempt}/{entity.poll_attempts}] waiting for stream to start, next check in {delay:.0f}'
                    info.set_status(msg, record)
                await asyncio.sleep(delay)
                continue
            elif record.is_live:
                self.logger.debug(f'stream {record.video_id}, attempt {attempt}: {record.author} is live')
                info.set_status(f'stream is live', record)
                break
            elif record.playability_status == 'OK':
                msg = f'{record.video_id} is a playable video, done'
                self.logger.debug(msg)
                info.set_status(msg, record)
                break
            elif record.playability_reason is not None:
                status = f'{record.playability_status}: {record.playability_reason}'
                self.logger.debug(f'[{record.video_id}] playability status {status}')
                msg = f'playability status {status}'
                info.set_status(msg, record)
                self.on_record(entity, YoutubeLiveErrorEvent(text=msg, record=base_record))
                return
            else:
                msg = f'dropping record with unknown state: {record!r}'
                self.logger.warning(msg)
                info.set_status(msg, record)
                return
        else:
            self.logger.debug(
                f'stream {base_record.video_id} is not live after {entity.poll_attempts} checks, dropping record {base_record!r}')
            msg = f"stream didn't go live after {entity.poll_interval * entity.poll_attempts} seconds"
            self.on_record(entity, YoutubeLiveErrorEvent(text=msg, record=base_record))
            return

        # stream is now live for sure, emit the record
        base_record = merge_models(base_record, record)
        self.db.store_records([base_record], entity.name)
        self.on_record(entity, base_record)

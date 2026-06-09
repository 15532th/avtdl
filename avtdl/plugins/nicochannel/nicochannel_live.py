import asyncio
import base64
import binascii
import datetime
import logging
import re
from typing import Optional, Sequence, Tuple, Union

from pydantic import Field, FilePath, HttpUrl, PositiveInt, SerializeAsAny, ValidationError, field_validator

from avtdl.core.actions import TaskAction, TaskActionConfig, TaskActionEntity
from avtdl.core.cookies import load_cookies
from avtdl.core.db import BaseDbConfig, RecordDB
from avtdl.core.formatters import Fmt
from avtdl.core.interfaces import Event, EventType, Record
from avtdl.core.plugins import Plugins
from avtdl.core.request import HttpClient, RequestDetails
from avtdl.core.runtime import RuntimeContext, TaskStatus
from avtdl.core.utils import JSONType, find_one
from avtdl.plugins.nicochannel.nicochannel import NicochannelUrl, NicochannelVideoRecord, parse_video_page


@Plugins.register('nicochannel.live', Plugins.kind.ASSOCIATED_RECORD)
class NicochannelLiveErrorEvent(Event):
    """Produced on failure to process a livestream"""
    event_type: str = EventType.error
    """text describing the nature of event, can be used to filter classes of events, such as errors"""
    record: SerializeAsAny[Union[NicochannelVideoRecord, None]] = Field(exclude=True)
    """record that was being processed when this event happened"""

    def __str__(self):
        msg = f'Processing stream failed, {self.text}'
        if self.record is not None:
            msg += f'\n[{self.record.author}] {self.record.title}\n{self.record.url}'
        return msg


Plugins.register('nicochannel.live', Plugins.kind.ASSOCIATED_RECORD)(NicochannelVideoRecord)


class NicochannelLiveRecord(NicochannelVideoRecord):
    """
    Nicochannel livestream with additional metadata
    """
    playlist_url: Optional[str] = None
    """HLS playlist url"""
    key: Optional[str] = None
    """decryption key, used to decrypt media chunks in playlist (as a string of hexadecimal values)"""
    key_b64: Optional[str] = None
    """decryption key (as a base64 string)"""



@Plugins.register('nicochannel.live', Plugins.kind.ACTOR_CONFIG)
class NicochannelLiveConfig(TaskActionConfig, BaseDbConfig):
    pass


@Plugins.register('nicochannel.live', Plugins.kind.ACTOR_ENTITY)
class NicochannelLiveEntity(TaskActionEntity):
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


@Plugins.register('nicochannel.live', Plugins.kind.ACTOR)
class NicochannelLive(TaskAction):
    """
    Wait for livestream on Nicochannel

    Checks if incoming record comes from the "nicochannel" monitor and represents an ongoing or upcoming livestream,
    waits for the stream start and emits the record down the chain. Records for ended streams and VODs are
    emitted immediately.

    Number and frequency of attempts is limited by `poll_attempts` and `poll_interval` settings.
    """

    def __init__(self, conf: NicochannelLiveConfig, entities: Sequence[NicochannelLiveEntity], ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)
        self.conf: NicochannelLiveConfig
        self.db = RecordDB(conf.db_path, logger=self.logger.getChild('db'))

    @classmethod
    async def request_endpoint(cls, client: HttpClient,
                               logger: logging.Logger,
                               r: RequestDetails,
                               base_update_interval: float,
                               current_update_interval: float) -> Tuple[Optional[JSONType], float]:
        '''Helper method to make http request to a json endpoint'''
        response = await client.request_endpoint(logger, r)
        update_interval = response.next_update_interval(base_update_interval, current_update_interval, True)
        if response.has_json():
            data = response.json()
        else:
            data = None
        return data, update_interval

    async def fetch_video_state(self,
                                client: HttpClient,
                                record: NicochannelVideoRecord,
                                update_interval: float,
                                base_update_interval: float,
                                logger: logging.Logger) -> Tuple[Optional[NicochannelLiveRecord], float]:
        assert record.fanclub_url is not None, 'caller must ensure fanclub_url is present. This is a bug'
        endpoint = await NicochannelUrl.construct(record.fanclub_url, client, logger)
        if endpoint is None:
            return None, update_interval
        r = endpoint.video_info(record.video_id)
        data, update_interval = await self.request_endpoint(client, logger, r, base_update_interval, update_interval)
        if data is None:
            return None, update_interval
        video_info = parse_video_page(data)
        updated_record = NicochannelLiveRecord(**{**record.model_dump(), **video_info})
        return updated_record, update_interval

    async def handle_record_task(self, logger: logging.Logger, client: HttpClient, entity: NicochannelLiveEntity,
                                 base_record: Record, info: TaskStatus) -> None:
        if not isinstance(base_record, NicochannelVideoRecord):
            self.logger.debug(
                f'dropping record with unsupported type {type(base_record)}: {base_record!r}')
            return
        assert isinstance(base_record, NicochannelVideoRecord)
        if base_record.fanclub_url is None:
            logger.warning(f'dropping record with missing fanclub_url: {base_record}\n. This is a bug.')
            return None
        # wait for the stream to go live, return if there is no point waiting anymore
        update_interval = float(entity.poll_interval)
        for attempt in range(entity.poll_attempts):
            msg = f'waiting for {Fmt.duration(int(update_interval))} before fetching live status'
            info.set_status(msg, base_record)
            await asyncio.sleep(update_interval)
            msg = f'stream {base_record.url} by {base_record.author or "unknown author"}, attempt {attempt}: fetching live status'
            self.logger.debug(msg)
            info.set_status(msg, base_record)
            record, update_interval = await self.fetch_video_state(client, base_record, update_interval,
                                                                   entity.poll_interval, logger)
            if record is None:
                continue

            record.origin = base_record.origin
            record.chain = base_record.chain

            if self.db.record_has_changed(record, entity.name, set()):
                self.db.store_records([record], entity.name)

            if record.is_upcoming:
                self.logger.debug(f'stream {record.video_id}, attempt {attempt}: scheduled {record.scheduled}')
                assert record.scheduled is not None, f'record is upcoming but not scheduled: {record}'
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
            else:
                msg = f'{record.video_id} is not an upcoming live, done'
                self.logger.debug(msg)
                info.set_status(msg, record)
                break
        else:
            self.logger.debug(
                f'stream {base_record.video_id} is not live after {entity.poll_attempts} checks, dropping record {base_record!r}')
            msg = f"stream didn't go live after {entity.poll_interval * entity.poll_attempts} seconds"
            self.on_record(entity, NicochannelLiveErrorEvent(text=msg, record=base_record))
            return

        # stream is now live for sure, emit the record
        self.db.store_records([record], entity.name)
        self.on_record(entity, record)


async def retrieve_playlist_url(client: HttpClient, record: NicochannelLiveRecord, logger: logging.Logger):
    """Try retrieving and updating "playlist_url" and "key" values of the given record"""
    if record.fanclub_url is None:
        return
    endpoint = await NicochannelUrl.construct(record.fanclub_url, client, logger)
    if endpoint is None:
        return

    r = endpoint.session_id(record.video_id)
    session_ids = await client.request_json_endpoint(logger, r)
    if session_ids is None:
        return
    session_id = find_one(session_ids, 'data.session_id')
    if not isinstance(session_id, str):
        return

    r = endpoint.video_info(record.video_id)
    video_info = await client.request_json_endpoint(logger, r)
    if video_info is None:
        return
    auth_url = find_one(video_info, 'data.video_page.video_stream.authenticated_url')
    if not isinstance(auth_url, str):
        return
    if not auth_url.endswith('{session_id}'):
        return

    playlist_url = auth_url.replace('{session_id}', session_id)
    record.playlist_url = playlist_url

    master_playlist = await client.request_text(playlist_url)
    if master_playlist is None:
        return
    playlists = [url for url in master_playlist.splitlines() if not url.startswith('#')]
    if not playlists:
        return
    playlist = playlists[0]
    try:
        HttpUrl(playlist)
    except ValidationError:
        return
    playlist_content = await client.request_text(playlist)
    if playlist_content is None:
        return
    key_urls = re.findall(r'#EXT-X-KEY:METHOD=AES-128,URI="[^"]+', playlist_content)
    if not key_urls:
        return
    key_url = key_urls[0]
    try:
        HttpUrl(key_url)
    except ValidationError:
        return
    key_b64 = await client.request_text(key_url, headers=endpoint.origin_headers) # needs browser user-agent
    if key_b64 is None:
        return
    record.key_b64 = key_b64
    try:
        key_raw = base64.b64decode(key_b64)
    except binascii.Error:
        return
    record.key = key_raw.hex()

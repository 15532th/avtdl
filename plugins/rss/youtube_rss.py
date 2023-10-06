from datetime import datetime, timezone
import logging
import sqlite3
from typing import Optional, Union
from typing import Sequence, Any

import aiohttp
import feedparser
import pydantic
from pydantic import ConfigDict, ValidationError

from core import utils
from core.config import Plugins
from core.interfaces import ActorConfig, LivestreamRecord
from core.monitors import HttpTaskMonitorEntity, HttpTaskMonitor
from core.utils import get_cache_ttl, make_datetime
from plugins.rss import video_info


class YoutubeFeedRecord(LivestreamRecord):
    model_config = ConfigDict(extra='allow')

    url: str
    title: str
    published: datetime
    updated: datetime
    author: str
    video_id: str
    summary: str
    views: Optional[int]
    scheduled: Optional[datetime] = None

    async def check_scheduled(self, session: Optional[aiohttp.ClientSession] = None):
        if self.views == 0:
            try:
                info = await video_info.aget_video_info(self.url, session)
                scheduled = info.scheduled
            except Exception:
                logging.exception('Exception while trying to get "scheduled" field, skipping')
                scheduled = None
        else:
            scheduled = None
        self.scheduled = scheduled

    def __str__(self):
        return self.format_record()

    def __repr__(self):
        template = '{} {:<8} [{}] {}'
        return template.format(self.format_date(self.published), self.author, self.video_id, self.title[:60])

    @staticmethod
    def format_date(date: Union[str, datetime]) -> str:
        if isinstance(date, str):
            date = datetime.fromisoformat(date)
        return date.strftime('%Y-%m-%d %H:%M')

    def format_record(self):
        scheduled = self.scheduled
        if scheduled:
            scheduled_time = '\nscheduled to {}'.format(self.format_date(scheduled))
        else:
            scheduled_time = ''
        template = '{}\n{}\npublished by {} at {}'
        return template.format(self.url, self.title, self.author, self.format_date(self.published)) + scheduled_time

    def as_dict(self, additional_fields):
        record_dict = {}
        record_dict.update(self.__dict__)
        record_dict.update(additional_fields)
        return record_dict

@Plugins.register('rss', Plugins.kind.ACTOR_ENTITY)
class FeedMonitorEntity(HttpTaskMonitorEntity):
    name: str
    url: str
    update_interval: int = 900
    adjust_update_interval: bool = True
    base_update_interval: pydantic.PrivateAttr = None

    def model_post_init(self, __context: Any) -> None:
        self.base_update_interval = self.update_interval

@Plugins.register('rss', Plugins.kind.ACTOR_CONFIG)
class FeedMonitorConfig(ActorConfig):
    db_path: str = ':memory:'

@Plugins.register('rss', Plugins.kind.ACTOR)
class FeedMonitor(HttpTaskMonitor):

    def __init__(self, conf: FeedMonitorConfig, entities: Sequence[FeedMonitorEntity]):
        super().__init__(conf, entities)
        self.feedparser = RSS2MSG(conf.db_path, self.logger)

    async def run(self):
        async with aiohttp.ClientSession() as session:
            for entity in self.entities.values():
                await self.feedparser.prime_db(entity, session)
        await super().run()

    async def get_new_records(self, entity: FeedMonitorEntity, session: aiohttp.ClientSession) -> Sequence[YoutubeFeedRecord]:
        return await self.feedparser.get_records(entity, session)


class RSS2MSG:

    def __init__(self, db_path=':memory:', logger=None):
        '''entries parsed from `feed_links` in `feeds` will be put in table `records`'''
        self.logger = logger or logging.getLogger('rss2msg')
        try:
            self.db = RecordDB(db_path)
        except sqlite3.OperationalError as e:
            self.logger.error(f'error opening sqlite database at path "{db_path}", specified in "db_path" config variable: {e}. If file exists make sure it was produced by this application, otherwise check if new file can be created at specified location. Alternatively use special value ":memory:" to use in-memory database instead.')
            raise
        else:
            self.logger.debug(f'successfully connected to sqlite database at "{db_path}"')

    async def prime_db(self, entity: FeedMonitorEntity, session: aiohttp.ClientSession) -> None:
        '''if feed has no prior records fetch it once and mark all entries as old
        in order to not produce ten messages at once when feed first added'''
        if self.db.get_size(entity.name) == 0:
            await self.get_records(entity, session)

    async def get_feed(self, entity: FeedMonitorEntity, session: aiohttp.ClientSession):
        async with session.get(entity.url) as response:
            text = await response.text()
        if response.status != 200:
            self.logger.warning(f'got code {response.status} while fetching {entity.url}')
            if response.status >= 400:
                update_interval = min(entity.update_interval * 2, entity.base_update_interval * 10)
                if entity.update_interval != update_interval:
                    entity.update_interval = update_interval
                    self.logger.warning(f'update interval set to {entity.update_interval} seconds for {entity.name} ({entity.url})')
                return None
        if entity.adjust_update_interval:
            update_interval = get_cache_ttl(response.headers) or entity.base_update_interval
            entity.update_interval = max(update_interval, entity.base_update_interval)
            self.logger.debug(f'Youtube RSS for {entity.name}: next update in {entity.update_interval}')
        else:
            # restore update interval after backoff on failure
            if entity.update_interval != entity.base_update_interval:
                self.logger.info(f'restoring update interval {entity.update_interval} seconds for {entity.name} ({entity.url})')
                entity.update_interval = entity.base_update_interval
        try:
            feed = feedparser.parse(text, response_headers=response.headers)
            if feed.get('entries') is not None:
                return feed
            else:
                from pprint import pformat
                self.logger.debug(f'feed for {entity.url} has no entries, probably broken:')
                self.logger.debug(pformat(feed))
                raise Exception(f'got broken feed while fetching {entity.url}')
        except Exception as e:
            self.logger.warning('Exception while updating rss feed: {}'.format(e))
            return None


    @staticmethod
    def parse_entry(entry: Union[dict, feedparser.FeedParserDict]) -> YoutubeFeedRecord:
        parsed = {}
        parsed['url'] = entry['link']
        parsed['title'] = entry['title']
        parsed['updated'] = make_datetime(entry['updated_parsed'])

        parsed['published'] = make_datetime(entry['published_parsed'])
        parsed['author'] = entry.get('author')
        parsed['summary'] = entry.get('summary')
        parsed['video_id'] = entry.get('yt_videoid', 'video_id missing')
        try:
            views = int(entry['media_statistics']['views'])
        except (ValueError, KeyError, TypeError):
            views = None
        parsed['views'] = views
        record = YoutubeFeedRecord(**parsed)
        return record

    def parse_entries(self, feed) -> Sequence[YoutubeFeedRecord]:
        records = []
        for entry in feed['entries']:
            try:
                record = self.parse_entry(entry)
            except KeyError as e:
                self.logger.warning(f'Youtube rss parser failed to construct record from rss entry {entry}: missing  necessarily field {e}')
                continue
            except ValidationError as e:
                self.logger.warning(f'Youtube rss parser failed to construct record from rss entry {entry}: {e}')
                continue
            records.append(record)
        return records

    def get_latest_record(self, video_id) -> Optional[YoutubeFeedRecord]:
        raw_latest_row = self.db.fetch_row(video_id)
        if raw_latest_row is not None:
            latest_row = dict(raw_latest_row)
            latest_row['url'] = latest_row.pop('link')
            for field in ['published', 'updated', 'scheduled']:
                value = latest_row[field]
                # handle db records with old format
                if isinstance(value, str):
                    latest_row[field] = datetime.fromisoformat(value)

            return YoutubeFeedRecord(**latest_row)
        else:
            return None

    async def get_records(self, entity: FeedMonitorEntity, session: aiohttp.ClientSession) -> Sequence[YoutubeFeedRecord]:
        feed = await self.get_feed(entity, session)
        if feed is None:
            return []
        records = self.parse_entries(feed)
        new_records = []
        for record in records:
            if not self.db.row_exists(record.video_id):
                # only first record for given video_id is send to actions
                await record.check_scheduled(session)
                new_records.append(record)
                self.logger.info(f'{record!r}')
            if not self.db.row_exists(record.video_id, record.updated):
                # every new record for given video_id will be stored in db
                previous = self.get_latest_record(record.video_id)
                if previous is not None and previous.scheduled is not None:
                    self.logger.debug(f'{record.video_id=} has last {previous.scheduled=}, updating')
                    await record.check_scheduled(session)
                    if record.scheduled is not None:
                        if record.scheduled < previous.scheduled:
                            msg = 'In feed {} record [{}] {} rescheduled back from {} to {}'
                            msg = msg.format(entity.name, record.video_id, record.title, previous.scheduled, record.scheduled)
                            self.logger.warning(msg)
                            # treat rescheduled records as new if scheduled time is earlier than before
                            # to allow action run on time, though it will run second time later
                            new_records.append(record)
                now = datetime.now(tz=timezone.utc).isoformat(timespec='seconds')
                additional_fields = {'feed_name': entity.name, 'parsed_at': now}
                row = record.as_dict(additional_fields)
                self.db.store(row)

        return new_records

class RecordDB(utils.RecordDB):
    table_structure = 'parsed_at datetime, feed_name text, author text, video_id text, link text, title text, summary text, published datetime, updated datetime, scheduled datetime DEFAULT NULL, views integer, PRIMARY KEY(video_id, updated)'
    row_structure = ':parsed_at, :feed_name, :author, :video_id, :url, :title, :summary, :published, :updated, :scheduled, :views'
    id_field = 'video_id'
    exact_id_field = 'updated'
    group_id_field = 'feed_name'
    sorting_field = 'parsed_at'

    def store(self, row: dict) -> None:
        return super().store(row)

    def row_exists(self, video_id: str, updated: Optional[datetime] = None) -> bool:
        return super().row_exists(video_id, updated)

    def fetch_row(self, video_id: str, updated: Optional[datetime] = None) -> Optional[sqlite3.Row]:
        return super().fetch_row(video_id, updated)

    def get_size(self, feed_name: Optional[str] = None) -> int:
        '''return number of records, total or for specified feed, are stored in db'''
        return super().get_size(feed_name)

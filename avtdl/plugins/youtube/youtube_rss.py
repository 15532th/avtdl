import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Union

import aiohttp
import feedparser
from pydantic import ConfigDict

from avtdl.core import utils
from avtdl.core.config import Plugins
from avtdl.core.interfaces import Record
from avtdl.plugins.rss.generic_rss import GenericRSSMonitor, GenericRSSMonitorConfig, GenericRSSMonitorEntity
from avtdl.plugins.youtube.common import thumbnail_url
from avtdl.plugins.youtube.video_info import VideoInfoError, parse_video_page


@Plugins.register('rss', Plugins.kind.ASSOCIATED_RECORD)
class YoutubeFeedRecord(Record):
    """
    Youtube video or livestream parsed from a channel's RSS feed
    """
    model_config = ConfigDict(extra='allow')

    url: str
    """link to the video"""
    title: str
    """title of the video at the time of parsing"""
    published: datetime
    """published value of the feed item, usually the time when the video was uploaded or the livestream frame was set up"""
    updated: datetime
    """updated value of the feed item. If different from `published`, might indicate either a change to video title, thumbnail or description, or a change in video status, for example livestream ending"""
    thumbnail_url: Optional[str] = None
    """link to the video thumbnail"""
    author: str
    """author's name, as shown on the channel icon"""
    video_id: str
    """short string identifying the video on Youtube. Part of the video url"""
    summary: Optional[str] = None
    """video's description"""
    views: Optional[int]
    """current number of views. Is zero for upcoming and ongoing livestreams"""
    scheduled: Optional[datetime] = None
    """scheduled time for an upcoming livestream to start at, otherwise absent"""

    async def check_scheduled(self, session: Optional[aiohttp.ClientSession] = None, logger: Optional[logging.Logger] = None):
        scheduled = None
        if self.views == 0:
            logger = logger or logging.getLogger().getChild('check_scheduled')
            try:
                page = await utils.request(self.url, session, logger)
                if page is None:
                    raise VideoInfoError('failed to fetch video page')
                info = parse_video_page(page, self.url)
                scheduled = info.scheduled
            except VideoInfoError as e:
                logger.warning(f'Error while trying to check scheduled date of {self.url}, skipping')
                logger.warning(f'{e}')
            except Exception:
                logger.exception(f'Error while trying to check scheduled date of {self.url}, skipping')
        self.scheduled = scheduled

    def __str__(self):
        return self.format_record()

    def __repr__(self):
        template = '{} {:<8} [{}] {}'
        return template.format(self.format_date(self.published), self.author, self.video_id, self.title[:60])

    def get_uid(self) -> str:
        return self.video_id

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

    def discord_embed(self) -> dict:
        embed: Dict[str, Any] = {
            'title': self.title,
            # 'description': ,
            'url': self.url,
            'color': None,
            'author': {'name': self.author},
            'timestamp': self.format_date(self.published),
            'image': {'url': thumbnail_url(self.video_id)}
        }
        if self.scheduled is not None:
            scheduled = self.scheduled.strftime('%Y-%m-%d %H:%M')
            embed['fields'] = [{'name': 'Scheduled:', 'value': scheduled, 'inline': True}]
        return embed


@Plugins.register('rss', Plugins.kind.ACTOR_ENTITY)
class FeedMonitorEntity(GenericRSSMonitorEntity):
    update_interval: float = 900
    """How often the feed should be updated, in seconds"""
    track_reschedule: bool = False
    """keep track of scheduled time of upcoming streams, emit record again if it is changed to an earlier date"""


@Plugins.register('rss', Plugins.kind.ACTOR_CONFIG)
class FeedMonitorConfig(GenericRSSMonitorConfig):
    pass


@Plugins.register('rss', Plugins.kind.ACTOR)
class FeedMonitor(GenericRSSMonitor):
    """
    Youtube channel RSS feed monitor

    Monitors channel for new uploads and livestreams using RSS feed
    generated by Youtube. Requires old channel id format. In order to obtain
    RSS feed url for a given channel, use "View Source" on a channel page
    and search for "rss".

    Example of a supported url:

    - `https://www.youtube.com/feeds/videos.xml?channel_id=UCK0V3b23uJyU4N8eR_BR0QA`

    RSS feed is smaller and faster to parse compared to an HTML channel page,
    but by design only shows updates of a single channel and doesn't support
    authentication and therefore unable to show member-only streams.

    Scheduled date for upcoming streams is not present in the feed itself, so it
    is obtained by fetching and parsing video page the first time it appears in
    the feed. It then gets updated until stream goes live, unless `track_reschedule`
    option is disabled.

    No matter how often the url gets fetched, content of the feed only gets
    changed once every 15 minutes, so setting `update_interval` lower than that
    value is not recommended. This monitor will attempt to calculate time of the
    next update from HTTP headers and schedule next request right after it. Use
    `adjust_update_interval` to disable this behavior.
    """

    def __init__(self, conf: FeedMonitorConfig, entities: Sequence[FeedMonitorEntity]):
        try:
            Migration(conf.db_path)
        except Exception as e:
            raise Exception('migration failed') from e
        super().__init__(conf, entities)

    async def get_records(self, entity: FeedMonitorEntity, session: aiohttp.ClientSession) -> Sequence[YoutubeFeedRecord]:
        records = await super().get_records(entity, session)
        return records

    async def get_new_records(self, entity: FeedMonitorEntity, session: aiohttp.ClientSession) -> Sequence[YoutubeFeedRecord]:
        records = await self.get_records(entity, session)
        rescheduled_records: List[YoutubeFeedRecord] = []

        # record.check_scheduled() involves loading video page, so it should only be done when necessarily
        # and here seems to be the only good place for it, since network request requires "session" object
        for record in records:
            previous = self.load_record(record, entity)
            if previous is None:
                await record.check_scheduled(session, self.logger)
                continue
            if not entity.track_reschedule:
                continue
            if not isinstance(previous, YoutubeFeedRecord):
                self.logger.warning(f'[{entity.name}] previous version of record "{record!r}" is not YoutubeFeedRecord: {previous.model_dump()}')
                continue
            if previous is not None and previous.scheduled is not None:
                self.logger.debug(f'{record.video_id=} has last {previous.scheduled=}, updating')
                await record.check_scheduled(session, self.logger)
                if record.scheduled is None:
                    continue
                if record.scheduled < previous.scheduled:
                    # treat rescheduled records as new if scheduled time is earlier than before
                    # to allow action run on time, though it will run second time later
                    msg = 'In feed {} record [{}] {} rescheduled back from {} to {}'
                    msg = msg.format(entity.name, record.video_id, record.title, previous.scheduled, record.scheduled)
                    self.logger.warning(msg)
                    rescheduled_records.append(record)

        new_records = self.filter_new_records(records, entity)
        rescheduled_records.extend(new_records)
        return rescheduled_records

    def record_got_updated(self, record: YoutubeFeedRecord, entity: FeedMonitorEntity) -> bool:
        excluded_fields = {'views'}
        return self.db.record_has_changed(record, entity.name, excluded_fields)

    def _parse_entry(self, entry: feedparser.FeedParserDict) -> YoutubeFeedRecord:
        parsed: Dict[str, Any] = {}
        parsed['url'] = entry['link']
        parsed['title'] = entry['title']
        parsed['updated'] = utils.make_datetime(entry['updated_parsed'])

        parsed['published'] = utils.make_datetime(entry['published_parsed'])
        parsed['author'] = entry.get('author')
        parsed['summary'] = entry.get('summary')
        video_id = entry.get('yt_videoid')
        if video_id is not None:
            parsed['video_id'] = video_id
            parsed['thumbnail_url'] = thumbnail_url(video_id)
        else:
            parsed['video_id'] = None
            parsed['thumbnail_url'] = None
            self.logger.warning(f'[{entry.name}] got entry without video_id: {entry}')
        """link to the video thumbnail"""
        try:
            views = int(entry['media_statistics']['views'])
        except (ValueError, KeyError, TypeError):
            views = None
        parsed['views'] = views
        record = YoutubeFeedRecord(**parsed)
        return record


class Migration:
    """Convert db produced by RSS2JBR in format used by RecordDB"""

    name: str = 'rss2jbr'
    table_name = 'records'
    target_table_structure = 'parsed_at datetime, feed_name text, uid text, hashsum text, class_name text, as_json text, PRIMARY KEY(uid, hashsum)'
    target_row_structure = ':parsed_at, :feed_name, :uid, :hashsum, :class_name, :as_json'

    migrated_table_schema = '''CREATE TABLE "records" ( "parsed_at" datetime, "feed_name" text, "author" text, "video_id" text, "link" text, "title" text, "summary" text, "published" datetime, "updated" datetime, "scheduled" datetime DEFAULT NULL, "views" intefer, PRIMARY KEY("video_id","updated"))'''

    def __init__(self, db_path: Union[str, Path]):
        self.logger = logging.getLogger('migration').getChild(self.name)
        dt = datetime.now(tz=timezone.utc).strftime('%Y_%m_%d_%H_%M_%S_%f')
        self.backup_table_name = f'migration_{self.table_name}_{dt}'
        if db_path == ':memory:':
            return
        if not Path(db_path).exists():
            return
        try:
            self.db = sqlite3.connect(db_path, isolation_level=None)
            self.db.row_factory = sqlite3.Row
        except sqlite3.OperationalError as e:
            self.logger.error(f'error opening sqlite database at path "{db_path}": {e}')
            raise
        else:
            self.logger.debug(f'successfully connected to sqlite database at "{db_path}"')

        with self.db:
            self.db.execute('BEGIN TRANSACTION')

            if not self.is_valid_target(self.table_name):
                self.logger.info(f'no migration is applicable for table "{self.table_name}" at "{db_path}"')
                return

            self.logger.info(f'running migration for table "{self.table_name}" at "{db_path}"')
            self.migrate()
            self.logger.info(f'migration for table "{self.table_name}" in file "{db_path}" is completed. '
                             f'Original data are preserved at table "{self.backup_table_name}"')

    def migrate(self):
        self.rename_table(self.table_name, self.backup_table_name)
        self.init_target_table(self.table_name)
        max_bad_records = 5
        for row in self.fetch_all_rows(self.backup_table_name):
            try:
                converted_row = self.transform(row)
            except Exception as e:
                self.logger.warning(f'failed to transform a row: {e}. Skipping failed record. Raw row: "{dict(row)}"')
                max_bad_records -= 1
                if max_bad_records <= 0:
                    raise Exception('exceeded limit for bad records, aborting migration') from e
                continue
            self.store(converted_row)

    def get_table_schema(self, table_name) -> Optional[str]:
        sql = 'SELECT sql FROM sqlite_schema WHERE name = :table_name'
        keys = {'table_name': table_name}
        cursor = self.db.execute(sql, keys)
        row = cursor.fetchone()
        if row is None:
            return None
        return row['sql']
    
    @staticmethod
    def _normalize_schema(schema: str) -> str:
        schema = schema.lower()
        schema = re.sub('[\s"\'`]', '', schema) 
        schema = schema.replace('intefer', 'integer')
        return schema

    def is_valid_target(self, table_name: str) -> bool:
        """returns True if this migration can be applied to given database"""
        existing_schema = self.get_table_schema(table_name) or ''
        return self._normalize_schema(self.migrated_table_schema) == self._normalize_schema(existing_schema)

    def rename_table(self, old: str, new: str):
        sql = f'ALTER TABLE {old} RENAME TO {new}'
        self.db.execute(sql)

    def init_target_table(self, table_name: str):
        self.db.execute('CREATE TABLE IF NOT EXISTS {} ({})'.format(table_name, self.target_table_structure))

    def fetch_all_rows(self, table_name: str) -> Iterator[sqlite3.Row]:
        sql = f'SELECT * FROM {table_name}'
        cursor = self.db.execute(sql)
        while True:
            row = cursor.fetchone()
            if row is None:
                break
            yield row

    @staticmethod
    def load_record(row: sqlite3.Row) -> YoutubeFeedRecord:
        row = dict(row)
        for field in ['parsed_at', 'feed_name']:
            row.pop(field)
        row['url'] = row.pop('link')
        for field in ['published', 'updated', 'scheduled']:
            value = row[field]
            # handle db records with old format
            if isinstance(value, str):
                row[field] = datetime.fromisoformat(value)
        return YoutubeFeedRecord(**row)

    def transform(self, row: sqlite3.Row) -> Optional[Dict[str, Any]]:
        """transform row in existing format into a row in target format"""
        record = self.load_record(row)

        transformed_row = {
            'parsed_at': row['parsed_at'],
            'feed_name': row['feed_name'],
            'uid': '{}:{}'.format(row['feed_name'], row['video_id']),
            'hashsum': record.hash(),
            'class_name': record.__class__.__name__,
            'as_json': record.as_json(),
        }
        return transformed_row

    def store(self, rows: Union[Dict[str, Any], List[Dict[str, Any]]]) -> None:
        sql = "INSERT OR IGNORE INTO {} VALUES({})".format(self.table_name, self.target_row_structure)
        if not isinstance(rows, list):
            rows = [rows]
        self.db.executemany(sql, rows)

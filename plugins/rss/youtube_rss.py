from typing import Sequence, Any
import datetime
import logging
import sqlite3
from typing import Optional, Union

import aiohttp
import feedparser
from pydantic import ConfigDict, ValidationError

from core import interfaces
from plugins.rss import yt_info

import pydantic

from core.config import Plugins
from core.interfaces import TaskMonitor, TaskMonitorEntity, ActorConfig, HttpTaskMonitorEntity, HttpTaskMonitor


class Record(interfaces.Record):
    model_config = ConfigDict(extra='allow')

    url: str
    title: str
    published: str
    updated: str
    author: str
    video_id: str
    summary: str
    views: Optional[int]
    scheduled: Optional[str] = None

    def check_scheduled(self):
        if self.views == 0:
            try:
                scheduled = yt_info.get_sched_isoformat(self.video_id)
            except Exception:
                logging.exception('Exception while trying to get "scheduled" field, skipping')
                scheduled = None
        else:
            scheduled = None
        self.scheduled = scheduled

    def __str__(self):
        return self.format_record()

    def __repr__(self):
        return f'Record({self.updated=}, {self.author=}, {self.title=})'

    @staticmethod
    def format_date(datestring, timezone: Optional[datetime.timezone] = None) -> str:
        dt = datetime.datetime.fromisoformat(datestring).astimezone(timezone)
        return dt.strftime('%Y-%m-%d %H:%M')

    def format_record(self, timezone: Optional[datetime.timezone] = None):
        scheduled = self.scheduled
        if scheduled:
            scheduled_time = '\nscheduled to {}'.format(self.format_date(scheduled, timezone))
        else:
            scheduled_time = ''
        template = '{}\n{}\npublished by {} at {}'
        return template.format(self.url, self.title, self.author, self.format_date(self.published, timezone)) + scheduled_time

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
    etag: pydantic.PrivateAttr = None
    modified: pydantic.PrivateAttr = None
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
        feeds = {e.name: e.url for e in entities}
        self.feedparser = RSS2MSG(feeds, conf.db_path)

    async def get_new_records(self, entity: FeedMonitorEntity, session: aiohttp.ClientSession):
        return self.feedparser.get_records(entity, session)

class RSS2MSG:

    def __init__(self, feeds: List[FeedMonitorEntity], db_path=':memory:'):
        '''entries parsed from `feed_links` in `feeds` will be put in table `records`'''
        self.feeds = feeds
        self.db = RecordDB(db_path)
        db_size = self.db.get_size()
        logging.info('{} records in DB'.format(db_size))
        if db_size == 0:
            for feed in self.feeds:
                self.get_records(feed)

    async def get_feed(self, entity: FeedMonitorEntity, session: Optional[aiohttp.ClientSession] = None):
        session = session or aiohttp.ClientSession()
        async with session.get(entity.url) as response:
            text = await response.text()
        if response.status == 304:
            return None
        elif response.status != 200:
            logging.warning(f'got code {response.status} while fetching {entity.url}')
            return None
        try:
            feed = feedparser.parse(text, response_headers=response.headers)
            if feed.get('entries') is not None:
                return feed
            else:
                from pprint import pformat
                logging.debug(f'feed for {entity.url} has no entries, probably broken:')
                logging.debug(pformat(feed))
                raise Exception(f'got broken feed while fetching {entity.url}')
        except Exception as e:
            logging.warning('Exception while updating rss feed: {}'.format(e))
            return None


    def parse_entry(self, entry: Union[dict, feedparser.FeedParserDict]) -> Record:
        parsed = {}
        parsed['url'] = entry['link']
        parsed['title'] = entry['title']
        parsed['updated'] = entry['updated']

        parsed['published'] = entry.get('published')
        parsed['author'] = entry.get('author')
        parsed['summary'] = entry.get('summary')
        parsed['video_id'] = entry.get('yt_videoid', 'video_id missing')
        try:
            views = int(entry['media_statistics']['views'])
        except (ValueError, KeyError, TypeError):
            views = None
        parsed['views'] = views
        record = Record(**parsed)
        return record

    def parse_entries(self, feed):
        records = []
        for entry in feed['entries']:
            try:
                record = self.parse_entry(entry)
            except KeyError as e:
                logging.warning(f'Youtube rss parser failed to construct record from rss entry {entry}: missing  necessarily field {e}')
                continue
            except ValidationError as e:
                logging.warning(f'Youtube rss parser failed to construct record from rss entry {entry}: {e}')
                continue
            records.append(record)
        return records

    def get_latest_record(self, video_id) -> Optional[Record]:
        latest_row = self.db.select_latest(video_id)
        if latest_row is not None:
            latest_row = dict(latest_row)
            latest_row['url'] = latest_row.pop('link')
            return Record(**latest_row)
        else:
            return None

    def get_records(self, feedname):
        link = self.feeds[feedname]
        feed = self.get_feed(link)
        if feed is None:
            return []
        records = self.parse_entries(feed)
        new_records = []
        for record in records:
            if not self.db.row_exists(record.video_id):
                # only first record for given video_id is send to actions
                record.check_scheduled()
                new_records.append(record)
                template = '{} {:<8} [{}] {}'
                logging.info(template.format(record.format_date(record.published), feedname, record.video_id, record.title))
            if not self.db.row_exists(record.video_id, record.updated):
                # every new record for given video_id will be stored in db
                previous = self.get_latest_record(record.video_id)
                if previous is not None and previous.scheduled is not None:
                    logging.debug(f'{record.video_id=} has last {previous.scheduled=}, updating')
                    record.check_scheduled()
                    if record.scheduled is not None:
                        if record.scheduled < previous.scheduled:
                            msg = 'In feed {} record [{}] {} rescheduled back from {} to {}'
                            logging.warning(
                                msg.format(feedname, record.video_id, record.title, previous.scheduled, record.scheduled))
                            # treat rescheduled records as new if scheduled time is earlier than before
                            # to allow action run on time, though it will run second time later
                            new_records.append(record)
                now = datetime.datetime.now(tz=datetime.timezone.utc).isoformat(timespec='seconds')
                additional_fields = {'feed_name': feedname, 'parsed_at': now}
                row = record.as_dict(additional_fields)
                self.db.insert_row(row)

        return new_records

class RecordDB:

    def __init__(self, db_path):
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        self.cursor = self.db.cursor()
        record_structure = 'parsed_at datetime, feed_name text, author text, video_id text, link text, title text, summary text, published datetime, updated datetime, scheduled datetime DEFAULT NULL, views intefer, PRIMARY KEY(video_id, updated)'
        self.cursor.execute('CREATE TABLE IF NOT EXISTS records ({})'.format(record_structure))
        self.db.commit()

    def insert_row(self, row: dict) -> None:
        row_structure = ':parsed_at, :feed_name, :author, :video_id, :url, :title, :summary, :published, :updated, :scheduled, :views'
        sql = "INSERT INTO records VALUES({})".format(row_structure)
        self.cursor.execute(sql, row)
        self.db.commit()

    def row_exists(self, video_id: str, updated: Optional[str] = None) -> bool:
        if updated is not None:
            sql = "SELECT 1 FROM records WHERE video_id=:video_id AND updated=:updated LIMIT 1"
        else:
            sql = "SELECT 1 FROM records WHERE video_id=:video_id LIMIT 1"
        keys = {'video_id': video_id, 'updated': updated}
        self.cursor.execute(sql, keys)
        return bool(self.cursor.fetchone())

    def select_latest(self, video_id: str) -> Optional[sqlite3.Row]:
        sql = "SELECT * FROM records WHERE video_id=:video_id ORDER BY updated DESC LIMIT 1"
        keys = {'video_id': video_id}
        self.cursor.execute(sql, keys)
        return self.cursor.fetchone()

    def get_size(self):
        sql = 'SELECT COUNT(1) FROM records'
        self.cursor.execute(sql)
        return int(self.cursor.fetchone()[0])

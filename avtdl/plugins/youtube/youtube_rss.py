import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Union

import aiohttp
import feedparser
from pydantic import ConfigDict

from avtdl.core import utils
from avtdl.core.config import Plugins
from avtdl.core.db import BaseRecordDB
from avtdl.core.interfaces import Record
from avtdl.plugins.rss.generic_rss import GenericRSSMonitor, GenericRSSMonitorConfig, GenericRSSMonitorEntity
from avtdl.plugins.youtube import video_info
from avtdl.plugins.youtube.common import thumbnail_url


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
    author: str
    """author's name, as shown on the channel icon"""
    video_id: str
    """short string identifying the video on Youtube. Part of the video url"""
    summary: str
    """video's description"""
    views: Optional[int]
    """current number of views. Is zero for upcoming and ongoing livestreams"""
    scheduled: Optional[datetime] = None
    """scheduled time for an upcoming livestream to start at, otherwise absent"""

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

    def discord_embed(self) -> dict:
        embed = {
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


class RecordDB(BaseRecordDB):
    table_structure = 'parsed_at datetime, feed_name text, author text, video_id text, link text, title text, summary text, published datetime, updated datetime, scheduled datetime DEFAULT NULL, views integer, PRIMARY KEY(video_id, updated)'
    row_structure = ':parsed_at, :feed_name, :author, :video_id, :url, :title, :summary, :published, :updated, :scheduled, :views'
    id_field = 'video_id'
    exact_id_field = 'updated'
    group_id_field = 'feed_name'
    sorting_field = 'parsed_at'

    def store(self, rows: Union[Dict[str, Any], List[Dict[str, Any]]]) -> None:
        return super().store(rows)

    def row_exists(self, video_id: str, updated: Optional[datetime] = None) -> bool:
        return super().row_exists(video_id, updated)

    def fetch_row(self, video_id: str, updated: Optional[datetime] = None) -> Optional[sqlite3.Row]:
        return super().fetch_row(video_id, updated)

    def get_size(self, feed_name: Optional[str] = None) -> int:
        '''return number of records, total or for specified feed, are stored in db'''
        return super().get_size(feed_name)


@Plugins.register('rss', Plugins.kind.ACTOR_ENTITY)
class FeedMonitorEntity(GenericRSSMonitorEntity):
    update_interval : float = 900
    """How often the feed should be updated, in seconds"""
    track_reschedule: bool = True
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

    RecordDB = RecordDB

    def __init__(self, conf: FeedMonitorConfig, entities: Sequence[FeedMonitorEntity]):
        super().__init__(conf, entities)

    async def get_new_records(self, entity: FeedMonitorEntity, session: aiohttp.ClientSession) -> Sequence[YoutubeFeedRecord]:
        records = await self.get_records(entity, session)
        new_records = []

        # record.check_scheduled() involves loading video page, so it should only be done when necessarily
        # and here seems to be the only good place for it, since network request requires "session" object
        for record in records:
            previous = self.load_record(record, entity)
            if previous is None:
                await record.check_scheduled(session)
                continue
            if not entity.track_reschedule:
                continue
            if previous is not None and previous.scheduled is not None:
                self.logger.debug(f'{record.video_id=} has last {previous.scheduled=}, updating')
                await record.check_scheduled(session)
                if record.scheduled is None:
                    continue
                if record.scheduled < previous.scheduled:
                    # treat rescheduled records as new if scheduled time is earlier than before
                    # to allow action run on time, though it will run second time later
                    msg = 'In feed {} record [{}] {} rescheduled back from {} to {}'
                    msg = msg.format(entity.name, record.video_id, record.title, previous.scheduled, record.scheduled)
                    self.logger.warning(msg)
                    new_records.append(record)

        new_records.extend(self.filter_new_records(records, entity))
        return new_records

    def get_record_id(self, record: YoutubeFeedRecord) -> str:
        return record.video_id

    def _get_record_id(self, record: YoutubeFeedRecord, entity: FeedMonitorEntity) -> str:
        return self.get_record_id(record)

    def record_got_updated(self, record: YoutubeFeedRecord, entity: FeedMonitorEntity) -> bool:
        return self.db.row_exists(record.video_id) and not self.db.row_exists(record.video_id, record.updated)

    def store_records(self, records: Sequence[YoutubeFeedRecord], entity: FeedMonitorEntity):
        rows = []
        for record in records:
            now = datetime.now(tz=timezone.utc).isoformat(timespec='seconds')
            additional_fields = {'feed_name': entity.name, 'parsed_at': now}
            row = record.as_dict(additional_fields)
            rows.append(row)
        self.db.store(rows)

    def load_record(self, record: YoutubeFeedRecord, entity: FeedMonitorEntity) -> Optional[YoutubeFeedRecord]:
        raw_latest_row = self.db.fetch_row(record.video_id)
        if raw_latest_row is None:
            return None
        latest_row = dict(raw_latest_row)
        for field in ['parsed_at', 'feed_name']:
            latest_row.pop(field)
        latest_row['url'] = latest_row.pop('link')
        for field in ['published', 'updated', 'scheduled']:
            value = latest_row[field]
            # handle db records with old format
            if isinstance(value, str):
                latest_row[field] = datetime.fromisoformat(value)
        return YoutubeFeedRecord(**latest_row)

    @classmethod
    def _parse_entry(cls, entry: feedparser.FeedParserDict) -> YoutubeFeedRecord:
        parsed = {}
        parsed['url'] = entry['link']
        parsed['title'] = entry['title']
        parsed['updated'] = utils.make_datetime(entry['updated_parsed'])

        parsed['published'] = utils.make_datetime(entry['published_parsed'])
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

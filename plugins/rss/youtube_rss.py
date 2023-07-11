import asyncio
from dataclasses import dataclass
import logging
from typing import Dict, Sequence

from core.config import Plugins
from core.interfaces import TaskMonitor, TaskMonitorEntity, MonitorConfig
from plugins.rss.feed_parser import RSS2MSG, Record as RSSRecord

@Plugins.register('rss', Plugins.kind.MONITOR_ENTITY)
@dataclass
class FeedMonitorEntity(TaskMonitorEntity):
    name: str
    url: str
    update_interval: int = 900

@Plugins.register('rss', Plugins.kind.MONITOR_CONFIG)
@dataclass
class FeedMonitorConfig(MonitorConfig):
    db_path: str
    ua: str

@Plugins.register('rss', Plugins.kind.MONITOR)
class FeedMonitor(TaskMonitor):

    def __init__(self, conf: FeedMonitorConfig, entities: Sequence[FeedMonitorEntity]):
        super().__init__(conf, entities)
        feeds = {e.name: e.url for e in entities}
        self.feedparser = RSS2MSG(feeds, conf.db_path)

    async def get_new_records(self, entity: FeedMonitorEntity):
        return self.feedparser.get_records(entity.name)

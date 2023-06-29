import asyncio
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from interfaces import Monitor, MonitorEntity, MonitorConfig, Record
from feed_parser import Record as FeedRecord, RSS2MSG

@dataclass
class FeedMonitorEntity(MonitorEntity):
    name: str
    url: str
    update_interval: int = 900

@dataclass
class FeedMonitorConfig(MonitorConfig):
    debug: str
    db_path: str


class FeedMonitor(Monitor):
    def __init__(self, conf: FeedMonitorConfig, entities: List[FeedMonitorEntity]):
        self.debug = conf.debug
        self.tasks: Dict[str, asyncio.Task] = {}
        self.feedparser = RSS2MSG([e.url for e in entities], conf.db_path)
        super().__init__(conf, entities)

    async def run(self):
        for name, feed in self.entities.items():
            self.tasks[name] = asyncio.create_task(self.run_for(feed))
        while True:
            await asyncio.sleep(0.1)

    async def run_for(self, feed):
        while True:
            records = self.feedparser.get_records(feed.name)
            for record in records:
                for cb in self.callbacks[feed.name]:
                    cb(record)
            await asyncio.sleep(feed.update_interval)

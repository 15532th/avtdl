import asyncio
from dataclasses import dataclass
import logging
from typing import Dict, Sequence

from interfaces import Monitor, MonitorEntity, MonitorConfig
from feed_parser import RSS2MSG

@dataclass
class FeedMonitorEntity(MonitorEntity):
    name: str
    url: str
    update_interval: int = 900

@dataclass
class FeedMonitorConfig(MonitorConfig):
    db_path: str
    ua: str


class FeedMonitor(Monitor):
    def __init__(self, conf: FeedMonitorConfig, entities: Sequence[FeedMonitorEntity]):
        super().__init__(conf, entities)
        self.tasks: Dict[str, asyncio.Task] = {}
        feeds = {e.name: e.url for e in entities}
        self.feedparser = RSS2MSG(feeds, conf.db_path)

    async def run(self):
        for name, feed in self.entities.items():
            self.tasks[name] = asyncio.create_task(self.run_for(feed), name=f'rss:{feed.name}')
        await asyncio.Future()

    async def run_for(self, entry: FeedMonitorEntity):
        while True:
            records = self.feedparser.get_records(entry.name)
            for record in records:
                for cb in self.callbacks[entry.name]:
                    cb(record)
            logging.debug(f'rss:{entry.name} got {len(records)} new records, next update in {entry.update_interval}')
            await asyncio.sleep(entry.update_interval)

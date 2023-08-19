from typing import Sequence

from core.config import Plugins
from core.interfaces import TaskMonitor, TaskMonitorEntity, ActorConfig
from plugins.rss.feed_parser import RSS2MSG


@Plugins.register('rss', Plugins.kind.ACTOR_ENTITY)
class FeedMonitorEntity(TaskMonitorEntity):
    name: str
    url: str
    update_interval: int = 900

@Plugins.register('rss', Plugins.kind.ACTOR_CONFIG)
class FeedMonitorConfig(ActorConfig):
    db_path: str = ':memory:'

@Plugins.register('rss', Plugins.kind.ACTOR)
class FeedMonitor(TaskMonitor):

    def __init__(self, conf: FeedMonitorConfig, entities: Sequence[FeedMonitorEntity]):
        super().__init__(conf, entities)
        feeds = {e.name: e.url for e in entities}
        self.feedparser = RSS2MSG(feeds, conf.db_path)

    async def get_new_records(self, entity: FeedMonitorEntity):
        return self.feedparser.get_records(entity.name)

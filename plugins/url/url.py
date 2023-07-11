#!/usr/bin/env python3

from dataclasses import dataclass

import aiohttp

from core.config import Plugins
from core.interfaces import MonitorConfig, Record, TaskMonitor, TaskMonitorEntity

@Plugins.register('get_url', Plugins.kind.MONITOR_CONFIG)
@dataclass
class UrlMonitorConfig(MonitorConfig):
    pass

@Plugins.register('get_url', Plugins.kind.MONITOR_ENTITY)
@dataclass
class UrlMonitorEntity(TaskMonitorEntity):
    name: str
    update_interval: int
    url: str

@Plugins.register('get_url', Plugins.kind.MONITOR)
class UrlMonitor(TaskMonitor):
    async def get_new_records(self, entity: UrlMonitorEntity):
        async with aiohttp.ClientSession() as session:
            async with session.get(entity.url) as r:
                text = await r.text()
                record = Record(title=text, url=entity.url)
        return [record]

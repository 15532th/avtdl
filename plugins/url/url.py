#!/usr/bin/env python3

from dataclasses import dataclass

import aiohttp

from core.config import Plugins
from core.interfaces import TaskMonitor, TaskMonitorEntity, Record, ActorEntity, TextRecord


@Plugins.register('get_url', Plugins.kind.ACTOR_CONFIG)
@dataclass
class UrlMonitorConfig(ActorEntity):
    pass

@Plugins.register('get_url', Plugins.kind.ACTOR_ENTITY)
@dataclass
class UrlMonitorEntity(TaskMonitorEntity):
    url: str

@Plugins.register('get_url', Plugins.kind.ACTOR)
class UrlMonitor(TaskMonitor):
    async def get_new_records(self, entity: UrlMonitorEntity):
        async with aiohttp.ClientSession() as session:
            async with session.get(entity.url) as r:
                text = await r.text()
                record = TextRecord(text=text)
        return [record]

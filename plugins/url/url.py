#!/usr/bin/env python3

from typing import Optional

import aiohttp
from pydantic import Field

from core.config import Plugins
from core.interfaces import ActorConfig, TextRecord
from core.monitors import HttpTaskMonitor, HttpTaskMonitorEntity


@Plugins.register('get_url', Plugins.kind.ACTOR_CONFIG)
class UrlMonitorConfig(ActorConfig):
    pass

@Plugins.register('get_url', Plugins.kind.ACTOR_ENTITY)
class UrlMonitorEntity(HttpTaskMonitorEntity):
    url: str
    """url to monitor"""
    last_record_hash: Optional[str] = Field(exclude=True, default=None)
    """internal variable to persist state between updates. Used to keep hash of page content on previous update"""

@Plugins.register('get_url', Plugins.kind.ACTOR)
class UrlMonitor(HttpTaskMonitor):
    """
    Monitor url content

    Download web-page content and emit it as a text record if it has changed.
    """

    async def get_new_records(self, entity: UrlMonitorEntity, session: aiohttp.ClientSession):
        text = await self.request(entity.url, entity, session)
        if text is None:
            return []
        record = TextRecord(text=text)
        record_hash = record.hash()
        if record_hash != entity.last_record_hash:
            entity.last_record_hash = record_hash
            return [record]
        return []
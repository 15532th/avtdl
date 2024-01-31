#!/usr/bin/env python3

from typing import Optional, Sequence

import aiohttp
from pydantic import Field

from avtdl.core.config import Plugins
from avtdl.core.interfaces import ActorConfig, TextRecord
from avtdl.core.monitors import HttpTaskMonitor, HttpTaskMonitorEntity

Plugins.register('get_url', Plugins.kind.ASSOCIATED_RECORD)(TextRecord)

@Plugins.register('get_url', Plugins.kind.ACTOR_CONFIG)
class UrlMonitorConfig(ActorConfig):
    pass

@Plugins.register('get_url', Plugins.kind.ACTOR_ENTITY)
class UrlMonitorEntity(HttpTaskMonitorEntity):
    url: str
    """url to monitor"""
    last_record_hash: Optional[str] = Field(exclude=True, default=None)
    """internal variable to persist state between updates. Used to keep hash of the page contents on previous update"""

@Plugins.register('get_url', Plugins.kind.ACTOR)
class UrlMonitor(HttpTaskMonitor):
    """
    Monitor web page text

    Download contents of web page at `url` and emit it as a `TextRecord`
    if it has changed since the last update. Intended for working with simple
    text endpoints.
    """

    async def get_new_records(self, entity: UrlMonitorEntity, session: aiohttp.ClientSession) -> Sequence[TextRecord]:
        text = await self.request(entity.url, entity, session)
        if text is None:
            return []
        record = TextRecord(text=text)
        record_hash = record.hash()
        if record_hash != entity.last_record_hash:
            entity.last_record_hash = record_hash
            return [record]
        return []

import asyncio
import datetime
import json
import logging
from textwrap import shorten
from typing import Any, List, Optional, Sequence

import aiohttp
import dateutil
import multidict
from pydantic import Field, field_validator

from core.interfaces import Actor, ActorConfig, ActorEntity, Record
from core.plugins import Plugins

EMBEDS_PER_MESSAGE = 10
EMBED_TITLE_MAX_LENGTH = 256
EMBED_DESCRIPTION_MAX_LENGTH = 4096


class DiscordWebhook:

    def __init__(self, name: str, hook_url: str, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger('DiscordHook')
        self.name = name
        self.hook_url = hook_url
        self.send_query: Optional[asyncio.Queue] = None
        self.session: Optional[aiohttp.ClientSession] = None

    def to_be_sent(self, record: Record):
        if self.send_query is None:
            return
        self.logger.debug(f'adding record to send query: {record!r}')
        self.send_query.put_nowait(record)

    async def run(self):
        self.send_query = asyncio.Queue()
        self.session = aiohttp.ClientSession()

        until_next_try = 0
        to_be_sent = []
        while True:
            await asyncio.sleep(until_next_try)
            try:
                while len(to_be_sent) < EMBEDS_PER_MESSAGE:
                    record = await asyncio.wait_for(self.send_query.get(), 60/EMBEDS_PER_MESSAGE)
                    to_be_sent.append(record)
            except asyncio.TimeoutError:
                pass
            if len(to_be_sent) == 0:
                continue
            message = MessageFormatter.format(to_be_sent)
            try:
                response = await self.session.post(self.hook_url, json=message)
                text = await response.text()
            except Exception as e:
                self.logger.warning(f'[{self.name}] error while sending message with Discord webhook: {e}')
                until_next_try = 60
                continue
            try:
                response.raise_for_status()
            except aiohttp.ClientResponseError as e:
                self.logger.warning(f'got {response.status} ({response.reason}) from {self.hook_url}')
                if e.status == 400:
                    self.logger.warning(f'[{self.name}] message got rejected with {response.status} ({response.reason}), dropping it')
                    self.logger.debug(f'[{self.name}] response headers: {response.headers}')
                    self.logger.debug(f'[{self.name}] raw request body: {json.dumps(message)}')
                    to_be_sent = []
                elif e.status in  [404, 403]:
                    self.logger.warning(f'[{self.name}] got {e.status} from webhook, interrupting operations. Check if webhook url is still valid: {self.hook_url}')
                    break
            else:
                to_be_sent = []
            until_next_try = self.get_next_delay(response.headers)

    def get_next_delay(self, headers: multidict.CIMultiDictProxy):
        if 'Retry-After' in headers:
            delay = headers.get('Retry-After', 0)
            self.logger.debug(f'Retry-After header is set to {delay}')
        else:
            remaining = headers.get('X-RateLimit-Remaining', '0')
            self.logger.debug(f'X-RateLimit-Remaining={remaining}')
            if remaining == '0':
                delay = headers.get('X-RateLimit-Reset-After', 'no X-RateLimit-Reset-After header in response')
                self.logger.debug(f'X-RateLimit-Reset-After={delay}')
            else:
                delay = 0
        try:
            delay = int(delay)
        except ValueError:
            self.logger.debug(f'failed to parse delay {delay} for {self.hook_url}, using default')
            delay = 6
        return delay


class MessageFormatter:

    @classmethod
    def format(cls, records: List[Record]) -> dict:
        '''take records and format them in Discord webhook payload as embeds'''
        if len(records) > EMBEDS_PER_MESSAGE:
            raise ValueError(f'got {len(records)} records, but only {EMBEDS_PER_MESSAGE} embeds per message are supported')
        embeds = [cls.make_embed(record) for record in records]
        message = cls.make_message(embeds)
        return message

    @classmethod
    def make_message(cls, embeds: List[dict]) -> dict:
        return {
            "content": None,
            "embeds": embeds
        }

    @classmethod
    def make_embed(cls, record: Record) -> dict:
        formatter = getattr(record, 'discord_embed', None)
        if formatter is not None and callable(formatter):
            return formatter()
        return cls.plaintext_embed(record)

    @classmethod
    def plaintext_embed(cls, record: Record) -> dict:
        text = str(record)
        if text.find('\n') > -1:
            title, description = text.split('\n', 1)
        else:
            title, description = '', text
        title = shorten(title, EMBED_TITLE_MAX_LENGTH)
        description = shorten(description, EMBED_DESCRIPTION_MAX_LENGTH)
        return {'title': title, 'description': description}


@Plugins.register('discord.hook', Plugins.kind.ACTOR_CONFIG)
class DiscordHookConfig(ActorConfig):
    pass

@Plugins.register('discord.hook', Plugins.kind.ACTOR_ENTITY)
class DiscordHookEntity(ActorEntity):
    url: str
    timezone: Optional[str] = None # https://en.wikipedia.org/wiki/List_of_tz_database_time_zones
    hook: Optional[Any] = Field(exclude=True, default=None)

    @field_validator('timezone')
    @classmethod
    def check_timezone(cls, timezone: str) -> datetime.timezone:
        tz = dateutil.tz.gettz(timezone)
        if tz is None:
            raise ValueError(f'Unknown timezone: {timezone}')
        return tz

@Plugins.register('discord.hook', Plugins.kind.ACTOR)
class DiscordHook(Actor):
    def __init__(self, conf: DiscordHookConfig, entities: Sequence[DiscordHookEntity]):
        super().__init__(conf, entities)
        for entity in entities:
            entity.hook = DiscordWebhook(entity.name, entity.url, self.logger)

    def handle(self, entity: DiscordHookEntity, record: Record):
        if entity.hook is None:
            return
        entity.hook.to_be_sent(record.as_timezone(entity.timezone))

    async def run(self):
        tasks = [asyncio.create_task(entity.hook.run()) for entity in self.entities.values()]
        await asyncio.wait(tasks)


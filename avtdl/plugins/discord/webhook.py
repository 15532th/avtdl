import asyncio
import datetime
import json
import logging
from textwrap import shorten
from typing import Any, List, Optional, Sequence, Tuple

import aiohttp
import dateutil
import multidict
from pydantic import Field, field_validator

from avtdl.core.interfaces import Actor, ActorConfig, ActorEntity, Record
from avtdl.core.plugins import Plugins

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

    async def run(self) -> None:
        self.send_query = asyncio.Queue()
        self.session = aiohttp.ClientSession()

        until_next_try = 0
        to_be_sent: List[Record] = []
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
            message, pending_records = MessageFormatter.format(to_be_sent)
            try:
                limits_ok = MessageFormatter.check_limits(message)
            except Exception as e:
                self.logger.debug(f'error checking content length limits for message: {e}\nRaw message: "{message}"')
                continue
            if not limits_ok:
                self.logger.warning(f'[{self.name}] prepared message exceeded Discord length limits. Records will be discarded')
                self.logger.debug(f'[{self.name}] message content:\n{message}')
                to_be_sent = pending_records
                continue
            try:
                response = await self.session.post(self.hook_url, json=message)
                text = await response.text()
            except OSError as e:
                self.logger.warning(f'[{self.name}] error while sending message with Discord webhook: {e}')
                until_next_try = 60
                continue  # implicitly saving messages in to_be_sent until the next try
            except Exception as e:
                self.logger.exception(f'[{self.name}] error while sending message with Discord webhook: {e}')
                self.logger.debug(f'raw message text:\n{message}')
                to_be_sent = pending_records
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
                elif e.status in  [404, 403]:
                    self.logger.warning(f'[{self.name}] got {e.status} from webhook, interrupting operations. Check if webhook url is still valid: {self.hook_url}')
                    break
            # if message got send successfully discard records except these that didn't fit into message
            to_be_sent = pending_records
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
    def format(cls, records: List[Record]) -> Tuple[dict, List[Record]]:
        '''take records and format them in Discord webhook payload as embeds
        after the limit on embeds is reached, the rest of the records are returned back'''
        embeds: List[dict] = []
        excess_records = []
        for i, record in enumerate(records):
            record_embeds = cls.make_embeds(record)
            if len(embeds) + len(record_embeds) > EMBEDS_PER_MESSAGE:
                excess_records = records[i:]
                break
            else:
                embeds.extend(record_embeds)
        message = cls.make_message(embeds)
        return message, excess_records

    @classmethod
    def make_message(cls, embeds: List[dict]) -> dict:
        return {
            "content": None,
            "embeds": embeds
        }

    @classmethod
    def make_embeds(cls, record: Record) -> List[dict]:
        formatter = getattr(record, 'discord_embed', None)
        if formatter is not None and callable(formatter):
            embeds = formatter()
            if not isinstance(embeds, list):
                embeds = [embeds]
            return embeds
        return [cls.plaintext_embed(record)]

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

    @classmethod
    def check_limits(cls, message: dict) -> bool:
        # doesn't count field.name and field.value number and size in hope it will not change outcome

        class Limits:
            TOTAL = 6000
            AUTHOR_NAME = 256
            TITLE = 256
            DESCRIPTION = 4096
            FOOTER_TEXT = 2048

        total_length = 0
        embeds = message.get('embeds', [])
        for embed in embeds:
            author_name = len(embed.get('author', {}).get('name', ''))
            title = len(embed.get('title') or '')
            description = len(embed.get('description') or '')
            footer_text = len((embed.get('footer') or {}).get('text') or '')
            if author_name > Limits.AUTHOR_NAME:
                return False
            if title > Limits.TITLE:
                return False
            if description > Limits.DESCRIPTION:
                return False
            if footer_text > Limits.FOOTER_TEXT:
                return False
            total_length += author_name + title + description + footer_text

        if total_length > Limits.TOTAL:
            return False
        return True


@Plugins.register('discord.hook', Plugins.kind.ACTOR_CONFIG)
class DiscordHookConfig(ActorConfig):
    pass


@Plugins.register('discord.hook', Plugins.kind.ACTOR_ENTITY)
class DiscordHookEntity(ActorEntity):
    url: str
    """webhook url"""
    timezone: Optional[str] = None # https://en.wikipedia.org/wiki/List_of_tz_database_time_zones
    """takes timezone name from <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones> (or OS settings if omitted), converts record fields containing date and time to this timezone"""
    hook: Optional[Any] = Field(exclude=True, default=None)
    """internal variable to persist state between update calls, holds DiscordWebhook object for this entity"""

    @field_validator('timezone')
    @classmethod
    def check_timezone(cls, timezone: str) -> datetime.timezone:
        tz = dateutil.tz.gettz(timezone)
        if tz is None:
            raise ValueError(f'Unknown timezone: {timezone}')
        return tz

@Plugins.register('discord.hook', Plugins.kind.ACTOR)
class DiscordHook(Actor):
    """
    Send record to Discord using webhook

    To generate webhook url follow instructions in "Making a Webhook" section of
    <https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks>

    Some record types support rich formatting when sent to Discord, such as
    showing the author's avatar and links to attached images. Youtube videos will
    show thumbnail, however embedding video itself is not supported.

    Records coming within six seconds one after another will be batched together into a single message.
    When too many records are received at once, they will be sent with delays to conform to Discord
    rate limits. Records deemed to be too long to fit in a Discord message
    [length limits](https://discord.com/developers/docs/resources/channel#create-message-jsonform-params)
    will be dropped with a warning.
    """

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

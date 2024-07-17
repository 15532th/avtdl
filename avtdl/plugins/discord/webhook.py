import asyncio
import datetime
import json
import logging
from textwrap import shorten
from typing import Dict, List, Optional, Sequence, Tuple, Union

import aiohttp
import multidict

from avtdl.core.interfaces import Action, ActionEntity, ActorConfig, Record
from avtdl.core.plugins import Plugins
from avtdl.core.utils import RateLimit, monitor_tasks, request_raw

EMBEDS_PER_MESSAGE = 10
EMBED_TITLE_MAX_LENGTH = 256
EMBED_DESCRIPTION_MAX_LENGTH = 4096


class DiscordRateLimit(RateLimit):

    def _submit_headers(self, headers: Union[Dict[str, str], multidict.CIMultiDictProxy[str]], logger: logging.Logger):
        try:
            self.limit_total = int(headers.get('X-RateLimit-Limit', -1))
            self.limit_remaining = int(headers.get('X-RateLimit-Remaining', -1))
            self.reset_at = int(headers.get('X-RateLimit-Reset', -1))
        except ValueError:
            logger.warning(f'[{self.name}] error parsing rate limit headers: "{headers}"')
        else:
            logger.debug(f'[{self.name}] rate limit {self.limit_remaining}/{self.limit_total}, resets after {datetime.timedelta(seconds=self.reset_after)}')

    @staticmethod
    def get_bucket(headers: multidict.CIMultiDictProxy[str]) -> Optional[str]:
        return headers.get('X-RateLimit-Bucket')


class NoRateLimit(DiscordRateLimit):

    def _submit_headers(self, headers: Union[Dict[str, str], multidict.CIMultiDictProxy[str]], logger: logging.Logger):
        pass


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
            author_name = len(embed.get('author', {}).get('name', '') or '')
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
class DiscordHookEntity(ActionEntity):
    url: str
    """webhook url"""


@Plugins.register('discord.hook', Plugins.kind.ACTOR)
class DiscordHook(Action):
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
        self.queues: Dict[str, asyncio.Queue] = {entity.name: asyncio.Queue() for entity in entities}
        self.buckets: Dict[Optional[str], DiscordRateLimit] = {}
        self.buckets[None] = NoRateLimit('fallback bucket')

    def handle(self, entity: DiscordHookEntity, record: Record):
        if not entity.name in self.queues:
            return
        self.logger.debug(f'[{entity.name}] adding record to send query: {record!r}')
        self.queues[entity.name].put_nowait(record)

    async def run(self):
        tasks = []
        async with aiohttp.ClientSession() as session:
            for entity in self.entities.values():
                task = asyncio.create_task(self.run_for(entity, session), name=f'{self.conf.name}:{entity.name}')
                tasks.append(task)
            await monitor_tasks(tasks, logger=self.logger)

    async def run_for(self, entity: DiscordHookEntity, session: aiohttp.ClientSession):
        send_queue = self.queues[entity.name]

        bucket: Optional[str] = None
        until_next_try = 0
        to_be_sent: List[Record] = []

        while True:
            await asyncio.sleep(until_next_try)
            to_be_sent = await self.wait_for_records(send_queue, pending=to_be_sent)
            message, pending_records = self._prepare_message(to_be_sent, entity)
            if message is None:
                to_be_sent = pending_records
                continue

            async with self.buckets[bucket] as _:
                headers = {'Content-Type': 'application/json'}
                try:
                    response = await request_raw(entity.url, session, self.logger,
                                                 method='POST',
                                                 data=json.dumps(message),
                                                 headers=headers,
                                                 raise_errors=True,
                                                 raise_for_status=False)
                    assert response is not None, 'request_json() returned None despite raise_errors=True'
                except (OSError, asyncio.TimeoutError, aiohttp.ClientConnectionError) as e:
                    self.logger.warning(f'[{entity.name}] error while sending message with Discord webhook: {e or type(e)}, saving for the next try')
                    until_next_try = 60
                    continue  # implicitly saving messages in to_be_sent until the next try
                except Exception as e:
                    self.logger.exception(f'[{entity.name}] error while sending message with Discord webhook: {e or type(e)}')
                    self.logger.debug(f'raw message text:\n{message}')
                    to_be_sent = pending_records # discard unsent messages as they might have been the cause of error
                    until_next_try = 60
                    continue

            bucket = DiscordRateLimit.get_bucket(response.headers)
            if not bucket in self.buckets:
                self.buckets[bucket] = DiscordRateLimit(f'{bucket}', self.logger)
            self.buckets[bucket].submit_headers(response.headers, self.logger)

            if self._has_fatal_error(response, entity, message):
                break

            # if message got send successfully discard records except these that didn't fit into message
            if pending_records:
                self.logger.debug(f'carrying {len(pending_records)} pending records to the next loop')
            to_be_sent = pending_records

    @staticmethod
    async def wait_for_records(queue: asyncio.Queue, pending: List[Record]) -> List[Record]:
        to_be_sent = pending
        while len(to_be_sent) < EMBEDS_PER_MESSAGE:
            try:
                record = await asyncio.wait_for(queue.get(), 60 / EMBEDS_PER_MESSAGE)
                to_be_sent.append(record)
            except asyncio.TimeoutError:
                if to_be_sent:
                    break
        return to_be_sent

    def _prepare_message(self, to_be_sent: List[Record], entity: DiscordHookEntity) -> Tuple[Optional[dict], List[Record]]:
        try:
            message, pending_records = MessageFormatter.format(to_be_sent)
            if pending_records:
                self.logger.debug(f'[{entity.name}] out of {len(to_be_sent)} records {len(pending_records)} are left pending')
        except Exception as e:
            self.logger.exception(f'error happened while formatting message: {e}\nRaw records list: "{to_be_sent}"')
            return None, []
        try:
            limits_ok = MessageFormatter.check_limits(message)
        except Exception as e:
            self.logger.exception(f'error checking content length limits for message: {e}\nRaw message: "{message}"')
            return None, pending_records
        if not limits_ok:
            self.logger.warning(f'[{entity.name}] prepared message exceeded Discord length limits. Records will be discarded')
            self.logger.debug(f'[{entity.name}] message content:\n{message}')
            return None, pending_records

        return message, pending_records

    def _has_fatal_error(self, response: aiohttp.ClientResponse, entity: DiscordHookEntity, message: dict) -> bool:
        try:
            response.raise_for_status()
            return False
        except aiohttp.ClientResponseError as e:
            self.logger.warning(f'[{entity.name}] failed to send message: got {response.status} ({response.reason}) from {entity.url}')
            if e.status == 400:
                self.logger.warning(f'[{entity.name}] message got rejected with {response.status} ({response.reason}), dropping it')
                self.logger.debug(f'[{entity.name}] request headers: {response.request_info.headers}')
                self.logger.debug(f'[{entity.name}] response headers: {response.headers}')
                self.logger.debug(f'[{entity.name}] raw request body: {json.dumps(message)}')
                return False
            elif e.status in [404, 401]:
                self.logger.warning(f'[{entity.name}] got {e.status} from webhook, interrupting operations. Check if webhook url is still valid: {entity.url}')
                return True
            else:
                return False

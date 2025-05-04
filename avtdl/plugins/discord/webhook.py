import asyncio
import datetime
import json
import logging
from typing import Dict, List, Optional, Sequence, Tuple, Union

import multidict

from avtdl.core.formatters import DiscordEmbedLimits, MessageFormatter
from avtdl.core.interfaces import Action, ActionEntity, ActorConfig, Record, RuntimeContext
from avtdl.core.plugins import Plugins
from avtdl.core.request import HttpClient, HttpResponse, RateLimit
from avtdl.core.utils import SessionStorage


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

    def __init__(self, conf: DiscordHookConfig, entities: Sequence[DiscordHookEntity], ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)
        self.sessions: SessionStorage = SessionStorage(self.logger)
        self.queues: Dict[str, asyncio.Queue] = {entity.name: asyncio.Queue() for entity in entities}
        self.buckets: Dict[Optional[str], DiscordRateLimit] = {}
        self.buckets[None] = NoRateLimit('fallback bucket')

    def handle(self, entity: DiscordHookEntity, record: Record):
        if not entity.name in self.queues:
            return
        self.logger.debug(f'[{entity.name}] adding record to send query: {record!r}')
        self.queues[entity.name].put_nowait(record)

    async def run(self):
        session = self.sessions.get_session(name=self.conf.name)
        client = HttpClient(self.logger, session)
        for entity in self.entities.values():
            _ = self.controller.create_task(self.run_for(entity, client), name=f'{self.conf.name}:{entity.name}')
        await self.sessions.ensure_closed()

    async def run_for(self, entity: DiscordHookEntity, client: HttpClient):
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
                response = await client.request(entity.url, data_json=message, method='POST')

            if response is None:
                self.logger.warning(f'[{entity.name}] network error while sending message with Discord webhook, saving for the next try')
                until_next_try = 60
                continue  # implicitly saving messages in to_be_sent until the next try
            elif not response.ok:
                self.logger.warning(f'[{entity.name}] error while sending message with Discord webhook: got {response.status} ({response.reason or "No reason"}) {response.text}')
                self.logger.debug(f'raw message text:\n{message}')
                to_be_sent = pending_records # discard unsent messages as they might have been the cause of error
                until_next_try = 60
                continue
            if self._has_fatal_error(response, entity, message):
                break

            bucket = DiscordRateLimit.get_bucket(response.headers)
            if not bucket in self.buckets:
                self.buckets[bucket] = DiscordRateLimit(f'{bucket}', self.logger)
            self.buckets[bucket].submit_headers(response.headers, self.logger)

            # if message got send successfully discard records except these that didn't fit into message
            if pending_records:
                self.logger.debug(f'carrying {len(pending_records)} pending records to the next loop')
            to_be_sent = pending_records

    @staticmethod
    async def wait_for_records(queue: asyncio.Queue, pending: List[Record]) -> List[Record]:
        to_be_sent = pending
        while len(to_be_sent) < DiscordEmbedLimits.EMBEDS_PER_MESSAGE:
            try:
                record = await asyncio.wait_for(queue.get(), 60 / DiscordEmbedLimits.EMBEDS_PER_MESSAGE)
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

    def _has_fatal_error(self, response: HttpResponse, entity: DiscordHookEntity, message: dict) -> bool:
        if response.ok:
            return False
        self.logger.warning(f'[{entity.name}] failed to send message: got {response.status} ({response.reason}) from {response.url}')
        if response.status == 400:
            self.logger.warning(f'[{entity.name}] message got rejected with {response.status} ({response.reason}), dropping it')
            self.logger.debug(f'[{entity.name}] request headers: {response.request_headers}')
            self.logger.debug(f'[{entity.name}] response headers: {response.headers}')
            self.logger.debug(f'[{entity.name}] raw request body: {json.dumps(message)}')
            return False
        elif response.status in [404, 401]:
            self.logger.warning(f'[{entity.name}] got {response.status} from webhook, interrupting operations. Check if webhook url is still valid: {response.url}')
            return True
        else:
            return False

import asyncio
import json
from typing import List, Optional, Sequence

import aiohttp
from pydantic import ValidationError

from core import utils
from core.interfaces import MAX_REPR_LEN, Record
from core.monitors import BaseFeedMonitor, BaseFeedMonitorConfig, BaseFeedMonitorEntity
from core.plugins import Plugins
from plugins.youtube.community_info import CommunityPostInfo, get_continuation_token, get_posts_renderers, \
    prepare_next_page_request
from plugins.youtube.utils import get_initial_data


class CommunityPostRecord(Record, CommunityPostInfo):
    channel_id: str
    post_id: str
    author: str
    vote_count: str
    sponsor_only: bool
    published_text: str
    full_text: str
    attachments: List[str]
    original_post: Optional['CommunityPostRecord'] = None

    def __repr__(self) -> str:
        return f'{self.post_id} [{self.author}] {self.full_text[:MAX_REPR_LEN]}'

    def __str__(self) -> str:
        channel_post_url = f'https://www.youtube.com/channel/{self.channel_id}/community?lb={self.post_id}'
        sponsor_only = '(Member only)' if self.sponsor_only else ''

        header = f'[{self.author}, {self.published_text} {sponsor_only}] {self.vote_count}'
        body = self.full_text
        attachments = '\n'.join(self.attachments)
        original_post = str(self.original_post) if self.original_post else ''
        return '\n'.join((channel_post_url, header, body, attachments, original_post))

    def discord_embed(self) -> dict:
        channel_url = f'https://www.youtube.com/channel/{self.channel_id}'
        post_url = f'https://www.youtube.com/post/{self.post_id}'

        attachments = '\n'.join(self.attachments)
        original_post = str(self.original_post) if self.original_post else ''
        text = '\n'.join([self.full_text, attachments, original_post])
        text = text.replace('\n', ' \r\n')

        embed = {
            'title': post_url,
            'description': text,
            'url': post_url,
            'color': None,
            'author': {'name': self.author, 'url': channel_url},
            'fields': [
                {
                    'name': '',
                    'value': self.published_text
                }
            ]
        }
        if self.sponsor_only:
            embed['fields'].append({'name': 'Member only', 'value': ''})
        if self.attachments:
            embed['image'] = {'url': self.attachments[0]}
        return embed

@Plugins.register('community', Plugins.kind.ACTOR_CONFIG)
class CommunityPostsMonitorConfig(BaseFeedMonitorConfig):
    pass


@Plugins.register('community', Plugins.kind.ACTOR_ENTITY)
class CommunityPostsMonitorEntity(BaseFeedMonitorEntity):
    update_interval: float = 1800

    max_continuation_depth: int = 10
    next_page_delay: float = 1
    allow_discontinuity: bool = False # store already fetched records on failure to load one of older pages
    fetch_until_the_end_of_feed_mode: bool = False


@Plugins.register('community', Plugins.kind.ACTOR)
class CommunityPostsMonitor(BaseFeedMonitor):

    def _parse_entries(self, page: dict) -> List[CommunityPostRecord]:
        post_renderers = get_posts_renderers(page)
        records: List[CommunityPostRecord] = []
        for item in post_renderers:
           try:
               info = CommunityPostInfo.from_post_renderer(item)
               record = CommunityPostRecord(**info.model_dump())
               records.append(record)
           except ValidationError as e:
               self.logger.debug(f'error parsing item {item}: {e}')
               continue
        return records


    async def get_records(self, entity: CommunityPostsMonitorEntity, session: aiohttp.ClientSession) -> Sequence[Record]:
        raw_page = await self.request(entity.url, entity, session)
        if raw_page is None:
            return []
        raw_page_text = await raw_page.text()


        initial_page = get_initial_data(raw_page_text)
        continuation_token = get_continuation_token(initial_page)
        records = current_page_records = self._parse_entries(initial_page)

        if entity.fetch_until_the_end_of_feed_mode:
            self.logger.info(f'[{entity.name}] "fetch_until_the_end_of_feed_mode" setting is enabled, will keep loading through already seen pages until the end. Disable it in config after it succeeds once')

        current_page = 1
        while True:
            if continuation_token is None:
                self.logger.debug(f'[{entity.name}] no continuation link on {current_page - 1} page, end of feed reached')
                entity.fetch_until_the_end_of_feed_mode = False
                break
            if not entity.fetch_until_the_end_of_feed_mode:
                if current_page > entity.max_continuation_depth:
                    self.logger.info(f'[{entity.name}] reached continuation limit of {entity.max_continuation_depth}, aborting update')
                    break
                if not all(self.record_is_new(record, entity) for record in current_page_records):
                    self.logger.debug(f'[{entity.name}] found already stored records on {current_page - 1} page')
                    break
            self.logger.debug(f'[{entity.name}] all records on page {current_page - 1} are new, loading next one')
            url, headers, post_body = prepare_next_page_request(initial_page, continuation_token, cookies=session.cookie_jar)
            next_page = await utils.request_json(url, session, self.logger, method='POST', headers=headers, data=json.dumps(post_body), retry_times=3, retry_multiplier=2, retry_delay=5)
            if next_page is None:
                if entity.allow_discontinuity or entity.fetch_until_the_end_of_feed_mode:
                    # when unable to load _all_ new records, return at least current progress
                    return records
                else:
                    # when unable to load _all_ new records, throw away all already parsed and return nothing
                    # to not cause discontinuity in stored data
                    return []
            current_page_records = self._parse_entries(next_page) or [] # perhaps should also issue total failure if no records on the page?
            records.extend(current_page_records)
            continuation_token = get_continuation_token(next_page)

            current_page += 1
            await asyncio.sleep(entity.next_page_delay)

        return records

    def get_record_id(self, record: CommunityPostRecord) -> str:
        return f'{record.channel_id}:{record.post_id}'


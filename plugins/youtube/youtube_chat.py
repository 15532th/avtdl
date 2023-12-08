import json
import logging
import time
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path
from textwrap import shorten
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import aiohttp
import requests
from pydantic import BaseModel, Field

from core import utils
from core.interfaces import MAX_REPR_LEN, Record
from core.monitors import BaseFeedMonitor, BaseFeedMonitorConfig, BaseFeedMonitorEntity
from core.plugins import Plugins
from plugins.youtube import video_info
from plugins.youtube.common import extract_keys, find_all, find_one, parse_navigation_endpoint, prepare_next_page_request


class YoutubeChatRecord(Record):
    uid: str
    action: str
    renderer: str

    author: str
    channel: str
    badges: List[str]
    timestamp: int
    text: Optional[str] = None
    amount: Optional[str] = None
    banner_header: Optional[str] = None
    message_header: Optional[str] = None
    sticker: Optional[str] = None

    def _main_text(self) -> str:
        items = []
        if self.banner_header:
            items.append(f'{self.banner_header}:')
        if self.amount:
            items.append(f'{self.amount}:')
        if self.sticker:
            items.append(self.sticker)
        if self.message_header:
            items.append(self.message_header)
        if self.text:
            items.append(self.text)
        text = ' '.join(items)
        return text

    def __str__(self):
        text = self._main_text()
        return f'[{self.author}] {text}'

    def __repr__(self):
        text = shorten(self._main_text(), MAX_REPR_LEN)
        return f'[{self.action}: {self.renderer}] [{self.author}] {text}'

class Context(BaseModel):
    initial_data: Optional[dict] = None
    continuation_url: Optional[str] = None
    continuation_token: Optional[str] = None
    base_update_interval: float = 120
    is_replay: Optional[bool] = None
    done: bool = False

@Plugins.register('prechat', Plugins.kind.ACTOR_ENTITY)
class YoutubeChatMonitorEntity(BaseFeedMonitorEntity):
    url: str
    update_interval: float = 20
    adjust_update_interval: bool = Field(exclude=True, default=False)
    context: Context = Field(exclude=True, default=Context())


@Plugins.register('prechat', Plugins.kind.ACTOR_CONFIG)
class YoutubeChatMonitorConfig(BaseFeedMonitorConfig):
    pass


@Plugins.register('prechat', Plugins.kind.ACTOR)
class YoutubeChatMonitor(BaseFeedMonitor):

    def get_record_id(self, record: YoutubeChatRecord) -> str:
       return record.uid

    async def get_records(self, entity: YoutubeChatMonitorEntity, session: aiohttp.ClientSession) -> Sequence[YoutubeChatRecord]:
        first_time = False
        if entity.context.done:
            return []
        actions = {}
        if entity.context.continuation_token is None:
            actions.update(await self._get_first(entity, session))
            first_time = True
        if entity.context.continuation_token is not None:
            actions.update(await self._get_next(entity, session))
        records = Parser().run_parsers(actions)
        if not first_time:
            new_update_interval = self._new_update_interval(entity, len(records))
            # if new_update_interval != entity.update_interval:
            #     self.logger.debug(f'[{entity.name}] update interval changed from {entity.update_interval} to {new_update_interval}')
            # entity.update_interval gets updated by self.request()
            # which doesn't expect anyone else to touch it
            # in order to work together with it entity.base_update_interval
            # is overwritten with current update interval, while original value
            # is stored in entity.context.base_update_interval
            entity.base_update_interval = entity.update_interval = new_update_interval
        return records

    async def _get_first(self, entity: YoutubeChatMonitorEntity, session: aiohttp.ClientSession) -> Dict[str, list]:
        response = await self.request(entity.url, entity, session)
        if response is None:
            return {}
        page = await response.text()
        try:
            info = video_info.parse_video_page(page, entity.url)
        except Exception as e:
            self.logger.warning(f'[{entity.name}] error parsing page {entity.url}: {e}')
            return {}
        entity.context.is_replay = not (info.is_upcoming or (info.live_start is not None and info.live_end is None))
        entity.context.continuation_url = self._get_continuation_url(entity.context.is_replay)
        actions, continuation, initial_data = self._get_actions(page, first_page=True)
        entity.context.initial_data = initial_data
        entity.context.continuation_token = continuation

        message = find_one(initial_data, '$..conversationBar.conversationBarRenderer.availabilityMessage.messageRenderer.text')
        if message is not None:
            text = Parser.runs_to_text(message)
            self.logger.debug(f'[{entity.name}] {text}')

        if continuation is None:
            self.logger.warning(f'[{entity.name}] first page has no continuation token. Video has no chat or error loading it happened')
            entity.context.done = True
        return actions

    async def _get_next(self, entity: YoutubeChatMonitorEntity, session: aiohttp.ClientSession) -> Dict[str, list]:
        if entity.context.initial_data is None:
            self.logger.warning(f'[{entity}] continuation token is present in absence of initial data. This is a bug')
            return {}
        if entity.context.continuation_url is None:
            self.logger.warning(f'[{entity}] continuation token is present in absence of continuation url. This is a bug')
            return {}
        _, headers, post_body = prepare_next_page_request(entity.context.initial_data, entity.context.continuation_token)
        page = await utils.request(entity.context.continuation_url, session, self.logger, method='POST', headers=headers,
                                       data=json.dumps(post_body), retry_times=3, retry_multiplier=2,
                                       retry_delay=5)
        if page is None:
            entity.context.continuation_token = None
            self.logger.warning(f'[{entity.name}] downloading chat continuation for {entity.url} failed')
            if entity.context.is_replay:
                entity.context.done = True
                self.logger.info(f'[{entity.name}] giving up on downloading chat replay for {entity.url}')
            return {}
        actions, continuation, _ = self._get_actions(page)
        entity.context.continuation_token = continuation
        if continuation is None and entity.context.is_replay:
            self.logger.info(f'[{entity.name}] finished downloading chat replay')
            entity.context.done = True
        return actions

    def _new_update_interval(self, entity: YoutubeChatMonitorEntity, new_records: int) -> float:
        if entity.context.is_replay:
            return 0
        entity.update_interval = max(entity.update_interval, 1)
        if new_records > 0:
            speed = new_records / entity.update_interval
            new_update_interval = int(10 / speed)
            self.logger.debug(f'[{entity.name}] speed: {new_records}/{entity.update_interval:.2f}={speed:.2f}, from {entity.update_interval:.2f} to {new_update_interval:.2f}')
            if new_update_interval < 60:
                return new_update_interval
        return min(entity.update_interval * 1.2, entity.context.base_update_interval)

    def _get_continuation_url(self, replay: bool) -> str:
        if replay:
            # loading chat replay is not implemented yet though
            return 'https://www.youtube.com/youtubei/v1/live_chat/get_live_chat_replay?key=AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8&prettyPrint=false'
        else:
            return 'https://www.youtube.com/youtubei/v1/live_chat/get_live_chat?key=AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8&prettyPrint=false'

    def _get_actions(self, page: str, first_page=False) -> Tuple[Dict[str, list], Optional[str], dict]:
        keys = list(Parser.known_actions)
        anchor = 'var ytInitialData = ' if first_page else ''
        actions, data = extract_keys(page, keys, anchor=anchor)
        continuation = find_one(data, '$..invalidationContinuationData,timedContinuationData,liveChatReplayContinuationData,reloadContinuationData..continuation')
        return actions, continuation, data


class Parser:

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger().getChild('chat_parser')

    def log(self, action_type, renderer_type, renderer):
        self.logger.debug(f'[{action_type}] {renderer_type}: {renderer}')

    def drop(self, action_type, renderer_type, renderer):
        return None

    @staticmethod
    def runs_to_text(runs: dict) -> str:
        parts = []
        for run in runs.get('runs', []):
            if 'navigationEndpoint' in run:
                try:
                    url = parse_navigation_endpoint(run)
                except Exception as e:
                    logging.debug(f'failed to parse navigationEndpoint in chat message: {run}: {e}')
                else:
                    parts.append(url)
                continue # "navigationEndpoint" comes with "text", so skipping parsing "text" is necessarily

            emoji = run.get('emoji')
            if emoji is not None:
                try:
                    shortcut = str(emoji['shortcuts'][0])
                except (KeyError, IndexError, TypeError):
                    shortcut = ''
                try:
                    label = emoji['image']['accessibility']['accessibilityData']['label']
                except (KeyError, IndexError, TypeError):
                    continue
                if shortcut.startswith(':_'):
                    text = shortcut
                elif label == shortcut.strip(':'):
                    text = shortcut
                else:
                    text = label
                parts.append(text)
                continue

            text = run.get('text')
            if text is not None:
                parts.append(text)
        message = ''.join(parts)
        return message

    def parse_chat_renderer(self, action_type: str, renderer_type: str, renderer: dict) -> YoutubeChatRecord:
        uid = renderer.get('id')

        text = self.runs_to_text(renderer.get('message', {}))
        author = renderer.get('authorName', {}).get('simpleText', '[no author]')
        channel_id = renderer.get('authorExternalChannelId') or find_one(renderer, '$..authorExternalChannelId')
        channel = f'https://www.youtube.com/channel/{channel_id}'
        timestamp = renderer.get('timestampUsec')
        badges = renderer.get('authorBadges', []) and find_all(renderer['authorBadges'], '$..label')
        amount = renderer.get('purchaseAmountText', {}).get('simpleText')
        header = renderer.get('headerSubtext')
        header_text = self.runs_to_text(header) if header else None
        sticker = find_one(renderer, '$.sticker.accessibility..label')
        record = YoutubeChatRecord(uid=uid,
                                    action=action_type,
                                    renderer=renderer_type,
                                    author=author,
                                    channel=channel,
                                    timestamp=timestamp,
                                    badges=badges,
                                    text=text,
                                    amount=amount,
                                    message_header=header_text,
                                    sticker=sticker
                    )
        return record

    def parse_banner(self, action_type: str, renderer_type: str, renderer: dict) -> YoutubeChatRecord:
        header = find_one(renderer, '$..liveChatBannerHeaderRenderer.text') or {}
        header_text = self.runs_to_text(header)
        message = find_one(renderer, '$..liveChatTextMessageRenderer')
        if message is None:
            self.logger.debug(f'[{action_type}.{renderer_type}] encounted banner with renderer other than liveChatTextMessageRenderer: {renderer}')
        record = self.parse_chat_renderer(action_type, renderer_type, message)
        record.banner_header = header_text
        return record

    def parse_gift_purchase(self, action_type: str, renderer_type: str, renderer: dict) -> YoutubeChatRecord:
        uid = renderer.get('id')
        channel_id = find_one(renderer, '$..authorExternalChannelId')
        channel = f'https://www.youtube.com/channel/{channel_id}'
        timestamp = renderer.get('timestampUsec')

        author = find_one(renderer, '$..authorName.simpleText') or '[no author]'
        badges = find_all(renderer, '$..authorBadges..label')
        header = find_one(renderer, '$.primaryText')
        header_text = self.runs_to_text(header) if header else None
        record = YoutubeChatRecord(uid=uid,
                                   action=action_type,
                                   renderer=renderer_type,
                                   author=author,
                                   channel=channel,
                                   timestamp=timestamp,
                                   badges=badges,
                                   message_header=header_text,
                                   )
        return record


    # most of actions and renderers names comes from chat-downloader:
    # https://github.com/xenova/chat-downloader/blob/master/chat_downloader/sites/youtube.py
    parsers: Dict[str, Dict[str, Callable]] = {
        'addChatItemAction': {
            'liveChatViewerEngagementMessageRenderer': drop,
            'liveChatMembershipItemRenderer': parse_chat_renderer,
            'liveChatTextMessageRenderer': parse_chat_renderer,
            'liveChatPaidMessageRenderer': parse_chat_renderer,
            'liveChatPlaceholderItemRenderer': drop,  # placeholder
            'liveChatDonationAnnouncementRenderer': log,
            'liveChatPaidStickerRenderer': parse_chat_renderer,
            'liveChatModeChangeMessageRenderer': log,  # e.g. slow mode enabled
            # Gifting
            'liveChatSponsorshipsGiftPurchaseAnnouncementRenderer': parse_gift_purchase,  # purchase
            'liveChatSponsorshipsGiftRedemptionAnnouncementRenderer': parse_chat_renderer  # receive
        },
        'replaceChatItemAction': {
            'liveChatPlaceholderItemRenderer': drop,
            'liveChatTextMessageRenderer': parse_chat_renderer
        },
        'removeChatItemAction': {
            'banUser': log
        },
        'removeChatItemByAuthorAction': {
            'banUser': log
        },
        'markChatItemsByAuthorAsDeletedAction': {
            'banUser': log  # deletedStateMessage
        },
        'markChatItemAsDeletedAction': {
            'deletedMessage': log  # deletedStateMessage
        },
        'addBannerToLiveChatCommand': {
            'liveChatBannerRenderer': parse_banner
        },
        'removeBannerForLiveChatCommand': {
            'removeBanner': log  # targetActionId
        }
    }

    known_actions = set(parsers.keys())
    known_renderers = set(sum([list(x.keys()) for x in parsers.values()], start=[]))

    def run_parsers(self, actions: Dict[str, list]) -> list:
        records = []
        for action_type, renderers_list in actions.items():
            if not action_type in self.known_actions:
                self.logger.debug(f'action "{action_type}" is not registered, skipping')
                continue
            for renderer_items in renderers_list:
                for _, renderers in renderer_items.items():
                    if not isinstance(renderers, dict):
                        continue
                    for renderer_type, renderer in renderers.items():
                        if not renderer_type in self.known_renderers:
                            self.logger.debug(f'action "{action_type}" has no renderer "{renderer_type}" registered, skipping')
                            continue
                        try:
                            parser = self.parsers[action_type][renderer_type]
                        except (KeyError, TypeError):
                            self.logger.debug(f'no parser for action "{action_type}" and renderer "{renderer_type}"')
                            continue
                        try:
                            record = parser(self, action_type, renderer_type, renderer)
                        except Exception as e:
                            self.logger.debug(f' failed to parse [{action_type}] {renderer_type}: {e}\n{renderer}')
                            continue
                        if record is not None:
                            records.append(record)
        return records


def get_first(url):
    response = requests.get(url)
    response.raise_for_status()
    renderers, data = get_actions(response.text, first_page=True)
    continuation = find_one(data, '$..liveChatRenderer..continuation')
    messages = parse_messages(renderers)
    ...
    return data, messages, continuation


def get_next(initial_data: dict, continuation_token: str):
    URL = 'https://www.youtube.com/youtubei/v1/live_chat/get_live_chat?key=AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8'
    _, headers, post_body = prepare_next_page_request(initial_data, continuation_token)
    response = requests.post(URL, data=json.dumps(post_body), headers=headers)
    renderers, data = get_actions(response.text)
    continuation = find_one(data, '$..liveChatContinuation..continuation')
    timeout = find_one(data, '$..liveChatContinuation..timeoutMs')
    if timeout:
        print(f'[wait {timeout}]', end='')
    messages = parse_messages(renderers)
    ...
    return initial_data, messages, continuation


def get_actions(page: str, first_page=False) -> Tuple[Dict[str, list], dict]:
    keys = ['addChatItemAction', 'replaceChatItemAction', 'removeChatItemAction', 'removeChatItemByAuthorAction', 'markChatItemsByAuthorAsDeletedAction', 'markChatItemAsDeletedAction', 'addBannerToLiveChatCommand', 'removeBannerForLiveChatCommand']
    anchor = 'var ytInitialData = ' if first_page else ''
    try:
        actions, data = extract_keys(page, keys, anchor=anchor)
    except Exception as e:
        return {}, {}
    return actions, data


def parse_messages(livechat_items) -> List[YoutubeChatRecord]:
    messages = Parser().run_parsers(livechat_items)
    return messages
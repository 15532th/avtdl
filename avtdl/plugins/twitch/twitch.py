import abc
import asyncio
import datetime
import json
from textwrap import shorten
from typing import Any, Dict, List, Optional, Sequence

from dateutil import parser as dateutil_parser
from pydantic import Field, FilePath, PositiveFloat, ValidationError

from avtdl.core.config import Plugins
from avtdl.core.interfaces import MAX_REPR_LEN, Record
from avtdl.core.monitors import BaseFeedMonitor, BaseFeedMonitorConfig, BaseFeedMonitorEntity
from avtdl.core.request import DataResponse, Endpoint, HttpClient, RequestDetails
from avtdl.core.runtime import RuntimeContext
from avtdl.core.utils import JSONType, find_all, format_validation_error


@Plugins.register('twitch', Plugins.kind.ASSOCIATED_RECORD)
class TwitchRecord(Record):
    """Represents event of a user going live on Twitch"""
    url: str
    """channel url"""
    username: str
    """username value from configuration entity"""
    title: str
    """stream title"""
    start: datetime.datetime
    """timestamp of the stream start"""
    avatar_url: Optional[str] = None
    """link to the user's avatar"""
    game: Optional[str] = None
    """game name, if present"""

    def __str__(self):
        return f'{self.url}\n{self.title}'

    def __repr__(self):
        title = shorten(self.title, MAX_REPR_LEN)
        return f'TwitchRecord(username={self.username}, title="{title}")'

    def get_uid(self) -> str:
        return self.url

    def as_embed(self) -> dict:
        return {
            'title': self.title,
            'description': self.url,
            'color': None,
            'author': {'name': self.username, 'url': self.url, 'icon_url': self.avatar_url},
            'timestamp': self.start.isoformat(),
            'footer': {'text': self.game},
            'fields': []
        }


@Plugins.register('twitch', Plugins.kind.ACTOR_ENTITY)
class TwitchMonitorEntity(BaseFeedMonitorEntity):
    username: str
    """Twitch username of a monitored channel"""
    update_interval: PositiveFloat = 300
    """how often the user will be checked for being live, in seconds"""
    selected_for_update: bool = Field(exclude=True, default=False)
    """internal variable, used to mark one entity that actually makes network request to update queues"""
    url: str = Field(exclude=True, default='')
    """url is not used, all streams are checked through the same api endpoint"""
    cookies_file: Optional[FilePath] = Field(exclude=True, default=None)
    """cookies are not used to log in"""
    adjust_update_interval: bool = Field(exclude=True, default=True)
    """rplay api endpoints doesn't use caching headers"""


@Plugins.register('twitch', Plugins.kind.ACTOR_CONFIG)
class TwitchMonitorConfig(BaseFeedMonitorConfig):
    pass


@Plugins.register('twitch', Plugins.kind.ACTOR)
class TwitchMonitor(BaseFeedMonitor):
    """
    Monitor for twitch.tv

    Monitors twitch.tv user with given username, produces a record when it goes live.
    For user `https://www.twitch.tv/username` username would be `username`.
    """

    def __init__(self, conf: TwitchMonitorConfig, entities: Sequence[TwitchMonitorEntity], ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)
        self.entities: Mapping[str, TwitchMonitorEntity]  # type: ignore
        self.consumers: Dict[str, asyncio.Queue] = {}
        for entity in self.entities.values():
            self.consumers[entity.name] = asyncio.Queue()

        # new data are only fetched in a single entity with the shortest update interval
        # it allows to avoid creating and monitoring a dedicated task for performing update
        entity_for_update = min(entities, key=lambda entity: entity.update_interval)
        entity_for_update.selected_for_update = True

    async def get_records(self, entity: TwitchMonitorEntity, client: HttpClient) -> Sequence[TwitchRecord]:
        if entity.selected_for_update:
            await self.update_consumers(entity, client)

        records = []
        queue = self.consumers[entity.name]
        while not queue.empty():
            base_stream_info = queue.get_nowait()
            records.extend(await self.get_stream_record(entity, client, base_stream_info))
        return records

    async def _request_gql(self, entity: TwitchMonitorEntity, client: HttpClient, operations: Sequence['Operation']) -> Optional[list]:
        details = GQL.prepare(operations)
        response = await self.request_endpoint(entity, client, details)
        if not isinstance(response, DataResponse) or not response.has_json():
            self.logger.warning(f'update failed: unexpected response. Raw response: {response}')
        results = response.json()
        if not isinstance(results, list):
            self.logger.warning(f'update failed: unexpected response format. Raw response: {results}')
            return None
        if not len(results) == len(operations):
            if not isinstance(results, list):
                self.logger.warning(f'update failed: got {len(results)} responses for {len(operations)} operations. Raw response: {results}')
                return None
        return results

    async def update_consumers(self, entity: TwitchMonitorEntity, client: HttpClient):
        consumers = {entity.username: self.consumers[entity.name] for entity in self.entities.values()}
        operations = [Operations.UseLive(username) for username in consumers.keys()]
        results = await self._request_gql(entity, client, operations)
        if results is None:
            return
        for result, operation, queue in zip(results, operations, consumers.values()):
            if not isinstance(result, dict):
                self.logger.warning(f'skipping unexpected result "{result}"')
                continue
            try:
                base_data = operation.parse(result)
            except Exception as e:
                self.logger.warning(f'failed to parse result: {e}')
                self.logger.debug(f'raw result: {result}')
                continue
            if base_data:
                await queue.put(base_data)

    async def get_stream_record(self, entity: TwitchMonitorEntity, client: HttpClient,
                                base_stream_info: Dict[str, Any]) -> List[TwitchRecord]:
        operations = [
            Operations.UseLiveBroadcast(entity.username),
            Operations.HomeShelfVideos(entity.username)
        ]
        results = await self._request_gql(entity, client, operations)
        if results is None:
            return []
        for result, operation in zip(results, operations):
            if not isinstance(result, dict):
                self.logger.warning(f'[{entity.name}] skipping unexpected result "{result}"')
                continue
            try:
                additional_info = operation.parse(result)
            except Exception as e:
                self.logger.warning(f'failed to parse additional result: {e}')
                self.logger.debug(f'raw result: {result}')
                continue
            base_stream_info.update(additional_info)
        try:
            return [TwitchRecord(**base_stream_info)]
        except ValidationError as e:
            msg = format_validation_error(e, f'[{entity.name}] failed to parse record:')
            self.logger.warning(msg)
            self.logger.debug(f'[{entity.name}] raw data: {base_stream_info}')
            return []


class Operation:

    @abc.abstractmethod
    def payload(self, *args, **kwargs) -> Dict[str, JSONType]:
        """Prepare body of request"""

    @abc.abstractmethod
    def parse(self, response: Dict[str, JSONType]) -> Dict[str, Any]:
        """Parse response, extracting useful values into a dict"""


class Operations:

    class UseLive(Operation):
        """
        If user is live, return stream_id and start date

        stream_id: str
        start: datetime.datetime
        """

        def __init__(self, username: str):
            self.username = username

        def payload(self) -> Dict[str, JSONType]:
            return {
                'operationName': 'UseLive',
                'variables': {'channelLogin': self.username},
                'extensions': {
                    'persistedQuery': {
                        'version': 1,
                        'sha256Hash': '639d5f11bfb8bf3053b424d9ef650d04c4ebb7d94711d644afb08fe9a0fad5d9'
                    }
                }
            }

        def parse(self, response: Dict[str, JSONType]) -> Dict[str, Any]:
            if response is None:
                return {}
            try:
                info: dict = response['data']['user']  # type: ignore
                stream_info = info['stream']
                if stream_info is None:
                    return {}
                stream_id = stream_info['id']
                start_text = stream_info['createdAt']
                start = dateutil_parser.parse(start_text)
            except (TypeError, IndexError, KeyError) as e:
                raise ValueError(f'failed to parse response: {type(e)} {e}')
            return {
                'username': self.username,
                'url': f'https://www.twitch.tv/{self.username}/',
                'stream_id': stream_id,
                'start': start
            }

    class UseLiveBroadcast(Operation):
        """
        Return live title and game category if present

        title: str
        game: Optional[str]
        """

        def __init__(self, username: str):
            self.username = username

        def payload(self) -> Dict[str, JSONType]:
            return {
                'operationName': 'UseLiveBroadcast',
                'variables': {'channelLogin': self.username},
                'extensions': {
                    'persistedQuery': {
                        'version': 1,
                        'sha256Hash': '0b47cc6d8c182acd2e78b81c8ba5414a5a38057f2089b1bbcfa6046aae248bd2'
                    }
                }
            }

        def parse(self, response: Dict[str, JSONType]) -> Dict[str, Any]:
            if response is None:
                return {}
            try:
                stream_info: dict = response['data']['user']['lastBroadcast']  # type: ignore
                title = str(stream_info['title'])
                game_info = stream_info.get('game') or {}
                game = game_info.get('name', None)
            except (TypeError, IndexError, KeyError) as e:
                raise ValueError(f'failed to parse response: {type(e)} {e}')
            return {'title': title, 'game': game}

    class HomeShelfVideos(Operation):
        """
        Return avatar url for given channel

        profileImageURL: str
        """

        def __init__(self, username: str):
            self.username = username

        def payload(self) -> Dict[str, JSONType]:
            return {
                'operationName': 'HomeShelfVideos',
                'variables': {'channelLogin': self.username, 'first': 1},
                'extensions': {
                    'persistedQuery': {
                        'version': 1,
                        'sha256Hash': '376aee6b71edb57fd47b3e3648eb9edbb6ab57bd7e65fe37ee93ae79d1ceb7cc'
                    }
                }
            }

        def parse(self, response: Dict[str, JSONType]) -> Dict[str, Any]:
            if response is None:
                return {}
            broadcasters = find_all(response, '$..broadcaster')
            for data in broadcasters:
                if isinstance(data, dict):
                    if data.get('login') == self.username:
                        avatar_url = data.get('profileImageURL')
                        if avatar_url is not None:
                            return {'profileImageURL': avatar_url}
            return {}


class GQL(Endpoint):

    @staticmethod
    def prepare(operations: Sequence[Operation]) -> RequestDetails:
        api_url = 'https://gql.twitch.tv/gql'
        headers = {'Client-Id': 'kimne78kx3ncx6brgo4mv6wki5h1ko', 'Content-Type': 'application/json'}
        body = [operation.payload() for operation in operations]
        return RequestDetails(url=api_url, method='POST', data=json.dumps(body, ensure_ascii=False), headers=headers)

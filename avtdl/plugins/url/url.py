#!/usr/bin/env python3
import datetime
from enum import Enum
from typing import Callable, Dict, List, Sequence

from avtdl.core.config import Plugins
from avtdl.core.interfaces import Event, Record, TextRecord
from avtdl.core.monitors import BaseFeedMonitor, BaseFeedMonitorConfig, BaseFeedMonitorEntity
from avtdl.core.request import DataResponse, HttpClient, HttpResponse
from avtdl.core.utils import sha1, utcnow

Plugins.register('get_url', Plugins.kind.ASSOCIATED_RECORD)(TextRecord)


@Plugins.register('get_url', Plugins.kind.ASSOCIATED_RECORD)
class UrlStatusRecord(TextRecord):
    url: str
    """url of the monitored page"""
    text: str
    """content of the web page. Might be replaced with empty string to save space"""
    text_hash: str
    """hash of the web page content. On network error hash of the last successfully fetched page is used"""
    accessed_at: datetime.datetime
    """time the url was fetched at"""
    ok: bool
    """set to True if HTTP request completed successfully"""
    status: int
    """HTTP response status. Special value 0 means network connection failed"""
    reason: str
    """short text explaining status code provided by the server or error if network connection failed"""
    headers: Dict[str, str] = {}
    """response headers"""

    def get_uid(self) -> str:
        return self.url

    def __str__(self) -> str:
        return repr(self)

    def __repr__(self) -> str:
        return f'UrlStatusRecord(url="{self.url}", accessed_at="{self.accessed_at}", status="{self.status}", reason="{self.reason}", text_hash="{self.text_hash}")'


@Plugins.register('get_url', Plugins.kind.ACTOR_CONFIG)
class UrlMonitorConfig(BaseFeedMonitorConfig):
    pass


@Plugins.register('get_url', Plugins.kind.ACTOR_ENTITY)
class UrlMonitorEntity(BaseFeedMonitorEntity):
    url: str
    """url to monitor"""
    store_text: bool = True
    """store page content in the "text" field of the record"""
    report_server_error: bool = False
    """emit an event if error status has been received in HTTP response, as well as when the url started responding with 2xx again"""
    report_network_error: bool = False
    """emit an event if a network error has occurred and when the request succeeded again after the network outage"""


@Plugins.register('get_url', Plugins.kind.ACTOR)
class UrlMonitor(BaseFeedMonitor):
    """
    Monitor web page state

    Download contents of web page at `url` and emit it as an `UrlStatusRecord`
    if it has changed since the last update. Intended to be used with simple
    text endpoints.
    """

    async def get_records(self, entity: UrlMonitorEntity, client: HttpClient) -> Sequence[UrlStatusRecord]:
        response = await self.request_raw(entity.url, entity, client)
        record = UrlStatusRecord(
            url=entity.url,
            text=(response.text or '') if entity.store_text else '',
            text_hash=sha1(response.text) if isinstance(response, DataResponse) else '',
            accessed_at=utcnow(),
            ok=response.ok,
            status=response.status,
            reason=response.reason if isinstance(response, HttpResponse) else str(response.e),
            headers={k: v for k, v in response.headers.items()} if response.headers is not None else {}
        )
        return [record]

    def filter_new_records(self, records: Sequence[UrlStatusRecord], entity: UrlMonitorEntity) -> Sequence[Record]:
        new_records: List[Record] = []
        for record in records:
            new_records.extend(self.filter_single_record(record, entity))
        return new_records

    def filter_single_record(self, record: UrlStatusRecord, entity: UrlMonitorEntity) -> Sequence[Record]:
        new_records: List[Record] = []
        stored_record = self.load_record(record, entity)
        if stored_record is None:
            self.logger.debug(
                f'[{entity.name}] fetched record is new: "{record.get_uid()}" (hash: {record.hash()[:5]})')
            return [record]
        if not isinstance(stored_record, UrlStatusRecord):
            self.logger.warning(f'[{entity.name} loaded record has unexpected type: {stored_record!r}')
            return [record]
        status = f'{record.status} {record.reason}'
        stored_status = f'{stored_record.status} {stored_record.reason}'
        if record.ok:
            if record.text_hash != stored_record.text_hash:
                self.store_records([record], entity)
                new_records.append(record)
                self.logger.debug(
                    f'[{entity.name}] storing new version of record "{record.get_uid()}" (hash: {record.hash()[:5]})')
            if not stored_record.ok:
                if stored_record.status == 0:
                    if entity.report_network_error:
                        event = Event(event_type='status',
                                      text=f'{record.url} is back online with {status} after network outage')
                        new_records.append(event)
                else:
                    if entity.report_server_error:
                        event = Event(event_type='status',
                                      text=f'{record.url} responded with {status} after {stored_status}')
                        new_records.append(event)
        else:
            record.text_hash = stored_record.text_hash
            if record.status == 0 and stored_record.status != 0:
                if entity.report_network_error:
                    event = Event(event_type='status',
                                  text=f'got network error requesting {record.url}: {record.reason}')
                    new_records.append(event)
            elif record.status != 0 and stored_record.status == 0:
                if entity.report_network_error:
                    event = Event(event_type='status',
                                  text=f'{record.url} is back online with {status} after network outage')
                    new_records.append(event)
            elif record.status != stored_record.status:
                if entity.report_server_error:
                    event = Event(event_type='status',
                                  text=f'{record.url} responded with {status} after {stored_status}')
                    new_records.append(event)
        return new_records

    def filter_single_record_a(self, record: UrlStatusRecord, entity: UrlMonitorEntity) -> Sequence[Record]:
        def store_new(record: UrlStatusRecord, stored_record: UrlStatusRecord) -> Sequence[Record]:
            if record.text_hash != stored_record.text_hash:
                self.store_records([record], entity)
                return [record]
            return []

        def copy_hash(record: UrlStatusRecord, stored_record: UrlStatusRecord) -> Sequence:
            record.text_hash = stored_record.text_hash
            return []

        def make_event(condition: bool, msg: str) -> Callable[[UrlStatusRecord, UrlStatusRecord], Sequence[Event]]:
            def event_factory(*_) -> Sequence[Event]:
                return [Event(event_type='status', text=msg)] if condition else []

            return event_factory

        class UrlStatus(str, Enum):
            ok = 'OK'
            bad = 'BAD'
            error = 'NETWORK ERROR'

        def record_status(record: UrlStatusRecord) -> UrlStatus:
            if record.ok:
                return UrlStatus.ok
            elif record.status == 0:
                return UrlStatus.error
            else:
                return UrlStatus.bad

        stored_record = self.load_record(record, entity)
        if stored_record is None:
            self.logger.debug(
                f'[{entity.name}] fetched record is new: "{record.get_uid()}" (hash: {record.hash()[:5]})')
            return [record]
        if not isinstance(stored_record, UrlStatusRecord):
            self.logger.warning(f'[{entity.name} loaded record has unexpected type: {stored_record!r}')
            return [record]
        status = f'{record.status} {record.reason}'
        stored_status = f'{stored_record.status} {stored_record.reason}'
        # mapping of record state to stored_record state pairs to actions to be taken
        actions: Dict[
            UrlStatus, Dict[UrlStatus, List[Callable[[UrlStatusRecord, UrlStatusRecord], Sequence[Record]]]]] = {
            UrlStatus.ok: {
                UrlStatus.ok: [store_new],
                UrlStatus.bad: [
                    store_new,
                    make_event(entity.report_server_error,
                               f'{record.url} responded with {status} after {stored_status}')
                ],
                UrlStatus.error: [
                    store_new,
                    make_event(entity.report_network_error,
                               f'{record.url} is back online with {status} after network outage')
                ],
            },
            UrlStatus.bad: {
                UrlStatus.ok: [
                    copy_hash,
                    make_event(entity.report_server_error,
                               f'{record.url} responded with {status} after {stored_status}')
                ],
                UrlStatus.bad: [
                    copy_hash,
                    make_event(entity.report_server_error and record.status != stored_record.status,
                               f'{record.url} responded with {status} after {stored_status}')
                ],
                UrlStatus.error: [
                    copy_hash,
                    make_event(entity.report_network_error,
                               f'{record.url} is back online with {status} after network outage'),
                    make_event(entity.report_server_error,
                               f'{record.url} responded with {status} after network outage')
                ],
            },
            UrlStatus.error: {
                UrlStatus.ok: [
                    copy_hash,
                    make_event(entity.report_network_error,
                               f'got network error requesting {record.url}: {record.reason}')
                ],
                UrlStatus.bad: [
                    copy_hash,
                    make_event(entity.report_network_error,
                               f'got network error requesting {record.url}: {record.reason}')
                ],
                UrlStatus.error: [copy_hash],
            },
        }
        action_sequence = actions[record_status(record)][record_status(stored_record)]
        new_records: List[Record] = []
        for action in action_sequence:
            new_records.extend(action(record, stored_record))
        return new_records

    def filter_single_record_b(self, record: UrlStatusRecord, entity: UrlMonitorEntity) -> Sequence[Record]:
        new_records: List[Record] = []
        stored_record = self.load_record(record, entity)
        if stored_record is None:
            self.logger.debug(
                f'[{entity.name}] fetched record is new: "{record.get_uid()}" (hash: {record.hash()[:5]})')
            return [record]
        if not isinstance(stored_record, UrlStatusRecord):
            self.logger.warning(f'[{entity.name} loaded record has unexpected type: {stored_record!r}')
            return [record]

        class UrlStatus(str, Enum):
            ok = 'OK'
            bad = 'BAD'
            error = 'NETWORK ERROR'

        def record_status(record: UrlStatusRecord) -> UrlStatus:
            if record.ok:
                return UrlStatus.ok
            elif record.status == 0:
                return UrlStatus.error
            else:
                return UrlStatus.bad

        def add_event(condition: bool, msg: str):
            if condition:
                new_records.append(Event(event_type='status', text=msg))

        status = f'{record.status} {record.reason}'
        stored_status = f'{stored_record.status} {stored_record.reason}'

        if record_status(record) == UrlStatus.ok:
            if record.text_hash != stored_record.text_hash:
                self.store_records([record], entity)
                new_records.append(record)
                self.logger.debug(
                    f'[{entity.name}] storing new version of record "{record.get_uid()}" (hash: {record.hash()[:5]})')

            if record_status(stored_record) == UrlStatus.ok:
                pass
            elif record_status(stored_record) == UrlStatus.bad:
                add_event(entity.report_network_error,
                          f'{record.url} is back online with {status} after network outage')
            elif record_status(stored_record) == UrlStatus.error:
                add_event(entity.report_server_error, f'{record.url} responded with {status} after {stored_status}')

        elif record_status(record) == UrlStatus.bad:
            record.text_hash = stored_record.text_hash

            if record_status(stored_record) == UrlStatus.ok:
                add_event(entity.report_server_error, f'{record.url} responded with {status} after {stored_status}')
            elif record_status(stored_record) == UrlStatus.bad:
                add_event(entity.report_server_error and record.status != stored_record.status,
                          f'{record.url} responded with {status} after {stored_status}')
            elif record_status(stored_record) == UrlStatus.error:
                add_event(entity.report_network_error,
                          f'{record.url} is back online with {status} after network outage'),
                add_event(entity.report_server_error,
                          f'{record.url} responded with {status} after network outage')

        elif record_status(record) == UrlStatus.error:
            record.text_hash = stored_record.text_hash

            if record_status(stored_record) == UrlStatus.ok:
                add_event(entity.report_network_error, f'got network error requesting {record.url}: {record.reason}')
            elif record_status(stored_record) == UrlStatus.bad:
                add_event(entity.report_network_error, f'got network error requesting {record.url}: {record.reason}')
            elif record_status(stored_record) == UrlStatus.error:
                pass

        return new_records

import datetime
from typing import List

import pytest

from avtdl.core.interfaces import Event, EventType, TextRecord
from avtdl.plugins.filters.filters import EmptyFilterConfig, EmptyFilterEntity, EventCauseFilter, FormatEventFilter, FormatEventFilterEntity, MatchFilter, MatchFilterEntity
from avtdl.plugins.twitch.twitch import TwitchRecord


@pytest.fixture()
def empty_config():
    return EmptyFilterConfig(name='test')


@pytest.fixture()
def empty_entity():
    return EmptyFilterEntity(name='test')


@pytest.fixture()
def text_record():
    return TextRecord(text='test')


@pytest.fixture()
def event_record(text_record):
    return Event(event_type=EventType.error, text='test event', record=text_record)


class TestEventCause:

    @staticmethod
    @pytest.fixture()
    def cause_filter(empty_config, empty_entity):
        return EventCauseFilter(config=empty_config, entities=[empty_entity])

    @staticmethod
    def test_match(cause_filter, empty_entity, event_record):
        result = cause_filter.match(empty_entity, event_record)
        assert result == event_record.record

    @staticmethod
    def test_passthrough(cause_filter, empty_entity, text_record):
        assert cause_filter.match(empty_entity, text_record) == text_record


class TestFormatEvent:

    @staticmethod
    def prepare_filter():
        empty_config = EmptyFilterConfig(name='test')
        entity = FormatEventFilterEntity(name='test', type_template='error', text_template='Event: *{text}*')
        return FormatEventFilter(config=empty_config, entities=[entity]), entity

    def test_match(self, text_record):
        format_event_filter, entity = self.prepare_filter()
        result = format_event_filter.match(entity, text_record)
        assert isinstance(result, Event)
        assert result.record == text_record
        assert result.event_type == 'error'
        assert result.text == 'Event: *test*'


class TestMatchFilter:

    @staticmethod
    def prepare_filter(patterns: List[str], fields: List[str]):
        config = EmptyFilterConfig(name='test')
        entity = MatchFilterEntity(name='test', patterns=patterns, fields=fields)
        return MatchFilter(config, [entity]), entity

    def test_empty_field_matches(self):
        record = TwitchRecord(
            url='https://twitch.tv/test',
            username='test',
            title='test',
            start=datetime.datetime.fromtimestamp(0),
            avatar_url='https://example.com/'
        )
        match_filter, entity = self.prepare_filter([''], ['game'])

        result = match_filter.match(entity, record)
        assert result == record

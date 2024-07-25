import datetime
from typing import List, Optional

import pytest

from avtdl.core.interfaces import Event, EventType, TextRecord
from avtdl.plugins.filters.filters import EmptyFilterConfig, EmptyFilterEntity, EventCauseFilter, FormatFilter, \
    FormatFilterEntity, MatchFilter, MatchFilterEntity
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


class TestFormatFilter:

    @staticmethod
    @pytest.fixture()
    def text_record():
        return TextRecord(text='test text message')

    @staticmethod
    def prepare_filter(template: str, missing: Optional[str] = None):
        config = EmptyFilterConfig(name='test')
        entity = FormatFilterEntity(name='test', template=template, missing=missing)
        return FormatFilter(config, [entity]), entity

    def test_single_field(self, text_record):
        fmt, entity = self.prepare_filter('*** {text} ***')

        result = fmt.match(entity, text_record)

        assert result.text == '*** test text message ***'

    def test_missing(self, text_record):
        fmt, entity = self.prepare_filter('*** {url} ***', missing='---')

        result = fmt.match(entity, text_record)

        assert result.text == '*** --- ***'

    def test_missing_disabled(self, text_record):
        fmt, entity = self.prepare_filter('*** {url} ***', missing=None)

        result = fmt.match(entity, text_record)

        assert result.text == '*** {url} ***'

    def test_empty_placeholder(self, text_record):
        fmt, entity = self.prepare_filter('*** {} ***', missing=None)

        result = fmt.match(entity, text_record)

        assert result.text == '*** {} ***'

    def test_nested_placeholder(self, text_record):
        fmt, entity = self.prepare_filter('*** {{text}} ***', missing=None)

        result = fmt.match(entity, text_record)

        assert result.text == '*** {test text message} ***'

    def test_escaped_placeholder(self, text_record):
        fmt, entity = self.prepare_filter('*** \{text\}=\{{text}\} ***', missing=None)

        result = fmt.match(entity, text_record)

        assert result.text == '*** {text}={test text message} ***'


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

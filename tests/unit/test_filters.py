import pytest

from avtdl.core.interfaces import Event, EventType, TextRecord
from avtdl.plugins.filters.filters import EmptyFilterConfig, EmptyFilterEntity, EventCauseFilter


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

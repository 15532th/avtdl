import datetime
from typing import Optional

import pytest

from avtdl.core.interfaces import TextRecord
from avtdl.plugins.filters.filters import EmptyFilterConfig, FormatFilter, FormatFilterEntity


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
        fmt, entity = self.prepare_filter('*** {url} ***')

        result = fmt.match(entity, text_record)

        assert result.text == '*** {url} ***'

    def test_empty_placeholder(self, text_record):
        fmt, entity = self.prepare_filter('*** {} ***')

        result = fmt.match(entity, text_record)

        assert result.text == '*** {} ***'

    def test_nested_placeholder(self, text_record):
        fmt, entity = self.prepare_filter('*** {{text}} ***')

        result = fmt.match(entity, text_record)

        assert result.text == '*** {test text message} ***'

    def test_escaped_placeholder(self, text_record):
        fmt, entity = self.prepare_filter('*** \{text\}=\{{text}\} ***')

        result = fmt.match(entity, text_record)

        assert result.text == '*** {text}={test text message} ***'

    def test_unpaired_braces(self, text_record):
        fmt, entity = self.prepare_filter('}{ {text} }{')

        result = fmt.match(entity, text_record)

        assert result.text == '}{ test text message }{'

    def test_datetime(self, text_record):
        fmt, entity = self.prepare_filter('[%Y-%m-%d] {text}')
        now = datetime.datetime.now()
        result = fmt.match(entity, text_record)

        assert result.text == f'[{now.year}-{now.month:02}-{now.day:02}] test text message'

import datetime

import pytest

from avtdl.core.interfaces import TextRecord
from avtdl.plugins.twitter.extractors import TwitterRecord
from avtdl.plugins.twitter.filters import TwitterFilter, TwitterFilterConfig, TwitterFilterEntity


@pytest.fixture()
def text_record():
    return TextRecord(text='test')


@pytest.fixture()
def config():
    return TwitterFilterConfig(name='test')


@pytest.fixture()
def empty_entity():
    return TwitterFilterEntity(name='test')


@pytest.fixture()
def regular_tweet():
    return TwitterRecord(
        uid='0',
        url='https://twitter.com/alice/0',
        author='Alice',
        username='alice',
        published=datetime.datetime.fromisoformat('2022-01-01 01:00'),
        text='test tweet'
    )


@pytest.fixture()
def retweet(regular_tweet):
    return TwitterRecord(
        uid='0',
        url='https://twitter.com/alice/0',
        author='Bob',
        username='bob',
        published=datetime.datetime.fromisoformat('2022-01-01 02:00'),
        text='test tweet',
        retweet=regular_tweet
    )


@pytest.fixture()
def reply(regular_tweet):
    return TwitterRecord(
        uid='1',
        url='https://twitter.com/eve/1',
        author='Eve',
        username='eve',
        published=datetime.datetime.fromisoformat('2022-01-01 03:00'),
        text='test reply',
        replying_to_username='alice'
    )


class TestPassthrough:

    @staticmethod
    def test_passthrough_pick(config, text_record):
        entity = TwitterFilterEntity(name='test', regular_tweet=True, reversed=False)
        filtr = TwitterFilter(config, [entity])

        assert filtr.match(entity, text_record) == text_record

    @staticmethod
    def test_passthrough_drop(config, text_record):
        entity = TwitterFilterEntity(name='test', regular_tweet=True, reversed=True)
        filtr = TwitterFilter(config, [entity])

        assert filtr.match(entity, text_record) == text_record


class TestStraight:

    @staticmethod
    def test_pick_single_condition(config, regular_tweet):
        entity = TwitterFilterEntity(name='test', regular_tweet=True)
        filtr = TwitterFilter(config, [entity])

        assert filtr.match(entity, regular_tweet) == regular_tweet

    @staticmethod
    def test_drop_single_condition(config, retweet):
        entity = TwitterFilterEntity(name='test', regular_tweet=True)
        filtr = TwitterFilter(config, [entity])

        assert filtr.match(entity, retweet) is None

    @staticmethod
    def test_pick_multiple_conditions(config, regular_tweet):
        entity = TwitterFilterEntity(name='test', regular_tweet=True, quote=True)
        filtr = TwitterFilter(config, [entity])

        assert filtr.match(entity, regular_tweet) == regular_tweet

    @staticmethod
    def test_pick_username(config, regular_tweet):
        entity = TwitterFilterEntity(name='test', username='alice')
        filtr = TwitterFilter(config, [entity])

        assert filtr.match(entity, regular_tweet) == regular_tweet

    @staticmethod
    def test_pick_author_name(config, regular_tweet):
        entity = TwitterFilterEntity(name='test', author='Alice')
        filtr = TwitterFilter(config, [entity])

        assert filtr.match(entity, regular_tweet) == regular_tweet

    @staticmethod
    def test_drop_retweet_name(config, regular_tweet):
        entity = TwitterFilterEntity(name='test', author='Bob')
        filtr = TwitterFilter(config, [entity])

        assert filtr.match(entity, regular_tweet) is None


class TestReversed:

    @staticmethod
    def test_pick_single_condition(config, reply):
        entity = TwitterFilterEntity(name='test', regular_tweet=True, reversed=True)
        filtr = TwitterFilter(config, [entity])

        assert filtr.match(entity, reply) is reply

    @staticmethod
    def test_drop_single_condition(config, retweet):
        entity = TwitterFilterEntity(name='test', retweet=True, reversed=True)
        filtr = TwitterFilter(config, [entity])

        assert filtr.match(entity, retweet) is None

    @staticmethod
    def test_pick_multiple_conditions(config, regular_tweet):
        entity = TwitterFilterEntity(name='test', reply=True, retweet=True, quote=True, reversed=True)
        filtr = TwitterFilter(config, [entity])

        assert filtr.match(entity, regular_tweet) is regular_tweet

    @staticmethod
    def test_drop_multiple_conditions(config, regular_tweet):
        entity = TwitterFilterEntity(name='test', regular_tweet=True, quote=True, reversed=True)
        filtr = TwitterFilter(config, [entity])

        assert filtr.match(entity, regular_tweet) is None

    @staticmethod
    def test_drop_username(config, regular_tweet):
        entity = TwitterFilterEntity(name='test', username='alice', reversed=True)
        filtr = TwitterFilter(config, [entity])

        assert filtr.match(entity, regular_tweet) is None

from typing import Optional, Sequence

from avtdl.core.interfaces import Filter, FilterEntity, Record
from avtdl.core.plugins import Plugins
from avtdl.plugins.filters.filters import EmptyFilterConfig
from avtdl.plugins.twitter.extractors import TwitterRecord


@Plugins.register('filter.twitter', Plugins.kind.ACTOR_CONFIG)
class TwitterFilterConfig(EmptyFilterConfig):
    pass


@Plugins.register('filter.twitter', Plugins.kind.ACTOR_ENTITY)
class TwitterFilterEntity(FilterEntity):
    retweet: bool = False
    """match retweets"""
    reply: bool = False
    """match replies"""
    quote: bool = False
    """match quotes"""
    regular_tweet: bool = False
    """match regular tweets that are not a retweet, reply or quote"""
    author: Optional[str] = None
    """match if a given string is a part of the name of the author of the tweet"""
    username: Optional[str] = None
    """match if a given string is a part of tweet author's username (without the "@" symbol)"""
    reversed: bool = False
    """drop record instead of letting it through if any of the properties matches"""


@Plugins.register('filter.twitter', Plugins.kind.ACTOR)
class TwitterFilter(Filter):
    """
    Filter `TwitterRecord` with specified properties

    Lets through `TwitterRecord` if it matches any of specified criteria.
    Enabling `reversed` setting reverses the conditions, making record match
    if none of the criteria apply.

    All records from other sources pass through without filtering.
    """

    def __init__(self, config: TwitterFilterConfig, entities: Sequence[TwitterFilterEntity]):
        super().__init__(config, entities)

    def match(self, entity: FilterEntity, record: Record) -> Optional[Record]:
        assert isinstance(entity, TwitterFilterEntity)
        if not isinstance(record, TwitterRecord):
            self.logger.debug(f'[{entity.name}] record is not a TwitterRecord, letting through: {record!r}')
            return record
        matches = entity_matches(record, entity)
        if (matches and not entity.reversed) or (not matches and entity.reversed):
            return record
        else:
            return None


def is_retweet(record: TwitterRecord) -> bool:
    return record.retweet is not None


def is_reply(record: TwitterRecord) -> bool:
    return record.replying_to_username is not None


def is_quote(record: TwitterRecord) -> bool:
    return record.quote is not None


def is_regular_tweet(record: TwitterRecord) -> bool:
    return all([x is None for x in [record.replying_to_username, record.retweet, record.quote]])


def author_matches(record: TwitterRecord, name: str) -> bool:
    return record.author.find(name) > -1


def username_matches(record: TwitterRecord, name: str) -> bool:
    return record.username.find(name) > -1


def entity_matches(record: TwitterRecord, entity: TwitterFilterEntity) -> bool:
    return any([
        entity.retweet and is_retweet(record),
        entity.reply and is_reply(record),
        entity.quote and is_quote(record),
        entity.regular_tweet and is_regular_tweet(record),
        entity.author and author_matches(record, entity.author),
        entity.username and username_matches(record, entity.username)
    ])

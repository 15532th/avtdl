from typing import List

from core.interfaces import Filter, Record
from core.config import Plugins

@Plugins.register('noop', Plugins.kind.FILTER)
class NoopFilter(Filter):
    name: str

    def match(self, record: Record):
        return record

@Plugins.register('match', Plugins.kind.FILTER)
class MatchFilter(Filter):
    name: str
    patterns: List[str]

    def match(self, record: Record):
        for pattern in self.patterns:
            if str(record).find(pattern) > -1:
                return record
        return None

@Plugins.register('exclude', Plugins.kind.FILTER)
class ExcludeFilter(Filter):
    name: str
    patterns: List[str]

    def match(self, record: Record):
        for pattern in self.patterns:
            if str(record).find(pattern) > -1:
                return None
        return record

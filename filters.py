from typing import List

from interfaces import Filter, Record

class NoopFilter(Filter):
    def match(self, record: Record):
        return record

class MatchFilter(Filter):

    def __init__(self, patterns: List[str]):
        self.patterns = patterns

    def match(self, record: Record):
        for pattern in self.patterns:
            if str(record).find(pattern) == -1:
                return None
        return record

from dataclasses import dataclass
from typing import List

from interfaces import Filter, Record

class NoopFilter(Filter):
    def match(self, record: Record):
        return record

@dataclass
class MatchFilter(Filter):
    name: str
    patterns: List[str]

    def match(self, record: Record):
        for pattern in self.patterns:
            if str(record).find(pattern) == -1:
                return None
        return record

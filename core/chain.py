import logging
from typing import List, Optional, Tuple

from core.interfaces import Action, Filter, Monitor, Record

class Chain:
    def __init__(self,
                 monitors: List[Tuple[Monitor, List[str]]],
                 actions: List[Tuple[Action, List[str]]],
                 filters: Optional[List[Filter]] = None,
                 name: str = "ChainX"):
        self.name = name
        self.monitors = monitors
        self.actions = actions
        self.filters = filters or []
        for monitor, monitor_entities in monitors:
            for monitor_entity in monitor_entities:
                monitor.register(monitor_entity, self.handle)

    def filter(self, record: Record):
        unfiltered_record = record
        for f in self.filters:
            record = f.match(record)
            if record is None:
                logging.debug(f'chain {self.name}: record {unfiltered_record} dropped on filter {f}')
                break
        return record

    def handle(self, record: Record):
        record = self.filter(record)
        if record is None:
            return
        for action, action_entities in self.actions:
            for action_entity_name in action_entities:
                action.handle(action_entity_name, record)

    def _pformat(self, section):
        f = []
        for item in section:
            item_instance, item_entries = item
            item_name = item_instance.__class__.__name__
            f.append(f'{item_name}, {item_entries}')
            return ', '.join(f)

    def __repr__(self):
        m = self._pformat(self.monitors)
        a = self._pformat(self.actions)
        return f'Chain("{self.name}", {m}, {self.filters!r}, {a})'

import logging
from typing import List, Optional, Dict

from core.interfaces import Action, Filter, Monitor, Record, MessageBus

class Chain:
    def __init__(self,
                 name: str,
                 bus: MessageBus,
                 filters: Optional[List[Filter]],
                 monitors: Dict[str, List[str]],
                 actions: Dict[str, List[str]],
                 events: Dict[str, Dict[str, List[str]]] = {}
                 ):
        self.name = name
        self.bus = bus
        self.monitors = monitors
        self.actions = actions
        self.filters = filters or []
        self.events = events

        for monitor, monitor_entities in monitors.items():
            for monitor_entity in monitor_entities:
                topic = self.bus.message_topic_for(monitor, monitor_entity)
                self.bus.sub(topic, self.handle)

        for event_type in self.events.keys():
            for action, action_entities in self.actions.items():
                for entity_name in action_entities:
                    event_topic = self.bus.event_topic_for(event_type, action, entity_name)
                    self.bus.sub(event_topic, self.handle_event)

    def filter(self, record: Record):
        unfiltered_record = record
        for f in self.filters:
            record = f.match(record)
            if record is None:
                logging.debug(f'chain {self.name}: record "{unfiltered_record}" dropped on filter {f}')
                break
        return record

    def handle(self, _: str, record: Record):
        record = self.filter(record)
        if record is None:
            return

        for action, action_entities in self.actions.items():
            for entity_name in action_entities:
                action_topic = self.bus.message_topic_for(action, entity_name)
                self.bus.pub(action_topic, record)

    def handle_event(self, topic, record: Record):
        event_type, event_action, event_action_entity = self.bus.split_event_topic(topic)
        for action, action_entities in self.events.get(event_type, {}).items():
            for entity in action_entities:
                if action == event_action and entity == event_action_entity:
                    continue
                topic = self.bus.message_topic_for(action, entity)
                self.bus.pub(topic, record)

    def __repr__(self):
        return f'Chain("{self.name}", {self.monitors}, {self.filters!r}, {self.actions})'

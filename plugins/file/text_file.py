#!/usr/bin/env python3

import asyncio
import logging
import os
from typing import Dict, List, Sequence
from pathlib import Path

from core.interfaces import TaskMonitor, TaskMonitorEntity, MonitorConfig
from core.interfaces import Action, ActionEntity, ActionConfig, Record, Event
from core.config import Plugins


class TextRecord(Record):
    def __str__(self):
        return self.title

@Plugins.register('from_file', Plugins.kind.MONITOR_CONFIG)
class FileMonitorConfig(MonitorConfig):
    pass

@Plugins.register('from_file', Plugins.kind.MONITOR_ENTITY)
class FileMonitorEntity(TaskMonitorEntity):
    name: str
    update_interval: float
    path: Path
    split_lines: bool = False
    mtime: float = -1

    def __post_init__(self):
        self.path = Path(self.path)

    def exists(self) -> bool:
        if not self.path.exists():
            self.mtime = -1
            return False
        else:
            return True

    def changed(self) -> bool:
        if not self.exists():
            return False
        current_mtime = os.stat(self.path).st_mtime
        if current_mtime == self.mtime:
            return False
        else:
            self.mtime = current_mtime
            return True

    def get_records(self) -> List[TextRecord]:
        records = []
        if self.exists():
            with open(self.path, 'rt') as fp:
                if self.split_lines:
                    lines = fp.readlines()
                else:
                    lines = [fp.read()]
                for line in lines:
                    record = TextRecord(title=line.strip(), url=str(self.path))
                    records.append(record)
        return records

    def get_new_records(self) -> List[TextRecord]:
        return self.get_records() if self.changed() else []


@Plugins.register('from_file', Plugins.kind.MONITOR)
class FileMonitor(TaskMonitor):

    async def get_new_records(self, entity: TaskMonitorEntity):
        return entity.get_new_records()


@Plugins.register('to_file', Plugins.kind.ACTION_CONFIG)
class FileActionConfig(ActionConfig):
    pass

@Plugins.register('to_file', Plugins.kind.ACTION_ENTITY)
class FileActionEntity(ActionEntity):
    path: Path

@Plugins.register('to_file', Plugins.kind.ACTION)
class FileAction(Action):

    def handle(self, entity_name: str, record: Record):
        entity = self.entities[entity_name]
        try:
            with open(entity.path, 'at') as fp:
                fp.write(str(record) + '\n')
        except Exception as e:
            message = f'error in {self.conf.name}.{entity_name}: {e}'
            self.on_event(Event.error, entity_name, TextRecord(title=message, url=record.url))

    async def run(self):
        return

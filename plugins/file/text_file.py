#!/usr/bin/env python3

import asyncio
import logging
from dataclasses import dataclass
import os
from typing import Dict, List, Sequence
from pathlib import Path

from core.interfaces import Monitor, MonitorEntity, MonitorConfig
from core.interfaces import Action, ActionEntity, ActionConfig, Record, Event
from core.config import Plugins


class TextRecord(Record):
    def __str__(self):
        return self.title

@Plugins.register('from_file', Plugins.kind.MONITOR_CONFIG)
@dataclass
class FileMonitorConfig(MonitorConfig):
    pass

@Plugins.register('from_file', Plugins.kind.MONITOR_ENTITY)
class FileMonitorEntity(MonitorEntity):

    def __init__(self, name: str, path: str, poll_interval: int = 1, split_lines: bool = False):
        super().__init__(name)
        self.path = Path(path)
        self.split_lines = split_lines
        self.poll_interval = poll_interval
        self.mtime: float = -1

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
                    record = TextRecord(line.strip(), str(self.path))
                    records.append(record)
        return records

    def get_new_records(self) -> List[TextRecord]:
        return self.get_records() if self.changed() else []


@Plugins.register('from_file', Plugins.kind.MONITOR)
class FileMonitor(Monitor):

    def __init__(self, conf: FileMonitorConfig,
                 entities: Sequence[FileMonitorEntity]):
        super().__init__(conf, entities)
        self.tasks: Dict[str, asyncio.Task] = {}

    async def run(self):
        for name, entity in self.entities.items():
            self.tasks[name] = asyncio.create_task(self.run_for(entity),
                                                   name=f'from_file:{entity.name}')
        await asyncio.Future()

    async def run_for(self, entity: FileMonitorEntity):
        while True:
            try:
                records = entity.get_new_records()
            except Exception:
                logging.exception(f'task for entity {entity} failed')
                break
            for record in records:
                self.on_record(entity.name, record)
            await asyncio.sleep(entity.poll_interval)


@Plugins.register('to_file', Plugins.kind.ACTION_CONFIG)
@dataclass
class FileActionConfig(ActionConfig):
    pass

@Plugins.register('to_file', Plugins.kind.ACTION_ENTITY)
@dataclass
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
            self.on_event(Event.error, entity_name, TextRecord(message, record.url))

    async def run(self):
        return

#!/usr/bin/env python3

import asyncio
import logging
from dataclasses import dataclass
import os
from typing import Dict, List, Sequence
from pathlib import Path

from ..core.interfaces import Monitor, MonitorEntity, MonitorConfig
from ..core.interfaces import Action, ActionEntity, ActionConfig, Record


class TextRecord(Record):
    def __str__(self):
        return self.title


@dataclass
class FileMonitorConfig(MonitorConfig):
    pass

class FileMonitorEntity(MonitorEntity):

    def __init__(self, name: str, path: str, poll_interval: int = 1, skip_existing_lines: bool = False):
        super().__init__(name)
        self.path = Path(path)
        self.poll_interval = poll_interval
        self.position: int = 0
        self.mtime: float = -1
        if skip_existing_lines:
            self.get_new_records()

    def exists(self) -> bool:
        if not self.path.exists():
            self.mtime = -1
            self.position = 0
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
                fp.seek(self.position)
                for line in fp.readlines():
                    record = TextRecord(line, str(self.path))
                    records.append(record)
                self.position = fp.tell()
        return records

    def get_new_records(self) -> List[TextRecord]:
        return self.get_records() if self.changed() else []


class FileMonitor(Monitor):

    def __init__(self, conf: FileMonitorConfig,
                 entities: Sequence[FileMonitorEntity]):
        super().__init__(conf, entities)
        self.tasks: Dict[str, asyncio.Task] = {}

    async def run(self):
        for name, entity in self.entities.items():
            self.tasks[name] = asyncio.create_task(self.run_for(entity),
                                                   name=f'file:{entity.name}')
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


@dataclass
class FileActionConfig(ActionConfig):
    pass

@dataclass
class FileActionEntity(ActionEntity):
    path: Path

class FileAction(Action):

    def handle(self, entity_name: str, record: Record):
        entity = self.entities[entity_name]
        with open(entity.path, 'at') as fp:
            fp.write(str(record))

    async def run(self):
        return

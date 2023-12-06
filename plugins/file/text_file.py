#!/usr/bin/env python3

import datetime
import os
from enum import Enum
from pathlib import Path
from typing import List, Optional

from pydantic import field_validator

from core import utils
from core.config import Plugins
from core.interfaces import Actor, ActorConfig, ActorEntity, Event, EventType, Record, TextRecord
from core.monitors import TaskMonitor, TaskMonitorEntity


@Plugins.register('from_file', Plugins.kind.ACTOR_CONFIG)
class FileMonitorConfig(ActorConfig):
    pass

@Plugins.register('from_file', Plugins.kind.ACTOR_ENTITY)
class FileMonitorEntity(TaskMonitorEntity):
    encoding: Optional[str] = None
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
            with open(self.path, 'rt', encoding=self.encoding) as fp:
                if self.split_lines:
                    lines = fp.readlines()
                else:
                    lines = [fp.read()]
                for line in lines:
                    record = TextRecord(text=line.strip())
                    records.append(record)
        return records

    def get_new_records(self) -> List[TextRecord]:
        return self.get_records() if self.changed() else []


@Plugins.register('from_file', Plugins.kind.ACTOR)
class FileMonitor(TaskMonitor):

    async def get_new_records(self, entity: FileMonitorEntity):
        return entity.get_new_records()


@Plugins.register('to_file', Plugins.kind.ACTOR_CONFIG)
class FileActionConfig(ActorConfig):
    pass

@Plugins.register('to_file', Plugins.kind.ACTOR_ENTITY)
class FileActionEntity(ActorEntity):
    path: Path
    separator: str = '\n'

@Plugins.register('to_file', Plugins.kind.ACTOR)
class FileAction(Actor):
    supported_record_types = [Record, TextRecord, Event]

    def handle(self, entity: FileActionEntity, record: Record):
        try:
            text = f'{record}{entity.separator}'
            with open(entity.path, 'at', encoding='utf8') as fp:
                fp.write(text)
                fp.flush()
        except Exception as e:
            message = f'error in {self.conf.name}.{entity}: {e}'
            self.on_record(entity, Event(event_type=EventType.error, text=message))
            self.logger.exception(message)

class SuffixType(str, Enum):
    timestamp = 'timestamp'
    date = 'date'

@Plugins.register('as_file', Plugins.kind.ACTOR_CONFIG)
class SaveAsFileActionConfig(ActorConfig):
    pass

@Plugins.register('as_file', Plugins.kind.ACTOR_ENTITY)
class SaveAsFileActionEntity(ActorEntity):
    save_path: Path
    base_name: str
    suffix_type: SuffixType = SuffixType.timestamp
    save_as_json: bool = False
    only_save_changed: bool = True
    hash: Optional[str] = None

    @field_validator('save_path')
    @classmethod
    def check_dir(cls, path: Path):
        if utils.check_dir(path):
            return path
        raise ValueError(f'check if provided path points to a writeable directory')

@Plugins.register('as_file', Plugins.kind.ACTOR)
class SaveAsFileAction(Actor):
    supported_record_types = [Record, TextRecord, Event]

    @staticmethod
    def has_changed(entity: SaveAsFileActionEntity, record: Record) -> bool:
        record_hash = record.hash()
        changed = record_hash != entity.hash
        entity.hash = record_hash
        return changed

    @staticmethod
    def get_filename(entity: SaveAsFileActionEntity) -> Path:
        now = datetime.datetime.now()
        if entity.suffix_type == SuffixType.timestamp:
            suffix = int(now.timestamp())
        elif entity.suffix_type == SuffixType.date:
            suffix = now.isoformat()
        else:
            suffix = '-'
        path = Path(entity.save_path).joinpath(entity.base_name)
        extension = 'txt' if not entity.save_as_json else 'json'
        path = path.with_suffix(f'.{suffix}{path.suffix}.{extension}')
        return path

    def handle(self, entity: SaveAsFileActionEntity, record: Record):
        if entity.only_save_changed and not self.has_changed(entity, record):
            self.logger.debug(f'{self.conf.name}.{entity}: record did not change since last time, not saving')
            return
        path = self.get_filename(entity)
        try:
            text = str(record) if not entity.save_as_json else record.as_json(indent=4)
            with open(path, 'wt', encoding='utf8') as fp:
                fp.write(text)
        except Exception as e:
            message = f'error in {self.conf.name}.{entity}: {e}'
            self.on_record(entity, Event(event_type=EventType.error, text=message))
            self.logger.exception(message)

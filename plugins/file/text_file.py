#!/usr/bin/env python3

import os
from pathlib import Path
from typing import List, Optional

from pydantic import Field, field_validator

from core import utils
from core.config import Plugins
from core.interfaces import Actor, ActorConfig, ActorEntity, Event, EventType, Record, TextRecord
from core.monitors import TaskMonitor, TaskMonitorEntity
from core.utils import Fmt, OutputFormat


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
    path: Path = Field(default=Path.cwd())
    filename: str
    encoding: Optional[str] = 'utf8'
    output_format: OutputFormat = OutputFormat.str
    overwrite: bool = True
    append: bool = True
    prefix: str = ''
    postfix: str = '\n'

    @field_validator('path')
    @classmethod
    def check_dir(cls, path: Path) -> Path:
        if utils.check_dir(path):
            return path
        raise ValueError(f'check if provided path points to a writeable directory')


@Plugins.register('to_file', Plugins.kind.ACTOR)
class FileAction(Actor):
    supported_record_types = [Record, TextRecord, Event]

    def handle(self, entity: FileActionEntity, record: Record):
        filename = Fmt.format(entity.filename, record)
        path = Path(entity.path).joinpath(filename)
        if path.exists() and not entity.overwrite:
            self.logger.debug(f'[{entity.name}] file {path} already exists, not overwriting')
            return
        mode = 'at' if entity.append else 'wt'
        try:
            text = entity.prefix + Fmt.save_as(record, entity.output_format) + entity.postfix
            with open(path, mode, encoding=entity.encoding) as fp:
                fp.write(text)
        except Exception as e:
            message = f'error in {self.conf.name}.{entity}: {e}'
            self.on_record(entity, Event(event_type=EventType.error, text=message))
            self.logger.exception(message)
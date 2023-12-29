#!/usr/bin/env python3

import os
from pathlib import Path
from typing import List, Optional, Sequence

from pydantic import Field, field_validator

from core import utils
from core.config import Plugins
from core.interfaces import Actor, ActorConfig, ActorEntity, Event, EventType, Record, TextRecord
from core.monitors import HIGHEST_UPDATE_INTERVAL, TaskMonitor, TaskMonitorEntity
from core.utils import Fmt, OutputFormat


@Plugins.register('from_file', Plugins.kind.ACTOR_CONFIG)
class FileMonitorConfig(ActorConfig):
    pass


@Plugins.register('from_file', Plugins.kind.ACTOR_ENTITY)
class FileMonitorEntity(TaskMonitorEntity):
    encoding: Optional[str] = None
    """Input file encoding. If not specified default system-wide encoding is used"""
    path: Path
    """Path to monitored file"""
    split_lines: bool = False
    """If true, each line of the file will create a separate record. Otherwise a single record will be generated with entire file content"""
    mtime: float = Field(exclude=True, default=-1)
    """internal variable to persist state between updates. Used to check if file has changed"""
    base_update_interval: float = Field(exclude=True, default=60)
    """internal variable to persist state between updates. Used to restore configured update interval after delay on network request error"""


    def __post_init__(self):
        self.path = Path(self.path)
        self.base_update_interval = self.update_interval


@Plugins.register('from_file', Plugins.kind.ACTOR)
class FileMonitor(TaskMonitor):
    """
    Monitor content of a text file

    On specified intervals check existence and last modification time
    of target file, and if it changed read file content
    (either line by line or as a whole) and make it to a text record(s).

    Records are not checked to be new, so appending content to the end
    of the existing file will produce duplicates of already sent records.
    """

    async def get_new_records(self, entity: FileMonitorEntity) -> Sequence[TextRecord]:
        if not self.has_changed(entity):
            return []
        try:
            records = self.get_records(entity)
            if entity.update_interval != entity.base_update_interval:
                entity.update_interval = entity.base_update_interval
            return records
        except Exception as e:
            self.logger.warning(f'[{entity.name}] error when processing file "{entity.path}": {e}')
            entity.update_interval = max(entity.update_interval * 1.2, HIGHEST_UPDATE_INTERVAL)
            return []

    def exists(self, entity: FileMonitorEntity) -> bool:
        if not entity.path.exists():
            entity.mtime = -1
            return False
        else:
            return True

    def has_changed(self, entity: FileMonitorEntity) -> bool:
        if not self.exists(entity):
            return False
        try:
            current_mtime = os.stat(entity.path).st_mtime
        except OSError as e:
            self.logger.debug(f'[{entity.name}] failed to get file info for "{entity.path}": {e}')
            return False
        if current_mtime == entity.mtime:
            return False
        else:
            entity.mtime = current_mtime
            return True

    def get_records(self, entity: FileMonitorEntity) -> List[TextRecord]:
        records = []
        if self.exists(entity):
            with open(entity.path, 'rt', encoding=entity.encoding) as fp:
                if entity.split_lines:
                    lines = fp.readlines()
                else:
                    lines = [fp.read()]
                for line in lines:
                    record = TextRecord(text=line.strip())
                    records.append(record)
        return records


@Plugins.register('to_file', Plugins.kind.ACTOR_CONFIG)
class FileActionConfig(ActorConfig):
    pass


@Plugins.register('to_file', Plugins.kind.ACTOR_ENTITY)
class FileActionEntity(ActorEntity):
    path: Path = Path.cwd()
    """Directory where output file should be created. Default is current directory"""
    filename: str
    """Name of the output file. Supports templating with {...}""" # FIXME: describe templating
    encoding: Optional[str] = 'utf8'
    """Output file encoding. Defaults to UTF8."""
    output_format: OutputFormat = OutputFormat.str
    """Should record be written in output file as plain text or json""" # FIXME: list all valid values
    overwrite: bool = True
    """Whether file should be written in if it already exists"""
    append: bool = True
    """If true, new record will be written in the end of the file without overwriting already present lines"""
    prefix: str = ''
    """String that will be appended before record text. Can be used to separate records from each other or for simple templating"""
    postfix: str = '\n'
    """String that will be appended after record text"""

    @field_validator('path')
    @classmethod
    def check_dir(cls, path: Path) -> Path:
        if utils.check_dir(path):
            return path
        raise ValueError(f'check if provided path points to a writeable directory')


@Plugins.register('to_file', Plugins.kind.ACTOR)
class FileAction(Actor):
    """
    Write text representation of a record to file

    Takes record coming from a Chain, converts it to text representation,
    and write to a file in given directory. Output file can be generated
    dynamically based on template filled with values from the record or
    be static. When file already exists, new records can be appended to
    the end of the file or overwrite it.
    """

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
#!/usr/bin/env python3

import os
from pathlib import Path
from typing import List, Optional, Sequence

from pydantic import Field, field_validator

from avtdl.core import utils
from avtdl.core.config import Plugins
from avtdl.core.interfaces import Actor, ActorConfig, ActorEntity, Event, EventType, Record, TextRecord
from avtdl.core.monitors import HIGHEST_UPDATE_INTERVAL, TaskMonitor, TaskMonitorEntity
from avtdl.core.utils import Fmt, OutputFormat, read_file, sanitize_filename

Plugins.register('from_file', Plugins.kind.ASSOCIATED_RECORD)(TextRecord)


@Plugins.register('from_file', Plugins.kind.ACTOR_CONFIG)
class FileMonitorConfig(ActorConfig):
    pass


@Plugins.register('from_file', Plugins.kind.ACTOR_ENTITY)
class FileMonitorEntity(TaskMonitorEntity):
    encoding: Optional[str] = None
    """encoding used to open the monitored file. If not specified, default system-wide encoding is used"""
    path: Path
    """path to the monitored file"""
    split_lines: bool = False
    """if true, each line of the file will create a separate record. Otherwise, a single record will be generated with the entire file content"""
    update_interval: float = 60
    """how often the monitored file should be checked, in seconds"""
    mtime: float = Field(exclude=True, default=-1)
    """internal variable to persist state between updates. Used to check if the file has changed"""
    base_update_interval: float = Field(exclude=True, default=60)
    """internal variable to persist state between updates. Used to restore configured update interval after delay on network request error"""


    def __post_init__(self):
        self.path = Path(self.path)
        self.base_update_interval = self.update_interval


@Plugins.register('from_file', Plugins.kind.ACTOR)
class FileMonitor(TaskMonitor):
    """
    Monitor content of a text file

    On specified intervals, check existence and last modification time
    of target file, and if it has changed, read file contents
    either line by line or as a whole and emit it as a text record(s).

    Records are not checked for uniqueness, so appending content to the end
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
            text = read_file(entity.path)
            if entity.split_lines:
                lines = text.split('\n')
            else:
                lines = [text]
            for line in lines:
                record = TextRecord(text=line.strip())
                records.append(record)
        return records


Plugins.register('to_file', Plugins.kind.ASSOCIATED_RECORD)(Event)


@Plugins.register('to_file', Plugins.kind.ACTOR_CONFIG)
class FileActionConfig(ActorConfig):
    pass


@Plugins.register('to_file', Plugins.kind.ACTOR_ENTITY)
class FileActionEntity(ActorEntity):
    path: Optional[Path] = None
    """directory where output file should be created. Default is current directory"""
    filename: str
    """name of the output file. Supports templating with `{...}`"""
    encoding: Optional[str] = 'utf8'
    """output file encoding"""
    output_format: OutputFormat = Field(default=OutputFormat.str, description='one of `' + "`, `".join(OutputFormat.__members__) + '`')
    """should record be written in output file as plain text or json"""
    overwrite: bool = True
    """whether file should be overwritten in if it already exists"""
    append: bool = True
    """if true, new record will be written at the end of the file without overwriting already present lines"""
    prefix: str = ''
    """string that will be appended before the record text. Can be used to separate records from each other or for simple templating"""
    postfix: str = '\n'
    """string that will be appended after the record text"""

    @field_validator('path')
    @classmethod
    def check_dir(cls, path: Optional[Path]) -> Path:
        if path is None:
            return Path.cwd()
        if utils.check_dir(path):
            return path
        raise ValueError(f'check if provided path points to a writeable directory')


@Plugins.register('to_file', Plugins.kind.ACTOR)
class FileAction(Actor):
    """
    Write record to a text file

    Takes a record coming from a Chain, converts it to text representation,
    and writes to a file in given directory. When a file already exists,
    new records can be appended to the end of the file or overwrite it.

    Output file name can be static or generated dynamically based on the template
    filled with values from the record fields: every occurrence of `{text}`
    in filename will be replaced with the value of the `text` field of processed
    record, if the record has one.

    Allows writing the record as human-readable text representation or as names and
    values of the record fields in json format. For custom format template, pass the record
    through `filter.format` plugin prior to this one.

    Produces `Event` with `error` type if writing to target file fails.

    Note discrepancy between default value of `encoding` setting between `from_file`
    and `to_file` plugins. Former is expected to be able to read files produced by
    different software and therefore relies on system-wide settings. It would make
    sense to do the same in the latter, but it would introduce possibility of failing
    to write records containing text with Unicode codepoints that cannot be represented
    using system-wide encoding.
    """

    def handle(self, entity: FileActionEntity, record: Record):
        filename = Fmt.format(entity.filename, record)
        filename = sanitize_filename(filename)
        if entity.path is None:
            path = Path.cwd().joinpath(filename)
        else:
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

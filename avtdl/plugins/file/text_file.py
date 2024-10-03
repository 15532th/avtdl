#!/usr/bin/env python3

import os
import re
from pathlib import Path
from typing import List, Optional, Sequence

from pydantic import Field, field_validator

from avtdl.core.config import Plugins
from avtdl.core.interfaces import Action, ActionEntity, ActorConfig, Event, EventType, Record, TextRecord
from avtdl.core.monitors import HIGHEST_UPDATE_INTERVAL, TaskMonitor, TaskMonitorEntity
from avtdl.core.utils import Fmt, OutputFormat, check_dir, read_file, sanitize_filename

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
    update_interval: float = 60
    """how often the monitored file should be checked, in seconds"""
    split_lines: bool = False
    """split text into multiple records according to "record_start" and "record_end" patterns. If disabled, a single record with entire text will be produced"""
    record_start: str = Field(default='^', validate_default=True)
    """regular expression marking beginning of the record in the text, used when "split_lines" enabled"""
    record_end: str = Field(default='$', validate_default=True)
    """regular expression marking the end of the record in the text, used when "split_lines" enabled"""
    follow: bool = False
    """remember current position in the file and only read lines below it on consequent update"""
    quiet_start: bool = False
    """throw away new records on the first update after application startup"""
    mtime: float = Field(exclude=True, default=-1)
    """internal variable to persist state between updates. Used to check if the file has changed"""
    inode: int = Field(exclude=True, default=-1)
    """internal variable to persist state between updates. Used to check if the file was replaced with a new one"""
    position: int = Field(exclude=True, default=-1)
    """internal variable to persist state between updates. Used to hold current position in the file in follow mode. Value -1 indicates that file hasn't yet been read since application start"""
    text_buffer: str = Field(exclude=True, default='')
    """internal variable to persist state between updates. Used to hold fragment of record that was only partially written in the monitored file"""
    base_update_interval: float = Field(exclude=True, default=60)
    """internal variable to persist state between updates. Used to restore configured update interval after delay on network request error"""

    @field_validator('path')
    @classmethod
    def check_length(cls, path: Path) -> Path:
        try:
            if path.is_dir():
                raise ValueError(f'path is a directory: "{path}"')
            if path.exists():
                with open(path, 'rb') as _:
                    pass
        except OSError as e:
            raise ValueError(f'{e}')
        return path

    @field_validator('record_start', 'record_end')
    @classmethod
    def check_regexp(cls, pattern: str) -> re.Pattern:
        try:
            return re.compile(pattern, re.MULTILINE)
        except re.error as e:
            raise ValueError(f'invalid regular expression "{pattern}": {e}')

    def __post_init__(self):
        self.base_update_interval = self.update_interval


@Plugins.register('from_file', Plugins.kind.ACTOR)
class FileMonitor(TaskMonitor):
    """
    Monitor content of a text file

    On specified intervals, check existence and last modification time
    of target file, and if it has changed, read file contents
    either line by line or as a whole and emit it as a text record(s).

    When `follow` mode is enabled, current position at the end of the file
    is preserved and only lines that were appended after it will be read
    on consequent update.

    Enabling `split_lines` option will cause file content to be split into
    multiple records according to regular expressions provided with the
    `record_start` and `record_end` settings.

    They are set to match the beginning and then end of a line by default,
    which means every line will be treated as a separate record. Note, that
    both regular expressions are compiled with multiline flag, meaning that
    match will span over multiple lines.

    It is possible to make a single `record_start` expression to match
    entire record by setting `record_end` to empty string ``.
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

    @staticmethod
    def exists(entity: FileMonitorEntity) -> bool:
        return entity.path.exists()

    def has_changed(self, entity: FileMonitorEntity) -> bool:
        if not self.exists(entity):
            entity.mtime = -1
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

    def has_been_replaced(self, entity: FileMonitorEntity) -> bool:
        if not self.exists(entity):
            entity.inode = -1
            return False
        try:
            current_inode = os.stat(entity.path).st_ino
        except OSError as e:
            self.logger.debug(f'[{entity.name}] failed to get file info for "{entity.path}": {e}')
            return False
        if current_inode == entity.inode:
            return False
        else:
            entity.inode = current_inode
            return True

    def get_file_content(self, entity: FileMonitorEntity) -> str:
        if not entity.follow:
            if entity.position == -1:
                entity.position = 0
                if entity.quiet_start:
                    return ''
            return read_file(entity.path, entity.encoding)

        if self.has_been_replaced(entity):
            entity.text_buffer = ''
            entity.position = 0
        with open(entity.path, 'rt', encoding=entity.encoding) as fp:
            if entity.position == -1:
                if entity.quiet_start:
                    entity.position = fp.seek(0, os.SEEK_END)
                else:
                    entity.position = 0
            else:
                fp.seek(entity.position, os.SEEK_SET)
            text = fp.read()
            entity.position = fp.tell()
            return text

    def split_text(self, entity: FileMonitorEntity, text: str) -> List[str]:
        if not entity.split_lines:
            return [text]
        text = entity.text_buffer + text
        entity.text_buffer = ''  # clear buffer in case processing gets interrupted
        return self.split_text_start_end(text, entity)

    @staticmethod
    def split_text_start_end(text:str, entity: FileMonitorEntity) -> List[str]:
        lines: List[str] = []
        position = 0

        while True:
            if position >= len(text):
                break
            start_match = re.search(entity.record_start, text[position:])
            if start_match is None:
                # no more records in the rest of text
                break
            start = position + start_match.start()
            middle = position + start_match.end()
            end_match = re.search(entity.record_end, text[middle:])
            if end_match is None:
                # text ended mid-record, store it in the buffer
                entity.text_buffer = text[start:]
                break
            end = middle + end_match.end() + 1
            lines.append(text[start:end])
            position = end
        return lines

    def get_records(self, entity: FileMonitorEntity) -> List[TextRecord]:
        records = []
        text = self.get_file_content(entity)
        lines = self.split_text(entity, text)
        for line in lines:
            text = line.strip()
            if text:
                record = TextRecord(text=text)
                records.append(record)
        return records


Plugins.register('to_file', Plugins.kind.ASSOCIATED_RECORD)(Event)


@Plugins.register('to_file', Plugins.kind.ACTOR_CONFIG)
class FileActionConfig(ActorConfig):
    pass


@Plugins.register('to_file', Plugins.kind.ACTOR_ENTITY)
class FileActionEntity(ActionEntity):
    path: Optional[Path] = None
    """directory where output file should be created. Default is current directory. Supports templating with {...}"""
    filename: str
    """name of the output file. Supports templating with {...}"""
    encoding: Optional[str] = 'utf8'
    """output file encoding"""
    output_format: OutputFormat = Field(default=OutputFormat.str, description='one of `' + "`, `".join(OutputFormat.__members__) + '`')
    """should record be written in output file as plain text or json"""
    output_template: Optional[str] = None
    """if provided, it will be used as a template to format processed record. Only works with `output_format` set to plain text"""
    missing: Optional[str] = None
    """if specified, will be used  to fill template placeholders that do not have corresponding fields in current record"""
    overwrite: bool = True
    """whether file should be overwritten in if it already exists"""
    append: bool = True
    """if true, new record will be written at the end of the file without overwriting already present lines"""
    prefix: str = ''
    """string that will be appended before the record text. Can be used to separate records from each other or for simple templating"""
    postfix: str = '\n'
    """string that will be appended after the record text"""


@Plugins.register('to_file', Plugins.kind.ACTOR)
class FileAction(Action):
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
    values of the record fields in json format. For text representation it is possible
    to provide a custom format template.

    Produces `Event` with `error` type if writing to target file fails.

    Note discrepancy between default value of `encoding` setting between `from_file`
    and `to_file` plugins. Former is expected to be able to read files produced by
    different software and therefore relies on system-wide settings. It would make
    sense to do the same in the latter, but it would introduce possibility of failing
    to write records containing text with Unicode codepoints that cannot be represented
    using system-wide encoding.
    """

    def handle(self, entity: FileActionEntity, record: Record):
        filename = Fmt.format(entity.filename, record, tz=entity.timezone)
        filename = sanitize_filename(filename)
        if entity.path is None:
            path = Path.cwd().joinpath(filename)
        else:
            path = Fmt.format_path(entity.path, record, tz=entity.timezone)
            ok = check_dir(path)
            if not ok:
                self.logger.warning(f'[{entity.name}] check "{path}" is a valid and writeable directory')
                return
            path = path.joinpath(filename)
        try:
            path.exists()
        except OSError as e:
            self.logger.warning(f'[{entity.name}] failed to process record: {e}')
            return
        if path.exists() and not entity.overwrite:
            self.logger.debug(f'[{entity.name}] file {path} already exists, not overwriting')
            return
        mode = 'at' if entity.append else 'wt'
        try:
            if entity.output_template is not None and entity.output_format == OutputFormat.str:
                text = Fmt.format(entity.output_template, record, entity.missing, tz=entity.timezone)
            else:
                text = Fmt.save_as(record, entity.output_format)
            text = entity.prefix + text + entity.postfix
        except Exception as e:
            message = f'[{entity.name}] failed to convert record "{record!r}" into a text: {e}'
            self.on_record(entity, Event(event_type=EventType.error, text=message, record=record))
            self.logger.exception(message)
            return
        try:
            with open(path, mode, encoding=entity.encoding) as fp:
                fp.write(text)
        except OSError as e:
            message = f'[{entity.name}] error writing to output file "{path}": {e}'
            self.on_record(entity, Event(event_type=EventType.error, text=message, record=record))
            self.logger.warning(message)


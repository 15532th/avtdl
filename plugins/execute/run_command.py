import asyncio
import datetime
import shlex
from hashlib import sha1
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from pydantic import field_validator

from core import utils
from core.config import Plugins
from core.interfaces import Actor, ActorConfig, ActorEntity, Event, EventType, Record
from core.utils import Fmt, check_dir, sanitize_filename


@Plugins.register('execute', Plugins.kind.ACTOR_CONFIG)
class CommandConfig(ActorConfig):
    pass

@Plugins.register('execute', Plugins.kind.ACTOR_ENTITY)
class CommandEntity(ActorEntity):
    command: str
    working_dir: Optional[Path] = None
    log_dir: Optional[Path] = None # write stdout to a file in this directory if set
    log_filename: Optional[str] = None
    placeholders: Dict[str, str] = {'{url}': 'url', '{title}': 'title', '{text}': 'text'} # format {'placeholder': 'record property name'}
    static_placeholders: Dict[str, str] = {} # set in config in format {'placeholder': 'value'}
    forward_failed: bool = False # emit record down the chain if subprocess returned non-zero exit code
    report_failed: bool = True # emit Event(type="error") if subprocess returned non-zero exit code or raised exception
    report_finished: bool = False # emit Event(type="finished") if subprocess returned zero as exit code
    report_started: bool = False # emit Event(type="started") before starting subprocess

    @field_validator('working_dir', 'log_dir')
    @classmethod
    def check_dir(cls, path: Optional[Path]):
        if path is None:
            return path
        ok = utils.check_dir(path)
        if ok:
            return path
        else:
            raise ValueError(f'check path "{path}" exists and is a writeable directory')


@Plugins.register('execute', Plugins.kind.ACTOR)
class Command(Actor):

    def __init__(self, conf: CommandConfig, entities: Sequence[CommandEntity]):
        super().__init__(conf, entities)
        self.running_commands: Dict[str, asyncio.Task] = {}

    def handle(self, entity: CommandEntity, record: Record):
        self.add(entity, record)

    def args_for(self, entity: CommandEntity, record: Record):
        try:
            args = shlex.split(entity.command)
        except ValueError as e:
            self.logger.error(f'{self.conf.name}: error parsing "command" field of entity "{entity.name}" with value "{entity.command}": {e}')
            raise
        record_as_dict = record.model_dump()
        new_args = []
        for arg in args:
            new_arg = arg
            for placeholder, field in entity.placeholders.items():
                value = record_as_dict.get(field)
                if value is not None:
                    new_arg = new_arg.replace(placeholder, value)
                else:
                    self.logger.warning(f'[{entity.name}] configured placeholder "{field}" is not a field of {record.__class__.__name__} ({record!r}), resulting command is unlikely to be valid')
            for placeholder, value in entity.static_placeholders.items():
                new_arg = new_arg.replace(placeholder, value)
            new_args.append(new_arg)
        return new_args

    @staticmethod
    def shell_for(args: List[str]) -> str:
        return ' '.join(args)

    def _generate_task_id(self, entity: CommandEntity, record: Record, command_line: str) -> str:
        record_hash = record.hash()
        task_id = f'Task for {entity.name}: on record {record!r} ({record_hash}) executing "{command_line}"'
        return task_id

    def add(self, entity: CommandEntity, record: Record):
        args = self.args_for(entity, record)
        if entity.working_dir is None:
            entity.working_dir = Path.cwd()
            self.logger.info(f'[{entity.name}] working directory is not specified, using current directory instead: {entity.working_dir}')
        else:
            ok = check_dir(entity.working_dir)
            if not ok:
                self.logger.warning(f'[{entity.name}] check if working directory "{entity.working_dir}" exists and is a writeable directory')

        command_line = self.shell_for(args)
        task_id = self._generate_task_id(entity, record, command_line)
        if task_id in self.running_commands:
            msg = f'[{entity.name}] command "{command_line}" for record {record!r} is already running, will not call again'
            self.logger.info(msg)
            return
        self.logger.debug(f'[{entity.name}] executing command "{command_line}" for record {record!r}')
        task = self.run_subprocess(args, task_id, entity, record)
        self.running_commands[task_id] = asyncio.get_event_loop().create_task(task)

    def _get_output_file(self, entity: CommandEntity, record: Record, task_id: str) -> Optional[Path]:
        if entity.log_dir is None:
            return None
        ok = check_dir(entity.log_dir)
        if not ok:
            self.logger.warning(f'[{entity.name}] check if directory specified in "output_dir" value "{entity.log_dir}" exists and is a writeable directory')
            self.logger.warning(f'[{entity.name}] output of running command will be redirected to stdout')
            return None
        if entity.log_filename is not None:
            filename = Fmt.format(entity.log_filename, record)
        else:
            timestamp = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S.%f')
            command_pre_hash = sha1(task_id.encode())
            command_hash = command_pre_hash.hexdigest()
            filename = f'command_{entity.name}_{timestamp}_{command_hash[:6]}_stdout.log'
        filename = sanitize_filename(filename)
        return entity.log_dir / filename

    async def run_subprocess(self, args: List[str], task_id: str, entity: CommandEntity, record: Record):
        command_line = self.shell_for(args)
        self.logger.info(f'[{entity.name}] executing command {command_line}')
        if entity.report_started:
            event = Event(event_type=EventType.started, text=f'Running command: {command_line}')
            self.on_record(entity, event)
        stdout_path = self._get_output_file(entity, record, task_id)
        try:
            stdout = open(stdout_path, 'at') if stdout_path is not None else None
        except OSError as e:
            self.logger.warning(f'[{entity.name}] failed to open file {stdout_path}, command output will not be written')
            stdout = None
        try:
            if stdout is None:
                process = await asyncio.create_subprocess_exec(*args, cwd=entity.working_dir)
            else:
                with stdout:
                    stdout.write(f'# [{self.conf.name}.{entity.name}] > {entity.command}\n')
                    stdout.flush()
                    process = await asyncio.create_subprocess_exec(*args, cwd=entity.working_dir, stdout=stdout, stderr=asyncio.subprocess.STDOUT)
        except Exception as e:
            self.logger.warning(f'[{entity.name}] failed to execute command "{command_line}": {e}')
            if entity.report_failed:
                event = Event(event_type=EventType.error, text=f'[{entity.name}] failed to execute command: {command_line}')
                self.on_record(entity, event)
            if entity.forward_failed:
                self.on_record(entity, record)
            return
        try:
            await process.wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            self.logger.warning(f'[{entity.name}] application is terminating before running command has completed. Check on it and restart manually if needed. Process PID: {process.pid}. Exact command line:\n{command_line}')
            raise

        self.running_commands.pop(task_id)

        self.logger.debug(f'[{entity.name}] subprocess for {command_line} finished with exit code {process.returncode}')

        if process.returncode == 0:
            if entity.report_finished:
                event = Event(event_type=EventType.finished, text=f'[{entity.name}] command finished successfully: {command_line}')
                self.on_record(entity, event)
        else:
            if entity.report_failed:
                event = Event(event_type=EventType.error, text=f'[{entity.name}] command finished with error: {command_line}')
                self.on_record(entity, event)
            if entity.forward_failed:
                self.on_record(entity, record)

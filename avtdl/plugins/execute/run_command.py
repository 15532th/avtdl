import asyncio
import datetime
import shlex
from hashlib import sha1
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from pydantic import field_validator

from avtdl.core import utils
from avtdl.core.config import Plugins
from avtdl.core.interfaces import Action, ActionEntity, ActorConfig, Event, EventType, Record
from avtdl.core.utils import Fmt, check_dir, sanitize_filename

Plugins.register('execute', Plugins.kind.ASSOCIATED_RECORD)(Event)


@Plugins.register('execute', Plugins.kind.ACTOR_CONFIG)
class CommandConfig(ActorConfig):
    pass

@Plugins.register('execute', Plugins.kind.ACTOR_ENTITY)
class CommandEntity(ActionEntity):
    command: str
    """shell command to be executed on every received record. Supports placeholders that will be replaced with currently processed record fields values"""
    working_dir: Optional[Path] = None
    """path to the directory where command will be executed. If not set current working directory is used. Supports templating with {...}"""
    log_dir: Optional[Path] = None
    """write executed process output to a file in this directory if set. If it is not set, output will not be redirected to file"""
    log_filename: Optional[str] = None
    """filename to write executed process output to. If not defined, it is generated automatically based on command and entity name"""
    placeholders: Dict[str, str] = {'{url}': 'url', '{title}': 'title', '{text}': 'text'}
    """parts of `command` string that should be replaced with processed record fields, defined as mapping `'placeholder': 'record field name'`"""
    static_placeholders: Dict[str, str] = {}
    """parts of `command` string that will be replaced with provided values, defined as mapping `'placeholder': 'replacement string'`. Intended to allow reusing same `command` template for multiple entities"""
    forward_failed: bool = False
    """emit currently processed record down the chain if the subprocess returned non-zero exit code. Can be used to define fallback command in case this one fails"""
    report_failed: bool = True
    """emit Event with type "error" if the subprocess returned non-zero exit code or raised exception"""
    report_finished: bool = False
    """emit Event with type "finished" if the subprocess returned zero as exit code"""
    report_started: bool = False
    """emit Event with type "started" before starting a subprocess"""

    @field_validator('log_dir')
    @classmethod
    def check_dir(cls, path: Optional[Path]):
        if path is None:
            return path
        ok = utils.check_dir(path)
        if ok:
            return path
        else:
            raise ValueError(f'check path "{path}" exists and is a writeable directory')

    @field_validator('command')
    @classmethod
    def split_args(cls, command: str):
        _ = shlex.split(command) # might raise ValueError
        return command


@Plugins.register('execute', Plugins.kind.ACTOR)
class Command(Action):
    """
    Run pre-defined shell command

    Take `command` string, replace keywords provided in `placeholders` with corresponding fields
    of currently processed record. For example, if `command` is set to

        "yt-dlp {url}"`

    and currently processed record comes from Youtube RSS feed and has `url` field value
    `https://www.youtube.com/watch?v=L692Sxz3thw`, then with default `placeholders`
    resulting command will be

        yt-dlp https://www.youtube.com/watch?v=L692Sxz3thw

    Note that placeholders do not have to be wrapped in `{}` and can, in fact, be any
    arbitrary text strings. However, placeholders are replaced by corresponding values
    one after another, so using piece of text that might come up in record field might
    produce unexpected results.

    `command` string is not treated as raw shell command. Instead, it is split into list
     of elements, where first element specifies the program executable, and the rest
     specify the arguments. It is therefore not possible to use shell features such as pipes
     or execute multiple commands in one line.

    Make sure the executable the command uses (`yt-dlp` in this case) is installed and
    can be run from the working directory by current user. It is advised to confirm that
    the command can be executed manually and it finishes without errors before automating it.

    For each entity, a separate working directory can be configured. Output is shown
    in the same window by default, but can be redirected to a file, with either static
    or autogenerated name.

    Produces Events at startup and successful or erroneous termination of the executed command
    if corresponding entity settings are enabled. They can be used to send Discord
    or Jabber notifications or execute another command when it happens.

    Processed record itself can also be passed down the chain if the command failed,
    providing a way to try a different one as a fallback. For example, record with
    Youtube url could be first handled by `ytarchive` and passed to `yt-dlp` if
    it happens to fail due to video link not being a livestream.
    """

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
            for placeholder, static_value in entity.static_placeholders.items():
                new_arg = new_arg.replace(placeholder, static_value)
            for placeholder, field in entity.placeholders.items():
                value = record_as_dict.get(field)
                if value is not None:
                    new_arg = new_arg.replace(placeholder, value)
                else:
                    if placeholder in arg:
                        self.logger.warning(f'[{entity.name}] configured placeholder "{field}" is not a field of {record.__class__.__name__} ({record!r}), resulting command is unlikely to be valid')
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
            working_dir = Path.cwd()
            self.logger.info(f'[{entity.name}] working directory is not specified, using current directory instead: {working_dir}')
        else:
            working_dir = Fmt.format_path(entity.working_dir, record)
            ok = check_dir(working_dir)
            if not ok:
                self.logger.warning(f'[{entity.name}] check if working directory "{working_dir}" exists and is a writeable directory')

        command_line = self.shell_for(args)
        task_id = self._generate_task_id(entity, record, command_line)
        if task_id in self.running_commands:
            msg = f'[{entity.name}] command "{command_line}" for record {record!r} is already running, will not call again'
            self.logger.info(msg)
            return
        self.logger.debug(f'[{entity.name}] executing command "{command_line}" for record {record!r}')
        task = self.run_subprocess(args, task_id, working_dir, entity, record)
        self.running_commands[task_id] = asyncio.get_event_loop().create_task(task, name=f'{self.conf.name}:{entity.name}:{task_id}')

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

    async def run_subprocess(self, args: List[str], task_id: str, working_dir: Path, entity: CommandEntity, record: Record):
        command_line = self.shell_for(args)
        self.logger.info(f'[{entity.name}] executing command {command_line}')
        if entity.report_started:
            event = Event(event_type=EventType.started, text=f'Running command: {command_line}')
            self.on_record(entity, event)
        stdout_path = self._get_output_file(entity, record, task_id)
        try:
            stdout = open(stdout_path, 'at') if stdout_path is not None else None
        except OSError as e:
            self.logger.warning(f'[{entity.name}] failed to open file {stdout_path}: {e}. Command output will not be written')
            stdout = None
        try:
            if stdout is None:
                process = await asyncio.create_subprocess_exec(*args, cwd=working_dir)
            else:
                with stdout:
                    stdout.write(f'# [{self.conf.name}.{entity.name}] > {command_line}\n')
                    stdout.flush()
                    process = await asyncio.create_subprocess_exec(*args, cwd=working_dir, stdout=stdout, stderr=asyncio.subprocess.STDOUT)
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

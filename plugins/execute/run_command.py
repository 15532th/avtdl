import asyncio
import os
import re
from pathlib import Path
from typing import Dict, List, Sequence, Optional
import shlex

from pydantic import field_validator

from core import utils
from core.interfaces import Actor, ActorConfig, ActorEntity, Record, Event, EventType
from core.config import Plugins

@Plugins.register('execute', Plugins.kind.ACTOR_CONFIG)
class CommandConfig(ActorConfig):
    pass

@Plugins.register('execute', Plugins.kind.ACTOR_ENTITY)
class CommandEntity(ActorEntity):
    name: str
    command: str
    working_dir: Optional[Path] = None
    placeholders: Dict[str, str] = {'{url}': 'url', '{title}': 'title', '{text}': 'text'} # format {'placeholder': 'record property name'}
    static_placeholders: Dict[str, str] = {} # set in config in format {'placeholder': 'value'}
    forward_failed: bool = False # emit record down the chain if subprocess returned non-zero exit code
    report_failed: bool = True # emit Event(type="error") if subprocess returned non-zero exit code or raised exception
    report_finished: bool = False # emit Event(type="finished") if subprocess returned zero as exit code
    report_started: bool = False # emit Event(type="started") before starting subprocess

    @field_validator('working_dir')
    @classmethod
    def check_dir(cls, path: Optional[Path]):
        if path is None:
            return path
        return utils.check_dir(path)


@Plugins.register('execute', Plugins.kind.ACTOR)
class Command(Actor):
    supported_record_types = [Record]

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
            for placeholder, value in entity.static_placeholders.items():
                new_arg = new_arg.replace(placeholder, value)
            new_args.append(new_arg)
        return new_args

    @staticmethod
    def shell_for(args: List[str]) -> str:
        return ' '.join(args)

    def add(self, entity: CommandEntity, record: Record):
        args = self.args_for(entity, record)
        if entity.working_dir is None:
            entity.working_dir = os.getcwd()
        else:
            if not os.path.exists(entity.working_dir):
                self.logger.warning('download directory {} does not exist, creating'.format(entity.working_dir))
                os.makedirs(entity.working_dir)

        command_line = self.shell_for(args)
        task_id = f'Task for {entity.name}: on record {record} executing {command_line}'
        if task_id in self.running_commands:
            msg = f'Task for {entity.name} is already processing record {record}'
            self.logger.info(msg)
            return
        task = self.run_subprocess(args, task_id, entity, record)
        self.running_commands[task_id] = asyncio.get_event_loop().create_task(task)

    async def run_subprocess(self, args: List[str], task_id: str, entity: CommandEntity, record: Record):
        command_line = self.shell_for(args)
        self.logger.info(f'[{entity.name}] executing command {command_line}')
        if entity.report_started:
            event = Event(event_type=EventType.started, text=f'Running command: {command_line}')
            self.on_record(entity, event)
        try:
            process = await asyncio.create_subprocess_exec(*args, cwd=entity.working_dir)
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

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
    forward_failed: bool = True # emit record down the chain if subprocess returned non-zero exit code

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

    def handle(self, entity_name: str, record: Record):
        if entity_name not in self.entities:
            raise ValueError(f'{self.conf.name}: unable run command for {entity_name}: no entity found')
        entity = self.entities[entity_name]
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
                    new_arg = re.sub(placeholder, value, new_arg)
            for placeholder, value in entity.static_placeholders.items():
                new_arg = re.sub(placeholder, value, new_arg)
            new_args.append(new_arg)
        return new_args

    @staticmethod
    def shell_for(args: List[str]) -> str:
        return ' '.join(args)

    def add(self, entity: CommandEntity, record: Record):
        args = self.args_for(entity, record)
        working_dir = entity.working_dir
        if working_dir is None:
            working_dir = os.getcwd()
        else:
            if not os.path.exists(working_dir):
                self.logger.warning('download directory {} does not exist, creating'.format(working_dir))
                os.makedirs(working_dir)

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
        self.logger.info(f'For {entity.name} executing command {command_line}')
        event = Event(event_type=EventType.started, url='', title=f'Running command: {command_line}')
        self.on_record(entity.name, event)
        process = await asyncio.create_subprocess_exec(*args, cwd=entity.working_dir)
        await process.wait()
        self.logger.debug('subprocess for {} finished with exit code {}'.format(command_line, process.returncode))
        self.running_commands.pop(task_id)
        if process.returncode == 0:
            event = Event(event_type=EventType.finished, url='', title=f'command finished successfully: {command_line}')
            self.on_record(entity.name, event)
        else:
            event = Event(event_type=EventType.error, url='', title=f'command failed: {command_line}')
            self.on_record(entity.name, event)
            if entity.forward_failed:
                self.on_record(entity.name, record)

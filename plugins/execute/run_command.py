import asyncio
import os
import re
from typing import Dict, List, Sequence, Optional
import shlex

from core.interfaces import Actor, ActorConfig, ActorEntity, Record, Event, EventType
from core.config import Plugins

URL_PLACEHOLDER = '{url}'
TEXT_PLACEHOLDER = '{text}'

@Plugins.register('execute', Plugins.kind.ACTOR_CONFIG)
class CommandConfig(ActorConfig):
    url_placeholder: str = URL_PLACEHOLDER
    text_placeholder: str = TEXT_PLACEHOLDER

@Plugins.register('execute', Plugins.kind.ACTOR_ENTITY)
class CommandEntity(ActorEntity):
    name: str
    command: str
    working_dir: Optional[str] = None


@Plugins.register('execute', Plugins.kind.ACTOR)
class Command(Actor):
    supported_record_types = [Record]

    def __init__(self, conf: CommandConfig, entities: Sequence[CommandEntity]):
        super().__init__(conf, entities)
        self.running_commands: Dict[str, asyncio.Task] = {}

    def handle(self, entity_name: str, record: Record):
        if entity_name not in self.entities:
            raise ValueError(f'Unable run command for {entity_name}: no entity found')
        entity = self.entities[entity_name]
        self.add(entity, record)

    def args_for(self, entity: CommandEntity, record: Record):
        try:
            args = shlex.split(entity.command)
        except ValueError as e:
            self.logger.error(f'Error parsing "download_command" string {entity.command}: {e}')
            raise
        # need a copy of template arguments list anyway, since it gets changed
        new_args = []
        for arg in args:
            if arg.find(self.conf.url_placeholder) > -1:
                new_arg = re.sub(self.conf.url_placeholder, record.url, arg)
                new_args.append(new_arg)
            elif arg.find(self.conf.text_placeholder) > -1:
                new_arg = re.sub(self.conf.text_placeholder, str(record), arg)
                new_args.append(new_arg)
            else:
                new_args.append(arg)
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
        task = self.run_subprocess(args, working_dir, entity.name, task_id)
        self.running_commands[task_id] = asyncio.get_event_loop().create_task(task)

    async def run_subprocess(self, args, working_dir, entity_name, task_id):
        command_line = self.shell_for(args)
        self.logger.info(f'For {entity_name} executing command {command_line}')
        event = Event(event_type=EventType.started, url='', title=f'Running command: {command_line}')
        self.on_record(entity_name, event)
        process = await asyncio.create_subprocess_exec(*args, cwd=working_dir)
        await process.wait()
        self.logger.debug('subprocess for {} finished with exit code {}'.format(command_line, process.returncode))
        self.running_commands.pop(task_id)
        if process.returncode == 0:
            event = Event(event_type=EventType.finished, url='', title=f'command finished successfully: {command_line}')
            self.on_record(entity_name, event)
        else:
            event = Event(event_type=EventType.error, url='', title=f'command failed: {command_line}')
            self.on_record(entity_name, event)

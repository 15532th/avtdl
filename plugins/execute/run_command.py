import asyncio
import logging
import os
from typing import Dict, List, Sequence
import shlex

from core.interfaces import Actor, ActorConfig, ActorEntity, Record, Event, EventType
from core.config import Plugins

URL_PLACEHOLDER = '{url}'

@Plugins.register('execute', Plugins.kind.ACTOR_CONFIG)
class CommandConfig(ActorConfig):
    url_placeholder: str = '{url}'

@Plugins.register('execute', Plugins.kind.ACTOR_ENTITY)
class CommandEntity(ActorEntity):
    name: str
    command: str
    working_dir: str


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
        args = self.args_for(entity, record)
        self.add(args, entity.working_dir, entity_name)

    def args_for(self, entity: CommandEntity, record: Record):
        try:
            args = shlex.split(entity.command)
        except ValueError as e:
            logging.error(f'Error parsing "download_command" string {entity.command}: {e}')
            raise
        # need a copy of template arguments list anyway, since it gets changed
        args = [record.url if arg == self.conf.url_placeholder else arg for arg in args]
        return args

    def shell_for(self, args: List[str]) -> str:
        return ' '.join(args)

    def add(self, args, working_dir, entity_name):
        if working_dir is None:
            working_dir = os.getcwd()
        else:
            if not os.path.exists(working_dir):
                logging.warning('download directory {} does not exist, creating'.format(working_dir))
                os.makedirs(working_dir)

        command_line = self.shell_for(args)
        if command_line in self.running_commands:
            logging.debug('command line {} was called already'.format(self.shell_for(args)))
        task = self.run_subprocess(args, working_dir, entity_name)
        self.running_commands[command_line] = asyncio.get_event_loop().create_task(task)

    async def run_subprocess(self, args, working_dir, entity_name):
        command_line = self.shell_for(args)
        logging.info('starting download subprocess for {}'.format(command_line))
        event = Event(event_type=EventType.started, url='', title=f'Running command: {command_line}')
        self.on_record(entity_name, event)
        process = await asyncio.create_subprocess_exec(*args, cwd=working_dir)
        await process.wait()
        logging.debug('subprocess for {} finished with exit code {}'.format(command_line, process.returncode))
        self.running_commands.pop(command_line)
        if process.returncode == 0:
            event = Event(event_type=EventType.finished, url='', title=f'command finished: {command_line}')
            self.on_record(entity_name, event)
        else:
            event = Event(event_type=EventType.error, url='', title=f'command failed: {command_line}')
            self.on_record(entity_name, event)

import asyncio
from dataclasses import dataclass
import logging
import os
from typing import Dict, List
import shlex

from ..core.interfaces import Action, ActionConfig, ActionEntity, Record

URL_PLACEHOLDER = '{url}'

@dataclass
class CommandConfig(ActionConfig):
    url_placeholder: str = '{url}'

@dataclass
class CommandEntity(ActionEntity):
    name: str
    command: str
    working_dir: str


class Command(Action):

    def __init__(self, conf: CommandConfig, entities: CommandEntity):
        super().__init__(conf, entities)
        self.conf = conf
        self.running_commands: Dict[str, asyncio.Task] = {}

    def handle(self, entity_name: str, record: Record):
        if entity_name not in self.entities:
            raise ValueError(f'Unable run command for {entity_name}: no entity found')
        entity = self.entities[entity_name]
        args = self.args_for(entity, record)
        self.add(args, entity.working_dir)

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

    def add(self, args, working_dir):
        if working_dir is not None:
            if not os.path.exists(working_dir):
                logging.warning('download directory {} does not exist, creating'.format(working_dir))
                os.makedirs(working_dir)

        command_line = self.shell_for(args)
        if command_line in self.running_commands:
            logging.debug('command line {} was called already'.format(self.shell_for(args)))
        task = self.run_subprocess(args, working_dir)
        self.running_commands[command_line] = asyncio.get_event_loop().create_task(task)

    async def run_subprocess(self, args, working_dir=None):
        command_line = self.shell_for(args)
        logging.info('starting download subprocess for {}'.format(command_line))
        self.on_event('beginning', Record(url='', title=f'Running command {command_line}'))
        if working_dir is None:
            working_dir = os.getcwd()
        process = await asyncio.create_subprocess_exec(*args, cwd=working_dir)
        await process.wait()
        logging.debug('subprocess for {} finished with exit code {}'.format(command_line, process.returncode))
        self.running_commands.pop(command_line)
        if process.returncode == 0:
            event = Record(url='', title=f'command finished: {command_line}')
            self.on_event('success', event)
        else:
            event = Record(url='', title=f'command failed: {command_line}')
            self.on_event('failure', event)

    async def run(self):
        return

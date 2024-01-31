#!/usr/bin/env python3

import argparse
import asyncio
import logging
import os
from pathlib import Path

import yaml
from pydantic import ValidationError

from avtdl.core.config import ConfigParser
from avtdl.core.info import generate_plugins_description
from avtdl.core.loggers import set_logging_format, silence_library_loggers
from avtdl.core.utils import read_file


def load_config(path: Path):
    if not os.path.exists(path):
        print('Configuration file {} does not exist'.format(path))
        raise SystemExit
    try:
        config_text = read_file(path)
        config = yaml.load(config_text, Loader=yaml.FullLoader)
    except Exception as e:
        print('Failed to parse configuration file:')
        print(e)
        raise SystemExit from e
    return config


def parse_config(conf):
    try:
        actors, chains = ConfigParser.parse(conf)
    except ValidationError as e:
        logging.error(e)
        raise SystemExit from e
    except Exception as e:
        logging.exception(e)
        raise SystemExit from e
    return actors, chains


def handler(loop, context):
    logging.exception(f'unhandled exception in event loop:', exc_info=context.get('exception'))
    loop.default_exception_handler(context)


async def run(runnables):
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(handler)
    loop.slow_callback_duration = 100

    tasks = []
    for runnable in runnables:
        task = asyncio.create_task(runnable.run(), name=runnable.__class__.__name__)
        tasks.append(task)
    while True:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            if not task.done():
                continue
            if task.exception() is not None:
                logging.warning(f'task {task.get_name()} has terminated with exception', exc_info=task.exception())
        if not pending:
            break
        tasks = pending
    logging.info('all tasks are finished in the main loop')


def start(args):
    conf = load_config(args.config)
    actors, chains = parse_config(conf)

    for chain_name, chain_instance in chains.items():
        for actor_name, entities in chain_instance.conf:
            actor = actors.get(actor_name)
            if actor is None:
                logging.warning(f'chain "{chain_name}" references actor "{actor_name}, absent in "Actors" section. It might be a typo in the chain configuration')
                continue
            orphans = set(entities) - actor.entities.keys()
            for orphan in orphans:
                logging.warning(f'chain "{chain_name}" references "{actor_name}: {orphan}", but actor "{actor_name}" has no "{orphan}" entity. It might be a typo in the chain conf configuration')

    asyncio.run(run(actors.values()), debug=True)


def make_docs(args):
    output = args.plugins_doc
    doc = generate_plugins_description(output.suffix == '.html')
    try:
        with open(output, 'wt', encoding='utf8') as fp:
            fp.write(doc)
    except OSError as e:
        logging.error(f'failed to write documentation file "{output}": {e}')
        raise SystemExit from e
    

def main():
    description = '''Tool for monitoring rss feeds and other sources and running commands for new entries'''
    parser = argparse.ArgumentParser(description=description)
    help_v = 'set loglevel to DEBUG'
    parser.add_argument('-d', '--debug', action='store_true', default=False, help=help_v)
    help_v = 'set loglevel to INFO'
    parser.add_argument('-v', '--verbose', action='store_true', default=False, help=help_v)
    help_c = 'specify path to configuration file to use instead of default'
    parser.add_argument('-c', '--config', type=Path, default='config.yml', help=help_c)
    help_h = 'write plugins documentation in given file and exit. Documentation format is deduced by file extension: html document for ".html", markdown otherwise'
    parser.add_argument('-p', '--plugins-doc', type=Path, required=False, help=help_h)
    args = parser.parse_args()

    if args.debug:
        log_level = logging.DEBUG
    elif args.verbose:
        log_level = logging.INFO
    else:
        log_level = logging.WARNING

    set_logging_format(log_level)
    silence_library_loggers()

    if args.plugins_doc is not None:
        make_docs(args)
    else:
        start(args)


if __name__ == "__main__":
    main()

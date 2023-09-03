#!/usr/bin/env python3

import argparse
import asyncio
import logging
import os
from pathlib import Path

import yaml

from core.config import Plugins, ConfigParser


def load_config(path):
    if not os.path.exists(path):
        print('Configuration file {} does not exist'.format(path))
        raise SystemExit
    try:
        with open(path, 'rt') as config_file:
            config = yaml.load(config_file, Loader=yaml.FullLoader)
    except Exception as e:
        print('Failed to parse configuration file:')
        print(e)
        raise SystemExit from e
    return config

def set_logger(name, level, propagate=True):
    logger = logging.getLogger(name)
    logger.propagate = propagate
    logger.setLevel(level)


def set_logging(level):
    log_format = '%(asctime)s.%(msecs)03d [%(name)s] [%(levelname)s] %(message)s'
    datefmt = '%Y/%m/%d %H:%M:%S'
    logging.basicConfig(level=level, format=log_format, datefmt=datefmt)

async def run(runnables):
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
    logging.info('all tasks are finished in a main loop')

def main(config_path: Path):
    set_logger('asyncio', logging.INFO, propagate=False)
    set_logger('charset_normalizer', logging.INFO, propagate=False)
    set_logger('slixmpp', logging.ERROR, propagate=False)

    Plugins.load('plugins')

    conf = load_config(config_path)
    try:
        actors, chains = ConfigParser.parse(conf)
    except Exception as e:
        logging.exception(e)
        raise SystemExit from e

    asyncio.run(run(actors.values()), debug=True)


if __name__ == "__main__":
    description = '''Tool for monitoring rss feeds and other sources and running commands for new entries'''
    parser = argparse.ArgumentParser(description=description)
    help_v = 'set loglevel to DEBUG regardless of configuration setting'
    parser.add_argument('-v', '--verbose', action='count', default=0, help=help_v)
    help_c = 'specify path to configuration file to use instead of default'
    parser.add_argument('-c', '--config', type=Path, default='config.yml', help=help_c)
    args = parser.parse_args()

    log_level = args.verbose or getattr(logging, args.config['loglevel'])
    set_logging(log_level)

    main(args.config)

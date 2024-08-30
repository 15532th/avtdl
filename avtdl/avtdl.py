#!/usr/bin/env python3

import argparse
import asyncio
import logging
from asyncio import AbstractEventLoop
from pathlib import Path
from typing import Any, Dict, Tuple

import yaml

from avtdl.core.chain import Chain
from avtdl.core.config import ConfigParser, ConfigurationError, config_sancheck
from avtdl.core.info import generate_plugins_description, generate_version_string
from avtdl.core.interfaces import Actor
from avtdl.core.loggers import set_logging_format, silence_library_loggers
from avtdl.core.plugins import UnknownPluginError
from avtdl.core.utils import monitor_tasks, read_file


def load_config(path: Path) -> Any:
    try:
        if not path.exists():
            alt_path = path.with_suffix(path.suffix + '.txt')
            if alt_path.exists():
                print(f'Configuration file {path} not found, trying {alt_path} instead')
                path = alt_path
            else:
                raise ValueError('Configuration file {} does not exist'.format(path))
        config_text = read_file(path)
        config = yaml.load(config_text, Loader=yaml.FullLoader)
    except Exception as e:
        print('Failed to parse configuration file:')
        print(e)
        raise SystemExit from e
    return config


def parse_config(conf) -> Tuple[Dict[str, Actor], Dict[str, Chain]]:
    try:
        actors, chains = ConfigParser.parse(conf)
    except (ConfigurationError, UnknownPluginError) as e:
        logging.error(e)
        raise SystemExit from e
    except Exception as e:
        logging.exception(e)
        raise SystemExit from e
    return actors, chains


def handler(loop: AbstractEventLoop, context: Dict[str, Any]) -> None:
    logging.exception(f'unhandled exception in event loop:', exc_info=context.get('exception'))
    loop.default_exception_handler(context)


async def install_exception_handler() -> None:
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(handler)
    loop.slow_callback_duration = 100


async def run(config: Path) -> None:
    conf = load_config(config)
    actors, chains = parse_config(conf)
    config_sancheck(actors, chains)

    await install_exception_handler()
    tasks = []
    for runnable in actors.values():
        task = asyncio.create_task(runnable.run(), name=f'{runnable!r}.{hash(runnable)}')
        tasks.append(task)
    await monitor_tasks(tasks)


def make_docs(output: Path) -> None:
    doc = generate_plugins_description(output.suffix == '.html')
    try:
        with open(output, 'wt', encoding='utf8') as fp:
            fp.write(doc)
    except OSError as e:
        logging.error(f'failed to write documentation file "{output}": {e}')
        raise SystemExit from e


def main() -> None:
    description = '''Tool for monitoring rss feeds and other sources and running commands for new entries'''
    parser = argparse.ArgumentParser(description=description)
    help_v = 'set loglevel to DEBUG'
    parser.add_argument('-d', '--debug', action='store_true', default=False, help=help_v)
    help_v = 'print version and exit'
    parser.add_argument('-v', '--version', action='store_true', default=False, help=help_v)
    help_c = 'specify path to configuration file to use instead of default'
    parser.add_argument('-c', '--config', type=Path, default='config.yml', help=help_c)
    help_h = 'write plugins documentation in given file and exit. Documentation format is deduced by file extension: html document for ".html", markdown otherwise'
    parser.add_argument('-p', '--plugins-doc', type=Path, required=False, help=help_h)
    args = parser.parse_args()

    if args.debug:
        log_level = logging.DEBUG
    else:
        log_level = logging.INFO

    set_logging_format(log_level)
    silence_library_loggers()

    try:
        if args.version:
            print(generate_version_string())
        elif args.plugins_doc is not None:
            make_docs(args.plugins_doc)
        else:
            asyncio.run(run(args.config), debug=True)
    except KeyboardInterrupt:
        if args.debug:
            logging.exception('Interrupted, exiting... Printing stacktrace for debugging purpose:')
        else:
            logging.info('Interrupted, exiting...')


if __name__ == "__main__":
    main()

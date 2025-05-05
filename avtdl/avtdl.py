#!/usr/bin/env python3

import argparse
import asyncio
import logging
from asyncio import AbstractEventLoop
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from avtdl.core import webui
from avtdl.core.chain import Chain
from avtdl.core.config import ConfigParser, ConfigurationError, SettingsSection, config_sancheck
from avtdl.core.info import generate_plugins_description, generate_version_string
from avtdl.core.interfaces import Actor, RuntimeContext, TerminatedAction
from avtdl.core.loggers import set_logging_format, silence_library_loggers
from avtdl.core.plugins import UnknownPluginError
from avtdl.core.utils import read_file, write_file
from avtdl.core.yaml import yaml_load

DEFAULT_CONFIG_PATH = Path('config.yml')
CONFIG_TEMPLATE = '''
actors:
  rss:
    entities:
      - name: "ChannelName"
        url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCK0V3b23uJyU4N8eR_BR0QA"
      - name: "AnotherChannelName"
        url: "https://www.youtube.com/feeds/videos.xml?channel_id=UC3In1x9H3jC4JzsIaocebWg"
  execute:
    entities:
      - name: "archive"
        command: "ytarchive --threads 3 --wait {url} best"
        working_dir: "archive/livestreams/{author}/"

chains:
  "archive":
    - rss:
        - "ChannelName"
        - "AnotherChannelName"
    - execute:
        - "archive"
'''


def load_config(path: Path, encoding: Optional[str] = None) -> Any:
    try:
        if not path.exists():
            alt_path = path.with_suffix(path.suffix + '.txt')
            if alt_path.exists():
                print(f'Configuration file {path} not found, trying {alt_path} instead.')
                path = alt_path
            elif path == DEFAULT_CONFIG_PATH:
                print(f'Configuration file {path} not found, using example template.')
                write_file(path, CONFIG_TEMPLATE)
            else:
                raise ValueError('Configuration file {} does not exist'.format(path))
        config_text = read_file(path, encoding=encoding)
        config = yaml_load(config_text)
    except Exception as e:
        print('Failed to parse configuration file:')
        print(e)
        raise SystemExit from e
    return config


def parse_config(conf, ctx: RuntimeContext) -> Tuple[SettingsSection, Dict[str, Actor], Dict[str, Chain]]:
    try:
        settings, actors, chains = ConfigParser.parse(conf, ctx)
    except (ConfigurationError, UnknownPluginError) as e:
        logging.error(e)
        raise SystemExit from e
    except Exception as e:
        logging.exception(e)
        raise SystemExit from e
    return settings, actors, chains


def handler(loop: AbstractEventLoop, context: Dict[str, Any]) -> None:
    logging.exception(f'unhandled exception in event loop:', exc_info=context.get('exception'))
    loop.default_exception_handler(context)


async def install_exception_handler() -> None:
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(handler)
    loop.slow_callback_duration = 100



async def run(config_path: Path, host: Optional[str], port: Optional[int]) -> None:
    await install_exception_handler()
    config_encoding: Optional[str] = None
    while True:
        config = load_config(config_path, config_encoding)
        ctx = RuntimeContext.create()
        with ctx:
            settings, actors, chains = parse_config(config, ctx)
            if config_encoding != settings.encoding:
                config_encoding = settings.encoding
                logging.debug(f'configuration file encoding is explicitly set to "{settings.encoding}", reloading the file')
                continue
            if settings.encoding is None:
                logging.info(f'configuration file will be written on disk in UTF8 encoding. This can be changed by explicitly setting "encoding" option in the "Settings" section')
                settings.encoding = 'utf8'

            config_sancheck(actors, chains)

            if host is not None:
                settings.host = host
            if port is not None:
                settings.port = port

            controller = ctx.controller
            for runnable in actors.values():
                _ = controller.create_task(runnable.run(), name=f'{runnable!r}.{hash(runnable)}')
            _ = controller.create_task(webui.run(config_path, config, ctx, settings, actors, chains), name='webui')

            action = await controller.run_until_termination()
            if action == TerminatedAction.EXIT:
                logging.info('terminating...')
                break
            elif action == TerminatedAction.RESTART:
                logging.info('restarting...')
                continue
            else:
                assert False, f'Unknown action: {action}'


def make_docs(output: Path) -> None:
    doc = generate_plugins_description()
    try:
        with open(output, 'wt', encoding='utf8') as fp:
            fp.write(doc)
    except OSError as e:
        logging.error(f'failed to write documentation file "{output}": {e}')
        raise SystemExit from e


def main() -> None:
    description = '''Tool for monitoring rss feeds and other sources and running commands for new entries'''
    parser = argparse.ArgumentParser(description=description)
    help_d = 'set loglevel to DEBUG'
    parser.add_argument('-d', '--debug', action='store_true', default=False, help=help_d)
    help_v = 'print version and exit'
    parser.add_argument('-v', '--version', action='store_true', default=False, help=help_v)
    help_c = 'specify path to configuration file to use instead of default'
    parser.add_argument('-c', '--config', type=Path, default=DEFAULT_CONFIG_PATH, help=help_c)
    help_h = 'write plugins documentation in markdown format into a given file and exit'
    parser.add_argument('-p', '--plugins-doc', type=Path, required=False, help=help_h)
    help_dp = 'web-interface port (takes priority over configuration file)'
    parser.add_argument('-P', '--port', type=int, required=False, help=help_dp)
    help_dh = 'web-interface bind address (takes priority over configuration file)'
    parser.add_argument('-H', '--host', required=False, help=help_dh)
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
            asyncio.run(run(args.config, args.host, args.port), debug=True)
    except KeyboardInterrupt:
        if args.debug:
            logging.exception('Interrupted, exiting... Printing stacktrace for debugging purpose:')
        else:
            logging.info('Interrupted, exiting...')


if __name__ == "__main__":
    main()

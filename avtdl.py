#!/usr/bin/env python3

import asyncio
import argparse
import logging
import os
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from pprint import pformat

import yaml

from plugins.core.interfaces import Action, Filter, Monitor, Record
from plugins.core.filters import MatchFilter
from plugins.xmpp.send_jabber import SendJabber, JabberConfig, JabberEntity
from plugins.rss.youtube_rss import FeedMonitor, FeedMonitorConfig, FeedMonitorEntity
from plugins.execute.run_command import Command, CommandConfig, CommandEntity
from plugins.file.text_file import FileMonitor, FileMonitorEntity, FileMonitorConfig, FileAction, FileActionEntity, FileActionConfig

class Chain:
    def __init__(self,
                 monitors: List[Tuple[Monitor, List[str]]],
                 actions: List[Tuple[Action, List[str]]],
                 filters: Optional[List[Filter]] = None,
                 name: str = "ChainX"):
        self.name = name
        self.monitors = monitors
        self.actions = actions
        self.filters = filters or []
        for monitor, monitor_entities in monitors:
            for monitor_entity in monitor_entities:
                monitor.register(monitor_entity, self.handle)

    def filter(self, record: Record):
        for f in self.filters:
            record = f.match(record)
            if record is None:
                break
        return record

    def handle(self, record: Record):
        record = self.filter(record)
        if record is None:
            return
        for action, action_entities in self.actions:
            for action_entity_name in action_entities:
                action.handle(action_entity_name, record)

    def _pformat(self, section):
        f = []
        for item in section:
            item_instance, item_entries = item
            item_name = item_instance.__class__.__name__
            f.append(f'{item_name}, {item_entries}')
            return ', '.join(f)

    def __repr__(self):
        m = self._pformat(self.monitors)
        a = self._pformat(self.actions)
        return f'Chain("{self.name}", {m}, {self.filters!r}, {a})'

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


def set_logging(level):
    log_format = '%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s'
    datefmt = '%Y/%m/%d %H:%M:%S'
    logging.basicConfig(level=level, format=log_format, datefmt=datefmt)

async def run(runnables):
    tasks = []
    for runnable in runnables:
        task = asyncio.create_task(runnable.run(), name=runnable.__class__.__name__)
        tasks.append(task)
    await asyncio.Future()

def main(config_path: Path):
    conf = load_config(config_path)
#   conf = SimpleNamespace(**conf)

    monitors_names = {
        'rss': (FeedMonitor, FeedMonitorConfig, FeedMonitorEntity),
        'file': (FileMonitor, FileMonitorConfig, FileMonitorEntity)
    }
    filters_names = {
        'match': MatchFilter
    }
    actions_names = {
        'send': (SendJabber, JabberConfig, JabberEntity),
        'download': (Command, CommandConfig, CommandEntity),
        'file': (FileAction, FileActionConfig, FileActionEntity)
    }

    monitors = {}
    for monitor_type, items in conf['Monitors'].items():
        MonitorFactory, ConfigFactory, EntityFactory = monitors_names[monitor_type]
        defaults = items.get('defaults', {})
        entities = []
        for entiry_item in items['entities']:
            entity = EntityFactory(**{**defaults, **entiry_item})
            entities.append(entity)

        config = ConfigFactory(**items.get('config', {}))
        monitor = MonitorFactory(config, entities)
        monitors[monitor_type] = monitor

    actions = {}
    for action_type, items in conf['Actions'].items():
        ActionFactory, ConfigFactory, EntityFactory = actions_names[action_type]
        defaults = items.get('defaults', {})
        entities = []
        for entiry_item in items['entities']:
            entity = EntityFactory(**{**defaults, **entiry_item})
            entities.append(entity)

        config = ConfigFactory(**items.get('config', {}))
        action = ActionFactory(config, entities)
        actions[action_type] = action

    filters = {}
    for filter_type, filters_list in conf.get('Filters', {}).items():
        FilterFactory = filters_names[filter_type]
        for entity in filters_list:
            filters[entity['name']] = FilterFactory(**entity)

    chains = {}
    for name, chain_config in conf['Chains'].items():
        chain_monitors = []
        for monitors_list in chain_config['monitors'].items():
            monitor_type, entries_names = monitors_list
            monitor = monitors[monitor_type]
            chain_monitors.append((monitor, entries_names))
        chain_actions = []
        for actions_list in chain_config['actions'].items():
            action_type, entries_names = actions_list
            action = actions[action_type]
            chain_actions.append((action, entries_names))
        chain_filters = []
        for filter_type, filter_names in chain_config.get('filters', {}).items():
            for filter_name in filter_names:
                if filter_name in filters:
                    chain_filters.append(filters[filter_name])

        chain = Chain(chain_monitors, chain_actions, chain_filters, name)
        chains[name] = chain

    workers = [*monitors.values(), *actions.values()]
    asyncio.run(run(workers), debug=True)


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

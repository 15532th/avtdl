#!/usr/bin/env python3

import asyncio
import logging
import os
from types import SimpleNamespace
from typing import Callable, Dict, List, Optional

import yaml

from interfaces import Action, ActionEntity, ActionConfig, Filter, Monitor, MonitorEntity, MonitorConfig, Record
from filters import NoopFilter, MatchFilter
from send_jabber import SendJabber, JabberConfig, JabberEntity
from youtube_rss import FeedMonitor, FeedMonitorConfig, FeedMonitorEntity

class Chain:
    def __init__(self,
                 monitors: Dict[Monitor, List[str]],
                 actions: Dict[Action, List[str]],
                 filters: Optional[List[Filter]] = None,
                 name: str = "ChainX"):
        self.name = name
        self.monitors = monitors
        self.actions = actions
        self.filters = filters or []
        for monitor, monitor_entities in monitors.items():
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
        for action, action_entities in self.actions.items():
            for action_entity_name in action_entities:
                action.handle(action_entity_name, record)



class UserFeed:
    def __init__(self, feeds: List[Feed], actions: List[Action], filters: Optional[List[Filter]] = None, name: str = "UserFeedX"):
        self.feeds = feeds
        self.actions = actions
        self.filters = filters or []
        self.name = name

    def on_record(self, record: Record):
        for f in self.filters:
            record = f.match(record)
            if record is None:
                return
        for action in self.actions:
            action.handle(record)


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


def set_logging(log_level):
    log_format = '%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s'
    datefmt = '%Y/%m/%d %H:%M:%S'
    logging.basicConfig(level=log_level, format=log_format, datefmt=datefmt)


def main():
    conf = load_config('new_config.yml')
    conf = SimpleNamespace(**conf)
    set_logging(conf.loglevel)
    db_path = 'test/test.db'

    monitors_names = {
        'rss': (RSSFeedMonitor, Feed)
    }
    filters_names = {
        'match': MatchFilter
    }
    actions_names = {
        'send': SendAny,
        'download': DownloadAny
    }

    monitors = []
    for monitor_type, feeds_configs in conf['Monitors'].items():
        MonitorFactory, FeedFactory = monitors_names.get(monitor_type, (None, None))
        if MonitorFactory is None:
            logging.warning('Unsupported monitor {monitor_type} ignored')
            continue
        feeds = []
        for feed_config in feeds_configs:
            try:
                feed = FeedFactory(feed_config)
                feeds.append(feed)
            except ValueError as e:
                logging.warning(f'In monitor {monitor_type} failed to parse feed {feed_config}: {e}')
                continue
        monitor = MonitorFactory(feeds)
        monitors.append(monitor)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info('stopping on user command')

import logging
import logging.handlers
from pathlib import Path
from typing import Dict, List

from core.utils import check_dir


def set_logging_format(level):
    log_format = '%(asctime)s.%(msecs)03d [%(levelname)-7s] [%(name)s] %(message)s'
    datefmt = '%Y/%m/%d %H:%M:%S'
    logging.basicConfig(level=level, format=log_format, datefmt=datefmt)

def set_file_logger(path: Path, name: str = 'avtdl.log', max_size=1000000):
    check_dir(path, create=True)
    path /= name
    log_format = '%(asctime)s.%(msecs)03d [%(levelname)s] [%(name)s] %(message)s'
    datefmt = '%Y/%m/%d %H:%M:%S'
    formatter = logging.Formatter(log_format, datefmt)
    try:
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=max_size, backupCount=10, encoding='utf8')
    except Exception as e:
        logging.error(f'writing log to {path.absolute()} failed: {e}')
        return
    handler.setFormatter(formatter)
    handler.setLevel(logging.DEBUG)
    logging.getLogger('').addHandler(handler)
    logging.info(f'writing verbose log to file {path.absolute()}')

def set_logger(name, level, propagate=True):
    logger = logging.getLogger(name)
    logger.propagate = propagate
    logger.setLevel(level)

def silence_library_loggers():
    set_logger('asyncio', logging.INFO, propagate=False)
    set_logger('charset_normalizer', logging.INFO, propagate=False)
    set_logger('slixmpp', logging.ERROR, propagate=False)

class LogFilter(logging.Filter):

    def __init__(self, names: List):
        super().__init__()
        self.names = names

    def filter(self, record: logging.LogRecord):
        for name in self.names:
            if record.name.startswith(name):
                return False
        return True

def blacklist_loggers(loggers_names: List[str]):
    root = logging.getLogger('')
    for handler in root.handlers:
        handler.addFilter(LogFilter(loggers_names))
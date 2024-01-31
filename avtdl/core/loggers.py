import logging
import logging.handlers
from enum import Enum
from pathlib import Path
from typing import Dict, List, Union

from avtdl.core.utils import check_dir


class LogLevel(str, Enum):
    debug = 'DEBUG'
    info = 'INFO'
    warning = 'WARNING'
    error = 'ERROR'


def set_logging_format(level):
    log_format = '%(asctime)s.%(msecs)03d [%(levelname)-7s] [%(name)s] %(message)s'
    datefmt = '%Y/%m/%d %H:%M:%S'
    handler = logging.StreamHandler()
    handler.setLevel(level)
    formatter = logging.Formatter(log_format, datefmt)
    handler.setFormatter(formatter)
    logging.getLogger().addHandler(handler)
    
    logging.getLogger().name = 'avtdl'
    logging.getLogger().setLevel(logging.NOTSET)

def set_file_logger(path: Path, name: str = 'avtdl.log', max_size=1000000, level: LogLevel = LogLevel.debug):
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
    handler.setLevel(getattr(logging, level))
    logging.getLogger().addHandler(handler)
    logging.info(f'writing verbose log to file {path.absolute()}')

def set_logger(name: str, level: Union[LogLevel, int], propagate=True):
    if isinstance(level, LogLevel):
        # since LogLevel itself should only contain valid log level names
        # it should never raise AttributeError, but if it does let it crash
        # so it can get noticed
        level = getattr(logging, level)
    logger = logging.getLogger(name)
    logger.propagate = propagate
    logger.setLevel(level)

def silence_library_loggers():
    set_logger('asyncio', logging.WARNING)
    set_logger('charset_normalizer', logging.WARNING)
    set_logger('slixmpp', logging.ERROR)

class LogFilter(logging.Filter):

    def __init__(self, names: List):
        super().__init__()
        self.names = names

    def filter(self, record: logging.LogRecord):
        for name in self.names:
            if record.name.startswith(name):
                return False
        return True

def override_loglevel(loggers: Dict[str, LogLevel]):
        for name, level in loggers.items():
            set_logger(name, level, propagate=True)

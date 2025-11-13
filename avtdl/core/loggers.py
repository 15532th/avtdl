import logging
import logging.handlers
from enum import Enum
from pathlib import Path
from typing import Dict, Optional, Union

from avtdl.core.utils import check_dir


class LogLevel(str, Enum):
    debug = 'DEBUG'
    info = 'INFO'
    warning = 'WARNING'
    error = 'ERROR'


class LoggingConfig:
    """
    Static class used to hold references to logger handlers

    Keeping references to handlers allows to call setup methods
    multiple times, enabling restarting the app with new config
    by removing existing handlers before attaching new ones.
    """

    LOG_FORMAT = '%(asctime)s.%(msecs)03d [%(levelname)-7s] [%(name)s] %(message)s'
    ACCESS_LOG_FORMAT = '%(message)s'
    DATE_FORMAT = '%Y/%m/%d %H:%M:%S'

    stream_handler: Optional[logging.StreamHandler] = None
    file_handler: Optional[logging.FileHandler] = None
    access_handler: Optional[logging.FileHandler] = None

    @classmethod
    def setup_console_logger(cls, level):
        root = logging.getLogger()
        if cls.stream_handler is not None:
            root.removeHandler(cls.stream_handler)
        cls.stream_handler = logging.StreamHandler()
        cls.stream_handler.setLevel(level)
        formatter = logging.Formatter(cls.LOG_FORMAT, cls.DATE_FORMAT)
        cls.stream_handler.setFormatter(formatter)
        root.addHandler(cls.stream_handler)
        root.name = 'avtdl'
        root.setLevel(logging.NOTSET)

    @classmethod
    def create_file_handler(cls, path: Path, name: str, max_size, level: LogLevel, log_format: str, date_format: str) -> Optional[logging.FileHandler]:
        check_dir(path, create=True)
        path /= name
        try:
            handler = logging.handlers.RotatingFileHandler(path, maxBytes=max_size, backupCount=10, encoding='utf8')
        except Exception as e:
            logging.error(f'writing log to {path.absolute()} failed: {e}')
            return None
        formatter = logging.Formatter(log_format, date_format)
        handler.setFormatter(formatter)
        handler.setLevel(getattr(logging, level))
        return handler

    @classmethod
    def setup_file_logger(cls, path: Path, name: str, max_size, level: LogLevel):
        handler = cls.create_file_handler(path, name, max_size, level, cls.LOG_FORMAT, cls.DATE_FORMAT)

        logger = logging.getLogger()
        if cls.file_handler is not None:
            logger.removeHandler(cls.file_handler)
        cls.file_handler = handler
        if cls.file_handler is not None:
            logger.addHandler(cls.file_handler)
            logging.info(f'writing verbose log to file {(path / name).absolute()}')

    @classmethod
    def setup_webserver_logger(cls, path: Path, name: str, max_size, level: LogLevel):
        handler = cls.create_file_handler(path, name, max_size, level, cls.ACCESS_LOG_FORMAT, cls.DATE_FORMAT)

        logger = logging.getLogger('aiohttp.access')
        if cls.access_handler is not None:
            logger.removeHandler(cls.access_handler)
        cls.access_handler = handler
        if cls.access_handler is None:
            return
        logger.addHandler(cls.access_handler)
        logger.propagate = False
        logging.info(f'writing access log to file {(path / name).absolute()}')


def setup_console_logger(level):
    """
    Set up stdout logging format

    Called before config was parsed, sets up
    console logger based on command line arguments
    """
    LoggingConfig.setup_console_logger(level)


def setup_file_logger(path: Path, max_size, level: LogLevel):
    """
    Set up logging to file based on configuration parameters
    """
    LoggingConfig.setup_file_logger(path, 'avtdl.log', max_size, level)


def setup_webserver_logger(path: Path, max_size, level: LogLevel):
    """
    Set up access log of the webui webserver
    """
    LoggingConfig.setup_webserver_logger(path, 'access.log', max_size, level)


def set_logger_loglevel(logger_name: str, level: Union[LogLevel, int], propagate=True):
    if isinstance(level, LogLevel):
        # since LogLevel itself should only contain valid log level names
        # it should never raise AttributeError, but if it does let it crash
        # so it can get noticed
        level = getattr(logging, level)
    logger = logging.getLogger(logger_name)
    logger.propagate = propagate
    logger.setLevel(level)


def silence_library_loggers():
    set_logger_loglevel('asyncio', logging.WARNING)
    set_logger_loglevel('charset_normalizer', logging.WARNING)
    set_logger_loglevel('slixmpp', logging.ERROR)


def override_loglevel(loggers: Dict[str, LogLevel]):
    for name, level in loggers.items():
        set_logger_loglevel(name, level, propagate=True)

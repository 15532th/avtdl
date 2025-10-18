import logging
import pickle
from pathlib import Path
from typing import Optional, Protocol, Type, TypeVar

from pydantic import BaseModel, ValidationError

from avtdl.core.utils import check_dir, format_validation_error


class StateSerializable(Protocol):
    """
    Protocol to allow storing and restoring object state on disk

    The directory argument provides path to the directory used to
    store data, leaving the filename to be decided by the implementations.

    Implementations must not raise errors related to file storing,
    reading and parsing, but may warn user about it.
    """

    def dump_state(self, directory: Path):
        """Serialize the current state and write it to directory"""

    def apply_state(self, directory: Path):
        """Load serialized state from directory and apply it to the object"""


DataType = TypeVar('DataType', bound=BaseModel)


class StateSerializer:
    """Utility class for handling storing and loading state"""
    logger = logging.getLogger().getChild('state_storage')

    @staticmethod
    def serialize(obj: object) -> bytes:
        return pickle.dumps(obj)

    @classmethod
    def dump(cls, obj: DataType, path: Path) -> bool:
        """Store state to file"""
        try:
            cls.logger.debug(f'storing runtime state to {path}')
            data = cls.serialize(obj)
        except Exception as e:
            cls.logger.warning(f'failed to serialize state to "{path}": {e}')
            cls.logger.debug(f'object that failed to serialize: {obj}', exc_info=True)
            return False
        try:
            check_dir(path.parent)
            path.write_bytes(data)
            return True
        except OSError as e:
            cls.logger.warning(f'failed to store state to "{path}": {e}')
            return False

    @staticmethod
    def deserialize(data: bytes):
        return pickle.loads(data)

    @classmethod
    def restore(cls, Model: Type[DataType], path: Path) -> Optional[DataType]:
        """Load state from file and apply to the object"""
        if not path.exists():
            cls.logger.debug(f'skipping restore: "{path}" does not exist')
            return None
        cls.logger.info(f'restoring runtime state from {path}')
        try:
            raw_data = path.read_bytes()
        except (OSError, UnicodeDecodeError) as e:
            cls.logger.warning(f'failed to read state from "{path}": {e}')
            return None
        try:
            obj = cls.deserialize(raw_data)
            return Model.model_validate(obj)
        except pickle.UnpicklingError as e:
            cls.logger.warning(f'failed to load state from "{path}": {e}')
            return None
        except ValidationError as e:
            msg = f'failed to parse state from "{path}": '
            cls.logger.warning(format_validation_error(e, msg))
            return None
        except Exception as e:
            cls.logger.warning(f'error restoring state from "{path}": {e}')
            return None

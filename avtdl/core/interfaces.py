import abc
import datetime
import json
import logging
from abc import abstractmethod
from hashlib import sha1
from textwrap import shorten
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny, field_validator, model_validator

MAX_REPR_LEN = 60


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(tz=datetime.timezone.utc)


class Record(BaseModel):
    '''Data entry, passed around from Monitors to Actions through Filters'''

    model_config = ConfigDict(use_attribute_docstrings=True)

    origin: Optional[str] = Field(default=None, exclude=True)
    """semicolon-separated names of actor and entity record originated from"""
    chain: str = Field(default='', exclude=True)
    """name of the Chain this record is going through.
    Empty string means it was just produced and should go to every subscriber"""
    created_at: datetime.datetime = Field(default_factory=utcnow, exclude=True)
    """record creation timestamp"""
    class_name: str = Field(default='', validate_default=True, exclude=True)
    """class name of specific Record implementation, used for deserialization"""

    @field_validator('class_name')
    @classmethod
    def set_class_name(cls, _: str) -> str:
        return cls.__name__

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        all_known_record_types[cls.__name__] = cls

    @abstractmethod
    def __str__(self) -> str:
        '''Text representation of the record to be sent in a message, written to a file etc.'''

    @abstractmethod
    def __repr__(self) -> str:
        '''Short text representation of the record to be printed in logs'''

    def __eq__(self, other) -> bool:
        if not isinstance(other, Record):
            return NotImplemented
        return self.model_dump() == other.model_dump()

    def get_uid(self) -> str:
        '''A string that is the same for different versions of the same record'''
        return self.hash()

    def as_timezone(self, timezone: Optional[datetime.tzinfo] = None) -> 'Record':
        fields = dict(self)
        for k, v in fields.items():
            if isinstance(v, Record):
                fields[k] = v.as_timezone(timezone)
            if isinstance(v, datetime.datetime):
                fields[k] = v.astimezone(timezone)
        record_copy = self.model_validate(fields)
        return record_copy

    def as_json(self, indent: Union[int, str, None] = None) -> str:
        return json.dumps(self.model_dump(), sort_keys=True, ensure_ascii=False, default=str, indent=indent)

    def as_embed(self) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
        embed_title_max_length = 256
        embed_description_max_length = 4096

        text = str(self)
        if text.find('\n') > -1:
            title, description = text.split('\n', 1)
        else:
            title, description = '', text
        title = shorten(title, embed_title_max_length)
        description = shorten(description, embed_description_max_length)
        return {'title': title, 'description': description}

    def hash(self) -> str:
        record_hash = sha1(self.as_json().encode())
        return record_hash.hexdigest()


all_known_record_types: Dict[str, type[Record]] = {}


def get_record_type(name: str) -> Optional[type[Record]]:
    """return Record descendant class with given class name"""
    return all_known_record_types.get(name)


class TextRecord(Record):
    """
    Simplest record, containing only a single text field
    """

    text: str
    """content of the record"""

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        return f'TextRecord("{shorten(self.text, MAX_REPR_LEN)}")'


class EventType:
    generic: str = 'generic'
    error: str = 'error'
    started: str = 'started'
    finished: str = 'finished'


class Event(Record):
    """
    Record produced by an internal event (usually error) inside the plugin
    """

    event_type: str = EventType.generic
    """text describing the nature of event, can be used to filter classes of events, such as errors"""
    text: str
    """text describing specific even details"""
    record: SerializeAsAny[Optional[Record]] = Field(exclude=True, default=None)
    """record that was being processed when this event happened"""

    def __str__(self):
        return self.text

    def __repr__(self):
        text = shorten(self.text, MAX_REPR_LEN)
        return f'Event(event_type="{self.event_type}", text="{text}")'

    def model_post_init(self, __context):
        if self.record is not None:
            self.origin = self.record.origin
            self.chain = self.record.chain


class OpaqueRecord(Record):
    """Record without predefined fields and structure"""
    model_config = ConfigDict(extra='allow')

    @model_validator(mode='before')
    @classmethod
    def check_fields_overwrite(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for name, value in data.items():
                if name in cls.model_fields:
                    logger = logging.getLogger('OpaqueRecord')
                    logger.warning(f'internal field "{name}" was overwritten by user-defined value "{value}"')
        return data

    def __str__(self) -> str:
        return self.model_dump_json(indent=4)

    def __repr__(self) -> str:
        fields = ', '.join((f'{field}={value}' for field, value in self.model_dump().items()))
        return f'{self.__class__.__name__}({fields})'


class AbstractRecordsStorage(abc.ABC):
    """Interface for accessing persistent records storage from web ui"""

    @abstractmethod
    def feeds(self) -> List[Tuple[str, int]]:
        """return names and number of records of distinct feeds storage currently has"""

    @abstractmethod
    def page_count(self, per_page: int, feed: Optional[str] = None) -> int:
        """return total number of pages"""

    @abstractmethod
    def load_page(self, page: Optional[int], per_page: int, desc: bool = True, feed: Optional[str] = None) -> List[
        Record]:
        """return content of specific page as a list of Record instances"""

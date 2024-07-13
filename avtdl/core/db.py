import datetime
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Union

from pydantic import Field, field_validator, model_validator

from avtdl.core.interfaces import ActorConfig, Record
from avtdl.core.utils import check_dir


class BaseRecordDB:
    table_name = 'records'
    table_structure = 'parsed_at datetime, feed_name text, uid text, hashsum text, class_name text, as_json text, PRIMARY KEY(uid, hashsum)'
    row_structure = ':parsed_at, :feed_name, :uid, :hashsum, :class_name, :as_json'
    id_field = 'uid'
    exact_id_field = 'hashsum'
    group_id_field = 'feed_name'
    sorting_field = 'parsed_at'

    def __init__(self, db_path: Union[str, Path], logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger('RecordDB')
        try:
            if not db_path == ':memory:' and not Path(db_path).exists():
                check_dir(Path(db_path).parent)
            self.db = sqlite3.connect(db_path)
            self.db.row_factory = sqlite3.Row
            self.cursor = self.db.cursor()
            self.cursor.execute('CREATE TABLE IF NOT EXISTS {} ({})'.format(self.table_name, self.table_structure))
            self.db.commit()
        except sqlite3.OperationalError as e:
            self.logger.error(
                f'error opening sqlite database at path "{db_path}", specified in "db_path" config variable: {e}. If file exists make sure it was produced by this application, otherwise check if new file can be created at specified location. Alternatively use special value ":memory:" to use in-memory database instead.')
            raise
        else:
            self.logger.debug(f'successfully connected to sqlite database at "{db_path}"')

    def store(self, rows: Union[Dict[str, Any], List[Dict[str, Any]]]) -> None:
        sql = "INSERT OR IGNORE INTO {} VALUES({})".format(self.table_name, self.row_structure)
        if not isinstance(rows, list):
            rows = [rows]
        self.cursor.executemany(sql, rows)
        self.db.commit()

    def fetch_row(self, uid: Any, exact_id: Optional[str] = None) -> Optional[sqlite3.Row]:
        if exact_id is not None:
            sql = f'SELECT * FROM records WHERE {self.id_field}=:uid AND {self.exact_id_field}=:exact_id ORDER BY {self.sorting_field} DESC LIMIT 1'
        else:
            sql = f'SELECT * FROM records WHERE {self.id_field}=:uid ORDER BY {self.sorting_field} DESC LIMIT 1'
        keys = {'uid': uid, 'exact_id': exact_id}
        self.cursor.execute(sql, keys)
        return self.cursor.fetchone()

    def row_exists(self, uid: Any, exact_id: Optional[str] = None) -> bool:
        if exact_id is not None:
            sql = f'SELECT 1 FROM records WHERE {self.id_field}=:uid AND {self.exact_id_field}=:exact_id LIMIT 1'
        else:
            sql = f'SELECT 1 FROM records WHERE {self.id_field}=:uid LIMIT 1'
        keys = {'uid': uid, 'exact_id': exact_id}
        self.cursor.execute(sql, keys)
        return self.cursor.fetchone() is not None

    def get_size(self, group_id: Optional[str] = None) -> int:
        '''return number of records, total or for specified feed, are stored in db'''
        if group_id is None:
            sql = f'SELECT COUNT(1) FROM {self.table_name}'
        else:
            sql = f'SELECT COUNT(1) FROM {self.table_name} WHERE {self.group_id_field}=:group'
        keys = {'group': group_id}
        self.cursor.execute(sql, keys)
        return int(self.cursor.fetchone()[0])


class RecordDB(BaseRecordDB):

    @staticmethod
    def _get_record_id(record: Record, entity_name: str) -> str:
        return '{}:{}'.format(entity_name, record.get_uid())

    def store_records(self, records: Sequence[Record], entity_name: str):
        rows = []
        for record in records:
            uid = self._get_record_id(record, entity_name)
            parsed_at = datetime.datetime.now(tz=datetime.timezone.utc)
            hashsum = record.hash()
            feed_name = entity_name
            class_name = record.__class__.__name__
            as_json = record.as_json()
            row = {'parsed_at': parsed_at, 'feed_name': feed_name, 'uid': uid, 'hashsum': hashsum, 'class_name': class_name, 'as_json': as_json}
            rows.append(row)
        self.store(rows)

    def load_record(self, record: Record, entity_name: str) -> Optional[Record]:
        """load most recently stored version of the record from db"""
        uid = self._get_record_id(record, entity_name)
        stored_record = self.fetch_row(uid)
        if stored_record is None:
            return None
        stored_record_instance = type(record).model_validate_json(stored_record['as_json'])
        return stored_record_instance

    def record_exists(self, record: Record, entity_name: str) -> bool:
        uid = self._get_record_id(record, entity_name)
        return self.row_exists(uid)

    def record_got_updated(self, record: Record, entity_name: str) -> bool:
        """return True when there are different versions of the record in db but not this one"""
        uid = self._get_record_id(record, entity_name)
        return self.row_exists(uid) and not self.row_exists(uid, record.hash())

    def record_has_changed(self, record: Record, entity_name: str, excluded_fields: Set[str]):
        """check if the record differs from most recently stored version, not counting fields listed in excluded_fields"""
        stored_record = self.load_record(record, entity_name)
        if stored_record is None:
            return False
        record_dump = record.model_dump(exclude=excluded_fields)
        stored_record_dump = stored_record.model_dump(exclude=excluded_fields)
        return record_dump != stored_record_dump


class BaseDbConfig(ActorConfig):
    db_path: Union[Path, str] = Field(default='db/', validate_default=True)
    """path to the sqlite database file keeping history of old records.
    Might specify a path to a directory containing the file (with trailing slash)
    or a direct path to the file itself (without a slash). If special value `:memory:` is used,
    database is kept in memory and not stored on disk at all, providing a clean database on every startup"""

    @field_validator('db_path')
    @classmethod
    def str_to_path(cls, path: Union[Path, str]):
        if isinstance(path, Path):
            return path
        if path == ':memory:':
            return path
        if path.endswith('/') or path.endswith('\\'):
            ok = check_dir(Path(path), create=True)
            if not ok:
                raise ValueError(f'error accessing path {path}, check if it is a valid path and is writeable')
        return Path(path)

    @model_validator(mode='after')
    def handle_db_directory(self):
        if isinstance(self.db_path, Path) and self.db_path.is_dir():
            self.db_path = self.db_path.joinpath(f'{self.name}.sqlite')
        return self

import datetime
import itertools
import logging
import math
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

from pydantic import Field, field_validator, model_validator

from avtdl.core.interfaces import AbstractRecordsStorage, Action, Actor, ActorConfig, Record
from avtdl.core.plugins import Plugins
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
        self.logger.debug(f'successfully connected to sqlite database at "{db_path}"')
        self.create_indexes()

    def create_indexes(self):
        queries = [
            f'CREATE INDEX IF NOT EXISTS `index_{self.group_id_field}` ON `{self.table_name}` (`{self.group_id_field}`);',
            f'CREATE INDEX IF NOT EXISTS `index_{self.sorting_field}` ON `{self.table_name}` ( `{self.sorting_field}`)',
            f'CREATE INDEX IF NOT EXISTS `index_{self.id_field}_{self.sorting_field}` ON `{self.table_name}` ( `{self.id_field}`, `{self.sorting_field}` )'
        ]
        for query in queries:
            try:
                self.cursor.execute(query)

            except sqlite3.OperationalError as e:
                self.logger.exception(f'failed to create index: {e}. Raw query: {query}')
        self.db.commit()

    def store(self, rows: Union[Dict[str, Any], List[Dict[str, Any]]], replace: bool = False) -> None:
        on_conflict = 'REPLACE' if replace else 'IGNORE'
        sql = "INSERT OR {} INTO {} VALUES({})".format(on_conflict, self.table_name, self.row_structure)
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

    def fetch_offset(self, limit: int, offset: int, group_id: Optional[str] = None, desc: bool = True) -> List[sqlite3.Row]:
        order = 'DESC' if desc else 'ASC'
        if group_id is not None:
            sql = f'SELECT * FROM records WHERE {self.group_id_field}=:group_id ORDER BY {self.sorting_field} {order} LIMIT :limit OFFSET :offset'
        else:
            sql = f'SELECT * FROM records ORDER BY {self.sorting_field} {order} LIMIT :limit OFFSET :offset'
        keys = {'group_id': group_id, 'limit': limit, 'offset': offset}
        self.cursor.execute(sql, keys)
        return self.cursor.fetchall()


@lru_cache(maxsize=1)
def record_types() -> Dict[str, type[Record]]:
    """Return mapping name: type for all record types registered in plugins"""
    associated_records = Plugins.known[Plugins.kind.ASSOCIATED_RECORD]
    return {t.__name__: t for t in itertools.chain(*associated_records.values())}


def calculate_offset(page: Optional[int], per_page: int, total_rows: int) -> Tuple[int, int]:
    if total_rows == 0 or per_page == 0:
        return 0, 0
    last_page = math.ceil(total_rows / per_page)
    if page is None:
        page = last_page
    if page > last_page:
        page = last_page
    if page < 1:
        page = 1
    page_offset = page * per_page
    offset = max(total_rows - page_offset, 0)
    rows_on_last_page = total_rows % per_page or per_page
    limit = per_page if page != last_page else rows_on_last_page
    return limit, offset


class RecordDB(BaseRecordDB):

    @staticmethod
    def _get_record_id(record: Record, entity_name: str) -> str:
        return '{}:{}'.format(entity_name, record.get_uid())

    def store_records(self, records: Sequence[Record], entity_name: str,
                      replace: bool = False, use_created_as_parsed: bool = False):
        rows = []
        for record in records:
            uid = self._get_record_id(record, entity_name)
            parsed_at = datetime.datetime.now(tz=datetime.timezone.utc)
            if use_created_as_parsed:
                parsed_at = record.created_at
            hashsum = record.hash()
            feed_name = entity_name
            class_name = record.__class__.__name__
            as_json = record.as_json()
            row = {'parsed_at': parsed_at, 'feed_name': feed_name, 'uid': uid, 'hashsum': hashsum,
                   'class_name': class_name, 'as_json': as_json}
            rows.append(row)
        self.store(rows, replace)

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

    def page_count(self, entity_name: Optional[str], per_page: int) -> int:
        pages = self.get_size(entity_name) / per_page
        pages = math.ceil(pages)
        return pages

    def parse_record(self, row: sqlite3.Row) -> Optional[Record]:
        type_name = row['class_name']
        record_type = record_types().get(type_name)
        if record_type is None:
            self.logger.warning(f'failed to restore record: unsupported record type "{record_type}')
            row_content = {k: row[k] for k in row.keys()}
            self.logger.debug(f'Raw row: {row_content}')
            return None
        record = record_type.model_validate_json(row['as_json'])
        self.set_record_time(record, row['parsed_at'] or None)
        return record

    @staticmethod
    def set_record_time(record: Record, ts: Optional[str]):
        if ts is None:
            return
        try:
            dt = datetime.datetime.fromisoformat(ts)
        except Exception:
            return
        dt = dt.astimezone(tz=datetime.timezone.utc)
        record.created_at = dt

    def load_page(self, entity_name: Optional[str], page: Optional[int], per_page: int, desc: bool = True) -> List[Record]:
        total_rows = self.get_size(entity_name)

        limit, offset = calculate_offset(page, per_page, total_rows)
        rows = self.fetch_offset(limit, offset, entity_name, desc)
        records = []
        for row in rows:
            record = self.parse_record(row)
            if record is not None:
                records.append(record)
        return records


class BaseDbConfig(ActorConfig):
    db_path: Union[Path, str] = Field(default='db/', validate_default=True)
    """path to the sqlite database file keeping history of old records.
    Might specify a path to a directory containing the file (with trailing slash)
    or a direct path to the file itself (without a slash). If special value `:memory:` is used,
    database is kept in memory and not stored on disk at all, providing a clean database on every startup"""

    @field_validator('db_path')
    @classmethod
    def str_to_path(cls, path: Union[Path, str]):
        return validate_db_path(path)

    @model_validator(mode='after')
    def handle_db_directory(self):
        if isinstance(self.db_path, Path) and self.db_path.is_dir():
            self.db_path = self.db_path.joinpath(f'{self.name}.sqlite')
        return self


def validate_db_path(path: Union[Path, str]) -> Union[Path, str]:
    if isinstance(path, Path):
        return path
    if path == ':memory:':
        return path
    if path.endswith('/') or path.endswith('\\'):
        ok = check_dir(Path(path), create=True)
        if not ok:
            raise ValueError(f'error accessing path {path}, check if it is a valid path and is writeable')
    return Path(path)


class HistoryView(AbstractRecordsStorage):

    def __init__(self, actor: Actor, entity_name: str):
        self.actor = actor
        self.entity_name = entity_name

    def _get_records(self, entity_name: str) -> List[Record]:
        direction = 'in' if isinstance(self.actor, Action) else 'out'
        records = self.actor.bus.get_history(self.actor.conf.name, entity_name, '', direction)  # type: ignore
        return records

    def page_count(self, per_page: int) -> int:
        return math.ceil(len(self._get_records(self.entity_name)) / per_page)

    def load_page(self, page: Optional[int], per_page: int, desc: bool = True) -> List[Record]:
        records = self._get_records(self.entity_name)
        limit, offset = calculate_offset(page, per_page, len(records))
        page_records = records[offset:offset + limit]
        if desc:
            page_records = page_records[::-1]
        return page_records


class RecordDbView(AbstractRecordsStorage):

    def __init__(self, db: RecordDB, entity_name: Optional[str]):
        self.db = db
        self.entity_name = entity_name

    def page_count(self, per_page: int) -> int:
        return self.db.page_count(self.entity_name, per_page)

    def load_page(self, page: Optional[int], per_page: int, desc: bool = True) -> List[Record]:
        return self.db.load_page(self.entity_name, page, per_page, desc)

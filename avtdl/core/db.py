import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from avtdl.core.utils import check_dir


class BaseRecordDB:
    table_name = 'records'
    table_structure = 'parsed_at datetime, feed_name text, uid text, hashsum text, class_name text, as_json text, PRIMARY KEY(uid, hashsum)'
    row_structure = ':parsed_at, :feed_name, :author, :video_id, :url, :title, :summary, :published, :updated, :scheduled, :views'
    id_field = 'uid'
    exact_id_field = 'hashsum'
    group_id_field = 'feed_name'
    sorting_field = 'parsed_at'

    def __init__(self, db_path: Union[str,Path], logger: Optional[logging.Logger] = None):
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

    def fetch_row(self, uid: Any, exact_id: Optional[Any] = None) -> Optional[sqlite3.Row]:
        if exact_id is not None:
            sql = f'SELECT * FROM records WHERE {self.id_field}=:uid AND {self.exact_id_field}=:exact_id ORDER BY {self.sorting_field} DESC LIMIT 1'
        else:
            sql = f'SELECT * FROM records WHERE {self.id_field}=:uid ORDER BY {self.sorting_field} DESC LIMIT 1'
        keys = {'uid': uid, 'exact_id': exact_id}
        self.cursor.execute(sql, keys)
        return self.cursor.fetchone()

    def row_exists(self, uid: Any, exact_id: Optional[Any] = None) -> bool:
        if exact_id is not None:
            sql = f'SELECT 1 FROM records WHERE {self.id_field}=:uid AND {self.exact_id_field}=:exact_id LIMIT 1'
        else:
            sql = f'SELECT 1 FROM records WHERE {self.id_field}=:uid LIMIT 1'
        keys = {'uid': uid, 'exact_id': exact_id}
        self.cursor.execute(sql, keys)
        return self.cursor.fetchone() is not None

    def get_size(self, group: Optional[Any] = None) -> int:
        '''return number of records, total or for specified feed, are stored in db'''
        if group is None:
            sql = f'SELECT COUNT(1) FROM {self.table_name}'
        else:
            sql = f'SELECT COUNT(1) FROM {self.table_name} WHERE {self.group_id_field}=:group'
        keys = {'group': group}
        self.cursor.execute(sql, keys)
        return int(self.cursor.fetchone()[0])

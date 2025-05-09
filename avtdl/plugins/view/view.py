from pathlib import Path
from typing import Optional, Sequence, Union

from pydantic import Field, field_validator, model_validator

from avtdl.core.db import RecordDB, RecordDbView, validate_db_path
from avtdl.core.interfaces import AbstractRecordsStorage, Action, ActionEntity, ActorConfig, Record, RuntimeContext
from avtdl.core.plugins import Plugins

Plugins.register('view', Plugins.kind.ASSOCIATED_RECORD)(Record)


@Plugins.register('view', Plugins.kind.ACTOR_CONFIG)
class ViewConfig(ActorConfig):
    pass


@Plugins.register('view', Plugins.kind.ACTOR_ENTITY)
class ViewEntity(ActionEntity):
    db_path: Union[Path, str] = Field(default='db/', validate_default=True)
    """path to the sqlite database file storing records.
    Might specify a path to a directory containing the file (with trailing slash)
    or a direct path to the file itself (without a slash). If special value `:memory:` is used,
    database is kept in memory and not stored on disk at all, providing a clean database on every startup"""
    readonly: bool = False
    """when enabled, prevents any writes to the database"""
    replace: bool = True
    """when exactly the same record is received more than once, the latest copy will overwrite already stored one,
    updating the time the record was stored"""
    use_creation_timestamp: bool = False
    """use time record was initially parsed at instead of the moment it was stored in the database for chronological sorting"""

    @field_validator('db_path')
    @classmethod
    def str_to_path(cls, path: Union[Path, str]):
        return validate_db_path(path)

    @model_validator(mode='after')
    def handle_db_directory(self):
        if isinstance(self.db_path, Path) and self.db_path.is_dir():
            self.db_path = self.db_path.joinpath(f'view/{self.name}.sqlite')
        return self


@Plugins.register('view', Plugins.kind.ACTOR)
class View(Action):
    """
    View records in the web interface

    Incoming records are stored in sqlite database at db_path (unless readonly parameter enabled).
    The stored records are available on the "View" tab of the web interface.

    This plugin is under active development. The way it works might change, options might be
    renamed or removed.
    """

    def __init__(self, conf: ViewConfig, entities: Sequence[ViewEntity], ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)
        self.databases = {}
        for entity in entities:
            db = RecordDB(entity.db_path, logger=self.logger.getChild('name'))
            self.databases[entity.name] = db

    def handle(self, entity: ViewEntity, record: Record):
        if entity.readonly:
            self.logger.debug(f'[{entity.name}] readonly mode, the following record will not be stored: {record!r}')
            return
        db = self.databases.get(entity.name)
        if db is None:
            self.logger.exception(
                f'no database is opened for entity {entity.name}, the following record will not be stored: {record!r}')
            return
        if record.origin:
            origin_items = record.origin.split(':', maxsplit=1)
            entity_name = origin_items[-1]  # take entire origin value if no separator present
        else:
            entity_name = entity.name
        db.store_records([record], entity_name, entity.replace, use_created_as_parsed=entity.use_creation_timestamp)

    def get_records_storage(self, entity_name: Optional[str] = None) -> Optional[AbstractRecordsStorage]:
        if entity_name not in self.databases:
            return None
        # entity_name is not specified because db is going to store records originating from different entities
        return RecordDbView(self.databases[entity_name], None)

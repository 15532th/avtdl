import asyncio
import logging
import os
import re
import shutil
from pathlib import Path
from typing import List, Mapping, Optional, Sequence

from pydantic import AnyUrl, Field, NonNegativeFloat, RootModel, ValidationError, field_validator, model_validator

from avtdl.core.actions import QueueAction, QueueActionConfig, QueueActionEntity
from avtdl.core.cache import FileCache, find_free_suffix, find_with_suffix
from avtdl.core.config import SettingsSection
from avtdl.core.download import RemoteFileInfo, download_file, has_same_content, remove_files
from avtdl.core.formatters import Fmt, sanitize_filename
from avtdl.core.interfaces import Record, RuntimeContext
from avtdl.core.plugins import Plugins
from avtdl.core.request import HttpClient
from avtdl.core.utils import check_dir, is_url, sha1


@Plugins.register('download', Plugins.kind.ACTOR_CONFIG)
class FileDownloadConfig(QueueActionConfig):
    max_concurrent_downloads: int = Field(default=1, ge=1)
    """limit for simultaneously active download tasks among all entities. Note that each entity will still process records sequentially regardless of this setting"""
    partial_file_suffix: str = '.part'
    """appended to a name of the file that is not yet completely downloaded"""


@Plugins.register('download', Plugins.kind.ACTOR_ENTITY)
class FileDownloadEntity(QueueActionEntity):
    url_field: str
    """field in the incoming record containing url of file to be downloaded"""
    path: Path
    """directory where downloaded file should be created. Supports templating with {...}"""
    filename: Optional[str] = None
    """name downloaded file should be stored under. If not provided will be inferred from HTTP headers or download url. Supports templating with {...} (additionally, "{source_name}" placeholder will be replaced with the inferred value)"""
    extension: Optional[str] = None
    """normally file extension will be inferred from HTTP headers. This option allows to overwrite it"""
    overwrite: bool = False
    """whether file should be overwritten in if it already exists. If set to false will cause suffix with a number be added to the newly downloaded file name"""
    rename_suffix: str = ' [{i}]'
    """when overwriting is disabled, this suffix is attached to the base filename with the "{i}" part replaced with a number. Must contain "{i}" exactly once"""

    @field_validator('extension')
    @classmethod
    def ensure_dot(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if value.startswith('.'):
            return value
        return '.' + value

    @field_validator('rename_suffix')
    @classmethod
    def check_suffix(cls, value: str) -> str:
        found = len(re.findall(r'{i}', value))
        if found != 1:
            raise ValueError('rename_suffix must contain exactly one occurrence of "{i}", got ' + str(found))
        value = sanitize_filename(value)
        return value


@Plugins.register('download', Plugins.kind.ACTOR)
class FileDownload(QueueAction):
    """
    Download a file

    Take an url from a field of a processed record with a name specified in `url_field`
    and download it as a file to specified location.
    The field must be present in the record and  must contain a valid url with a scheme
    (such as "https") or a list of such urls.

    Primarily designed for downloading images attached to a post, or thumbnails.
    Does not support resuming interrupted downloads or detecting that this exact file is
    already stored at target location without downloading it again.

    File extension and name are inferred from HTTP headers and path part of the url,
    unless provided explicitly with `extension` and `filename` parameters.
    Since final file name is determined as a part of the download process, the file
    is initially stored under a temporary name (currently an SHA1 of the url) in the
    download directory, and then renamed to target filename.

    If a file with given name already exists, depending on an `overwrite` setting a new file will either
    overwrite it or get stored under different name, generated by combining
    the base name with a number added as part of `rename_suffix`.
    If, however, an exact copy of the new file is found among the files in target directory
    sharing the base name, the new file will be deleted, giving preference to the existing copy.
    """

    def __init__(self, conf: FileDownloadConfig, entities: Sequence[FileDownloadEntity], ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)
        self.conf: FileDownloadConfig
        self.entities: Mapping[str, FileDownloadEntity]
        self.concurrency_limit = asyncio.BoundedSemaphore(value=conf.max_concurrent_downloads)

    def _get_urls_list(self, entity: FileDownloadEntity, record: Record) -> Optional[List[str]]:
        field = getattr(record, entity.url_field, None)
        if field is None:
            msg = f'received a record that does not contain "{entity.url_field}" field. The record: {record!r}'
            self.logger.debug(msg)
            return None
        if isinstance(field, str):
            field = [field]
        try:
            urls = [str(url) for url in UrlList(field)]
            return urls
        except ValidationError:
            self.logger.debug(
                f'received record with the "{entity.url_field}" field that was not recognised as a valid url or a sequence of urls. Raw record: {record!r}')
            return None

    async def handle_single_record(self, logger: logging.Logger, client: HttpClient,
                                   entity: FileDownloadEntity, record: Record) -> None:
        urls = self._get_urls_list(entity, record)
        if urls is None:
            self.logger.debug(f'found no values in field "{entity.url_field}", skipping record')
            return
        for url in urls:
            self.logger.debug(f'processing url {url}')
            await self.handle_download(logger, client, entity, record, url)

    async def handle_download(self, logger, client: HttpClient, entity: FileDownloadEntity, record: Record, url: str):
        """
        Handle download-related stuff: generating filenames, moving files, error reporting

        - generate tempfile name from url hash
        - if it exists abort
        - perform the download into a temp file
        - generate resulting file name
        - if exists either rename or replace depending on settings
        """
        path = Fmt.format_path(entity.path, record, tz=entity.timezone)
        ok = check_dir(path)
        if not ok:
            logger.warning(f'check "{path}" is a valid and writeable directory')
            return

        temp_file = path / Path(sha1(url)).with_suffix(self.conf.partial_file_suffix)
        if temp_file.exists():
            logger.warning(
                f'aborting download of "{url}": temporary file "{temp_file}" already exists, meaning download is already in progress or download process has been interrupted abruptly')
            return

        logger.debug(f'downloading "{url}" to "{temp_file}"')
        info = await self.download(logger, client, url, temp_file)
        if info is None:
            return None

        if entity.filename is not None:
            extra = {'source_name': info.source_name}
            filename = Fmt.format(entity.filename, record, tz=entity.timezone, extra=extra)
        else:
            filename = info.source_name
        filename = sanitize_filename(filename)
        path = path.joinpath(filename)
        if entity.extension is not None:
            path = path.with_suffix(entity.extension)
        else:
            path = path.with_suffix(info.extension)

        try:
            path.exists()
        except OSError as e:
            logger.warning(f'failed to process record: {e}')
            return
        if path.exists() and not entity.overwrite:
            for p in path.parent.iterdir():
                if not p.stem.startswith(path.stem):
                    continue
                if has_same_content(temp_file, p):
                    self.logger.info(f'file "{temp_file}" is already stored as "{p}", deleting')
                    remove_files([temp_file])
                    return
            new_path = Path(path)  # making a copy
            i = 0
            while new_path.exists():
                i += 1
                suffix = entity.rename_suffix.replace('{i}', str(i))
                new_name = path.stem + suffix
                new_path = new_path.with_stem(new_name)
            path = new_path
        move_file(temp_file, path, logger)

    async def download(self, logger: logging.Logger, client: HttpClient,
                       url: str, output_file: Path) -> Optional[RemoteFileInfo]:
        """Perform the actual download"""
        return await download(self.concurrency_limit, logger, client, url, output_file)


async def download(semaphore: asyncio.BoundedSemaphore, logger: logging.Logger, client: HttpClient,
                   url: str, output_file: Path) -> Optional[RemoteFileInfo]:
    try:
        async with semaphore:
            logger.debug(
                f'acquired semaphore({semaphore._value}), downloading "{url}" to "{output_file}"')
            info = await download_file(url, output_file, client.session)
    except Exception as e:
        logger.exception(f'unexpected error when downloading "{url}" to "{output_file}": {e}')
        return None
    logger.debug(
        f'finished downloading "{url}" to "{output_file}", semaphore({semaphore._value}) released')
    return info


class UrlList(RootModel):
    root: Sequence[AnyUrl]

    def __iter__(self):
        return iter(self.root)

    def __getitem__(self, item):
        return self.root[item]


def move_file(source: Path, target: Path, logger: logging.Logger) -> bool:
    try:
        logger.debug(f'moving "{source}" to "{target}"')
        os.replace(source, target)
        return True
    except Exception as e:
        message = f'failed to move file "{source}" to desired location "{target}": {e}'
        logger.warning(message)
        return False


@Plugins.register('cache', Plugins.kind.ACTOR_CONFIG)
class FileCacheConfig(FileDownloadConfig):
    pass


@Plugins.register('cache', Plugins.kind.ACTOR_ENTITY)
class FileCacheEntity(QueueActionEntity):
    url_fields: List[str] = ['attachments', 'thumbnail_url', 'avatar_url']
    """names of fields in the incoming record containing urls of files to be downloaded.
    Field must contain url, list of urls, or a nested record to look for fields names into"""
    replace_after: Optional[NonNegativeFloat] = None
    """how old existing file should be to get redownloaded, in hours"""
    consume_record: bool = False
    """whether record should be consumed or passed down the chain after processing"""
    import_path: Optional[str] = None
    """path to external location to look for a file before downloading. Supports templating with '{...}'"""
    import_filename: Optional[str] = None
    """name (without extension) of external file to use instead of downloading.
    If file exists, it is copied to the cache directory once for every processed url. Supports templating with '{...}'"""
    import_rename_suffix: str = ' [{i}]'
    """for fields containing list of urls, this suffix is added to import_filename template to look for additional files.
    Must contain {i} exactly once"""
    export_path: Optional[str] = None
    """path to external location to store a copy of cached file. Supports templating with '{...}'"""
    export_filename: Optional[str] = None
    """name used to store a copy of cached file externally. Supports templating with '{...}'"""
    export_rename_suffix: str = ' [{i}]'
    """if export_filename already exists, this suffix is used to generate a new, unique name.
    Must contain {i} exactly once"""

    @model_validator(mode='after')
    def check_paths(self):
        if (self.import_path and not self.import_filename) or (not self.import_path and self.import_filename):
            raise ValueError(f'both import_path and import_filename should be present if specified')
        if (self.export_path and not self.export_filename) or (not self.export_path and self.export_filename):
            raise ValueError(f'both export_path and export_filename should be present if specified')
        return self


@Plugins.register('cache', Plugins.kind.ACTOR)
class FileCacheAction(QueueAction):
    """
    Cache url locally

    For every incoming record, go through fields specified in "url_fields" setting
    and download files the urls are pointing to. Downloaded files are stored under
    the path, defined by the cache_directory parameter in application-wide settings,
    where they are used to present record in the web interface.

    It is possible to reuse files already stored by the "download" plugin with
    import_path/import_filename template options, though it might not work well
    with the "attachments" field. A copy of cached file can be
    stored to external location by providing export_path/export_filename templates.

    The way files are stored internally might change in the future, leaving already
    stored files inaccessible by the web interface. Use export_path/export_filename
    and import_path/import_filename options to define external persistent storage
    layout, that doesn't depend on the cache format.
    """

    def __init__(self, conf: FileCacheConfig, entities: Sequence[FileCacheEntity], ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)
        self.conf: FileCacheConfig
        self.entities: Mapping[str, FileCacheEntity]
        self.concurrency_limit = asyncio.BoundedSemaphore(value=conf.max_concurrent_downloads)
        settings: Optional[SettingsSection] = ctx.get_extra('settings')
        if settings is None:
            raise RuntimeError(f'runtime context is missing Settings instance. This is a bug, please report it')
        self.cache = FileCache(settings.cache_directory, self.conf.partial_file_suffix)

    async def handle_single_record(self, logger: logging.Logger, client: HttpClient,
                                   entity: FileCacheEntity, record: Record) -> None:
        for field in entity.url_fields:
            await self._handle_field(logger, client, entity, record, field)

    async def _handle_field(self, logger: logging.Logger, client: HttpClient,
                            entity: FileCacheEntity, record: Record, field_name: str):
        field = getattr(record, field_name, None)
        if field is None:
            logger.debug(f'no field "{field_name}" in record {record!r}, skipping')
        elif isinstance(field, str):
            await self._cache_urls(logger, client, entity, record, [field])
        elif isinstance(field, list):
            await self._cache_urls(logger, client, entity, record, field)
        elif isinstance(field, Record):
            await self.handle_single_record(logger, client, entity, field)
        else:
            msg = f'field "{field_name}" of record {record!r} does not seem to hold any links. Raw field value: {field}'
            logger.debug(msg)

    def _find_external_files(self, entity: FileCacheEntity, record: Record) -> List[Path]:
        if entity.import_path and entity.import_filename:
            external_path = Fmt.format_filename(entity.import_path, entity.import_filename, record)
            external_files = find_with_suffix(external_path, entity.import_rename_suffix)
            self.logger.debug(f'[{entity.name}] external files found: {len(external_files)}')
            return external_files
        return []

    def _export(self, entity: FileCacheEntity, record: Record, cache_path: Optional[Path]):
        if cache_path is None:
            return
        if not entity.export_path or not entity.export_filename:
            return
        export_path = Fmt.format_filename(entity.export_path, entity.export_filename, record)
        export_path = find_free_suffix(export_path, entity.export_rename_suffix)
        export_path = export_path.with_suffix(cache_path.suffix)
        try:
            self.logger.debug(f'[{entity.name}] creating a copy of "{cache_path}" at "{export_path}"')
            shutil.copy2(cache_path, export_path)
        except OSError as e:
            self.logger.warning(f'[{entity.name}] failed to copy "{cache_path}" to "{export_path}": {e}')

    async def _cache_urls(self, logger: logging.Logger, client: HttpClient,
                          entity: FileCacheEntity, record: Record, maybe_urls: List[str]) -> None:
        urls = []
        for url in maybe_urls:
            if is_url(url):
                urls.append(url)
            else:
                logger.debug(f'"{url}" does not seem to be a valid url, skipping. Record: {record!r}')

        external_files: Sequence[Optional[Path]] = self._find_external_files(entity, record)
        if not external_files or len(external_files) != len(urls):
            external_files = [None] * len(urls)

        for url, external_file in zip(urls, external_files):
            async with self.concurrency_limit:
                cache_path = await self.cache.store(logger, client, record, url, entity.replace_after, external_file)
            self._export(entity, record, cache_path)


import asyncio
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import aiohttp
from pydantic import AnyUrl, Field, FilePath, RootModel, ValidationError, field_validator

from avtdl.core.download import RemoteFileInfo, download_file, has_same_content, remove_files
from avtdl.core.interfaces import Action, ActionEntity, ActorConfig, Event, Record
from avtdl.core.plugins import Plugins
from avtdl.core.utils import Fmt, check_dir, convert_cookiejar, load_cookies, monitor_tasks, sanitize_filename, sha1

Plugins.register('download', Plugins.kind.ASSOCIATED_RECORD)(Event)


@Plugins.register('download', Plugins.kind.ACTOR_CONFIG)
class FileDownloadConfig(ActorConfig):
    max_concurrent_downloads: int = Field(default=1, ge=1)
    """limit for simultaneously active download tasks among all entities"""
    partial_file_suffix: str = '.part'
    """appended to a name of the file that is not yet completely downloaded"""


@Plugins.register('download', Plugins.kind.ACTOR_ENTITY)
class FileDownloadEntity(ActionEntity):
    url_field: str
    """field in the incoming record containing url of file to be downloaded"""
    cookies_file: Optional[FilePath] = None
    """path to a text file containing cookies in Netscape format"""
    headers: Optional[Dict[str, str]] = {}
    """custom HTTP headers as pairs "key": value". "Set-Cookie" header will be ignored, use `cookies_file` option instead"""
    path: Path
    """directory where downloaded file should be created. Supports templating with {...}"""
    filename: Optional[str] = None
    """name downloaded file should be stored under. If not provided will be inferred from HTTP headers or download url. Supports templating with {...}"""
    extension: Optional[str] = None
    """normally file extension will be inferred from HTTP headers. This option allows to overwrite it"""
    overwrite: bool = False
    """whether file should be overwritten in if it already exists. If set to false will cause suffix with a number be added to the newly downloaded file name"""
    rename_suffix: str = ' [{i}]'
    """when overwriting is disabled, this suffix is attached to base filename with the "{i}" part replaced with a number. Must contain "{i}" exactly once"""

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
            raise ValueError('rename_suffix must contain exactly one occurrence of "{i}", got '  + str(found))
        value = sanitize_filename(value)
        return value

@Plugins.register('download', Plugins.kind.ACTOR)
class FileDownload(Action):
    """
    Download a file

    Take an url from a record field and download it as a file to specified location.
    Field must contain a valid url with a scheme or a list of such urls.
    """

    def __init__(self, conf: FileDownloadConfig, entities: Sequence[FileDownloadEntity]):
        super().__init__(conf, entities)
        self.concurrency_limit = asyncio.BoundedSemaphore(value=conf.max_concurrent_downloads)
        self.queues: Dict[str, asyncio.Queue] = {entity.name: asyncio.Queue() for entity in entities}

    def handle(self, entity: FileDownloadEntity, record: Record):
        try:
            queue = self.queues[entity.name]
            queue.put_nowait(record)
            self.logger.debug(f'[{entity.name}] added new record to the queue, current queue size is {queue.qsize()}')
        except (asyncio.QueueFull, KeyError) as e:
            self.logger.exception(f'[{entity.name}] failed to add url, {type(e)}: {e}. This is a bug, please report it.')

    def _get_urls_list(self, entity: FileDownloadEntity, record: Record) -> Optional[List[str]]:
        field = getattr(record, entity.url_field, None)
        if field is None:
            msg = f'[{entity.name}] received a record that does not contain "{entity.url_field}" field. The record: {record!r}'
            self.logger.debug(msg)
            return None
        if isinstance(field, str):
            field = [field]
        try:
            urls = [str(url) for url in UrlList(field)]
            return urls
        except ValidationError:
            self.logger.debug(f'[{entity.name}] received record with the "{entity.url_field}" field that was not recognised as a valid url or a sequence of urls. Raw record: {record!r}')
            return None

    @staticmethod
    def _initialize_session(entity: FileDownloadEntity) -> aiohttp.ClientSession:
        netscape_cookies = load_cookies(entity.cookies_file)
        cookies = convert_cookiejar(netscape_cookies) if netscape_cookies else None
        session = aiohttp.ClientSession(cookie_jar=cookies, headers=entity.headers)
        return session

    async def run_for(self, entity: FileDownloadEntity):
        try:
            session = self._initialize_session(entity)
            queue = self.queues[entity.name]
            async with session:
                while True:
                    record = await queue.get()
                    self.logger.debug(f'[{entity.name}] processing record {record!r}')
                    self.logger.debug(f'[{entity.name}] {queue.qsize()} records left waiting in the queue')
                    urls = self._get_urls_list(entity, record)
                    if urls is None:
                        self.logger.debug(f'[{entity.name}] found no values in field "{entity.url_field}", skipping record')
                        continue
                    for url in urls:
                        self.logger.debug(f'[{entity.name}] processing url {url}')
                        await self.handle_download(session, entity, record, url)
        except Exception:
            self.logger.exception(f'[{entity.name}] unexpected error in background task, terminating')

    async def handle_download(self, session: aiohttp.ClientSession, entity: FileDownloadEntity, record: Record, url: str):
        """
        Handle download-related stuff: generating filenames, moving files, error reporting

        - generate tempfile name from url hash
        - if it exists abort
        - perform the download into a temp file
        - generate resulting file name
        - if exists either rename or replace depending on settings
        """
        path = Fmt.format_path(entity.path, record)
        ok = check_dir(path)
        if not ok:
            self.logger.warning(f'[{entity.name}] check "{path}" is a valid and writeable directory')
            return

        temp_file = path / Path(sha1(url)).with_suffix(self.conf.partial_file_suffix)
        if temp_file.exists():
            self.logger.warning(f'[{entity.name}] aborting download of "{url}": temporary file "{temp_file}" already exists, meaning download is already in progress or download process has been interrupted abruptly')
            return

        self.logger.debug(f'[{entity.name}] downloading "{url}" to "{temp_file}"')
        info = await self.download(session, url, temp_file)
        if info is None:
            return None

        if entity.filename is not None:
            filename = Fmt.format(entity.filename, record)
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
            self.logger.warning(f'[{entity.name}] failed to process record: {e}')
            return
        if path.exists() and not entity.overwrite:
            for p in path.parent.glob(path.stem + '*'):
                if has_same_content(temp_file, p):
                    self.logger.info(f'[{entity.name}] file "{temp_file}" is already stored as "{p}", deleting')
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
        try:
            self.logger.debug(f'[{entity.name}] moving "{temp_file}" to "{path}"')
            os.replace(temp_file, path)
        except Exception as e:
            message = f'[{entity}]: when downloading "{url}" failed to move file "{temp_file}" to desired location "{path}": {e}'
            self.logger.warning(message)

    async def download(self, session: aiohttp.ClientSession, url: str, output_file: Path) -> Optional[RemoteFileInfo]:
        """Perform the actual download"""
        try:
            self.logger.debug(f'waiting for semaphore({self.concurrency_limit._value}) to download "{url}"')
            async with self.concurrency_limit:
                self.logger.debug(f'acquired semaphore({self.concurrency_limit._value}), downloading "{url}" to "{output_file}"')
                info = await download_file(url, output_file, session)
        except Exception as e:
            self.logger.exception(f'unexpected error when downloading "{url}" to "{output_file}": {e}')
            return None
        self.logger.debug(f'finished downloading "{url}" to "{output_file}", semaphore({self.concurrency_limit._value}) released')
        return info

    async def run(self):
        tasks = []
        for entity in self.entities.values():
            task = asyncio.create_task(self.run_for(entity), name=f'{self.conf.name}:{entity.name}')
            tasks.append(task)
        await monitor_tasks(tasks)


class UrlList(RootModel):
    root: Sequence[AnyUrl]

    def __iter__(self):
        return iter(self.root)

    def __getitem__(self, item):
        return self.root[item]

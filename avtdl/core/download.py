import asyncio
import logging
import mimetypes
import os
import shutil
import urllib.parse
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

import aiohttp
import multidict
from pydantic import BaseModel

from avtdl.core.utils import sha1

CHUNK_SIZE = 1024 ** 3


def remove_files(files: Sequence[Path]):
    for file in files:
        if not file.exists():
            continue
        try:
            os.remove(file)
        except OSError as e:
            logging.getLogger('download').debug(f'failed to delete "{file}": {e}')


def has_same_content(file1: Path, file2: Path) -> bool:
    """return True if file1 and file2 are both files and have the same content"""
    try:
        if not file1.exists() or not file2.exists():
            return False
        if file1.is_dir() or file2.is_dir():
            return False
        if file1.lstat().st_size != file2.lstat().st_size:
            return False
        with (file1.open('rb') as fp1, file2.open('rb') as fp2):
            while True:
                chunk1 = fp1.read(CHUNK_SIZE)
                chunk2 = fp2.read(CHUNK_SIZE)
                if chunk1 != chunk2:
                    return False
                elif chunk1 and chunk2:
                    continue
                else:
                    return True
    except OSError:
        return False


async def download_file(url: str, path: Path, session: aiohttp.ClientSession, headers: Optional[Dict[str, Any]] = None, logger: Optional[logging.Logger] = None) -> Optional['RemoteFileInfo']:
    logger = logger or logging.getLogger('download')
    try:
        timeout = aiohttp.ClientTimeout(total=0, connect=60, sock_connect=60, sock_read=60)
        async with session.get(url, timeout=timeout, headers=headers) as response:
            response.raise_for_status()
            remote_info = RemoteFileInfo.from_url_response(url, response.headers)
            logger.debug(f'downloading {str(remote_info.content_length) + " bytes" or ""} from "{url}"')
            with open(path, 'w+b') as fp:
                async for data in response.content.iter_chunked(CHUNK_SIZE):
                    fp.write(data)
    except (OSError, asyncio.TimeoutError, aiohttp.ClientConnectionError, aiohttp.ClientResponseError) as e:
        logger.warning(f'failed to download "{url}": {type(e)} {e}')
        return None
    except Exception as e:
        logger.exception(f'failed to download "{url}": {e}')
        return None
    return remote_info


class CachedFile(BaseModel):
    url: str
    size: int
    metadata_name: Path
    local_name: Path
    source_name: Optional[str] = None
    response_headers: dict

    @classmethod
    def from_file(cls, path: Path) -> Optional['CachedFile']:
        if not path.exists():
            return None
        with open(path, 'rt', encoding='utf8') as fp:
            text = fp.read()
            info = CachedFile.model_validate_json(text)
            if info.local_name.exists():
                return info
            else:
                raise FileNotFoundError(f'cached file not found: {info.local_name}')

    def to_file(self, path: Path) -> None:
        text = self.model_dump_json(indent=4)
        with open(path, 'wt', encoding='utf8') as fp:
            fp.write(text)


class FileStorage:
    METADATA_EXTENSION = '.info'
    PARTIAL_EXTENSION = '.part'

    def __init__(self, cache_directory: Path, logger: Optional[logging.Logger] = None):
        self._cache = cache_directory
        self.logger = logger or logging.getLogger('download')

    async def download(self, url: str, session: aiohttp.ClientSession, headers: Optional[Dict[str, Any]] = None) -> Optional[CachedFile]:
        file_info = self.get_cached_file(url)
        if file_info is not None:
            self.logger.debug(f'found in local cache: "{url}"')
            return file_info
        path = self._get_filename_prefix(url).with_suffix(self.PARTIAL_EXTENSION)
        remote_info = await download_file(url, path, session, headers)
        if remote_info is None:
            return None
        downloaded_file = self._add_downloaded_file(path, remote_info)
        return downloaded_file

    def get_cached_file(self, url: str) -> Optional[CachedFile]:
        metadata_path = self._get_filename_prefix(url).with_suffix(self.METADATA_EXTENSION)
        try:
            # CachedFile if cache hit, None if cache miss
            metadata = CachedFile.from_file(metadata_path)
            return metadata
        except Exception as e:
            self.logger.debug(f'error loading metadata for "{url}" from "{metadata_path}"')
            return None

    def _add_downloaded_file(self, file: Path, info: 'RemoteFileInfo') -> Optional[CachedFile]:
        if not file.exists():
            return None
        base_name = self._get_filename_prefix(info.url)
        extension = info.extension or ''
        local_info = CachedFile(
            url=info.url,
            source_name=info.source_name,
            size=file.stat().st_size,
            metadata_name=base_name.with_suffix(self.METADATA_EXTENSION),
            local_name=base_name.with_suffix(extension),
            response_headers=info.response_headers
        )
        if local_info.local_name.exists():
            self.logger.debug(f'overwriting existing file: "{local_info.local_name}"')
        try:
            shutil.move(file, local_info.local_name)
        except Exception as e:
            self.logger.warning(f'failed to move "{file}" to "{local_info.local_name}": {e}')
            remove_files([file, local_info.local_name])
            return None
        if local_info.metadata_name.exists():
            self.logger.debug(f'overwriting existing metadata file: "{local_info.metadata_name}"')
        try:
            local_info.to_file(local_info.metadata_name)
        except Exception as e:
            self.logger.warning(f'failed to write metadata for "{local_info.url}" to "{local_info.metadata_name}": {e}')
            remove_files([file, local_info.local_name])
            return None
        return local_info

    def _get_filename_prefix(self, url: str) -> Path:
        name = sha1(url)
        path = self._cache.joinpath(name)
        return path


class RemoteFileInfo(BaseModel):
    url: str
    source_name: str
    extension: str = ''
    content_length: Optional[int] = None
    response_headers: dict

    @classmethod
    def from_url_response(cls, url: str, headers: multidict.CIMultiDictProxy) -> 'RemoteFileInfo':
        size = headers.get('Content-Length')
        filename = cls.extract_filename(url, headers)
        if filename is None:
            name, extension = '', ''
        else:
            name, extension = filename.stem, filename.suffix
        return cls(url=url, content_length=size, source_name=name, extension=extension, response_headers=dict(headers))

    @classmethod
    def extract_filename(cls, url: str, headers: multidict.CIMultiDictProxy) -> Optional[Path]:
        filename = cls._get_filename_from_headers(headers) or cls._get_filename_from_url(url)
        if filename is not None and not filename.suffix:
            extension = cls._get_mime_extension(headers)
            if extension is not None:
                filename = filename.with_suffix(extension)
        return filename

    @staticmethod
    def _get_filename_from_headers(headers: multidict.CIMultiDictProxy) -> Optional[Path]:
        """get filename from Content-Disposition, with or without extension"""
        content_disposition = headers.get('Content-Disposition') or ''
        email = EmailMessage()
        email.add_header('Content-Disposition', content_disposition)
        name = email.get_filename()
        if name is not None:
            filename = Path(name)
            filename = Path(filename.name)
            return filename
        else:
            return None

    @staticmethod
    def _get_filename_from_url(url: str) -> Optional[Path]:
        """try extracting filename part from the url"""
        try:
            path = urllib.parse.urlparse(url).path
            if not path:
                return None
            path = urllib.parse.unquote_plus(path)
            filename = Path(path)
            if not filename.name:
                return None
            filename = Path(filename.name)
            return filename
        except Exception as e:
            return None

    @staticmethod
    def _get_mime_extension(headers: multidict.CIMultiDictProxy) -> Optional[str]:
        """return file extension deduced from Content-Type header"""
        mime_extension = None
        content_type = headers.get('Content-Type')
        if content_type is not None:
            extension = mimetypes.guess_extension(content_type, strict=False)
            if extension and extension.startswith('.'):
                mime_extension = extension
        return mime_extension

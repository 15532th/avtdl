import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

import aiohttp
from pydantic import BaseModel

from avtdl.core.request import RemoteFileInfo, download_file
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

    async def download(self, url: str, session: aiohttp.ClientSession, headers: Optional[Dict[str, Any]] = None) -> \
    Optional[CachedFile]:
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

    def _add_downloaded_file(self, file: Path, info: RemoteFileInfo) -> Optional[CachedFile]:
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

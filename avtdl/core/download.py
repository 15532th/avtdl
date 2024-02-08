import hashlib
import json
import logging
import mimetypes
import urllib.parse
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

import aiohttp
import multidict
from pydantic import BaseModel


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode()).digest().hex()


def download_file(session: aiohttp.ClientSession, url: str) -> Optional[Path]:
    return None


def download(session: aiohttp.ClientSession, url: str, cache: 'LocalFileCache') -> Optional['CachedFile']:
    local = cache.get_local_file(url)
    if local is not None:
        return local
    downloaded_file = download_file(session, url)



class CachedFile(BaseModel):
    url: str
    size: int
    metadata_name: Path
    local_name: Path
    source_name: Optional[Path] = None


class LocalFileCache:

    def __init__(self, cache_directory: Path, logger: Optional[logging.Logger] = None):
        self._cache = cache_directory
        self.logger = logger or logging.getLogger('download')

    def get_filename_prefix(self, url: str) -> Path:
        name = sha1(url)
        path = self._cache.joinpath(name)
        return path

    def get_local_file(self, url: str) -> Optional[CachedFile]:
        metadata_path = self.get_filename_prefix(url).with_suffix('.info')
        if not metadata_path.exists():
            return None
        try:
            with open(metadata_path, 'rt', encoding='utf8') as fp:
                text = json.load(fp)
                info = CachedFile.model_validate_json(text)
                if info.local_name.exists():
                    return info
                else:
                    return None
        except Exception as e:
            return None

    def add_downloaded_file(self, file: Path, info: RemoteFileInfo) -> Optional[CachedFile]:
        if not file.exists():
            return None



#################################################33

class RemoteFileInfo(BaseModel):
    url: str
    source_name: Optional[Path] = None
    content_length: Optional[int] = None


def get_file_info(url: str, headers: multidict.CIMultiDictProxy) -> RemoteFileInfo:
    size = headers.get('Content-Length')
    name = get_filename(url, headers)
    return RemoteFileInfo(url=url, content_length=size, source_name=name)


def get_filename(url: str, headers: multidict.CIMultiDictProxy) -> Optional[Path]:
    filename = get_filename_from_headers(headers) or get_filename_from_url(url)
    if filename is not None and not filename.suffix:
        extension = get_mime_extension(headers)
        if extension is not None:
            filename = filename.with_suffix(extension)
    return filename


def get_filename_from_headers(headers: multidict.CIMultiDictProxy) -> Optional[Path]:
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


def get_filename_from_url(url: str) -> Optional[Path]:
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


def get_mime_extension(headers: multidict.CIMultiDictProxy) -> Optional[str]:
    """return file extension deduced from Content-Type header"""
    mime_extension = None
    content_type = headers.get('Content-Type')
    if content_type is not None:
        extension = mimetypes.guess_extension(content_type, strict=False)
        if extension and extension.startswith('.'):
            mime_extension = extension
    return mime_extension


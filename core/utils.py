import logging
from http import cookiejar
from pathlib import Path
from typing import Optional


def load_cookies(path: Path, raise_on_error: bool = False) -> Optional[cookiejar.CookieJar]:
    cookie_jar = cookiejar.MozillaCookieJar(path)
    try:
        cookie_jar.load()
        logging.info(f"Successfully loaded cookies from {path}")
    except FileNotFoundError:
        if raise_on_error:
            raise
        return None
    except (cookiejar.LoadError, OSError) as e:
        if raise_on_error:
            raise
        logging.exception(f'Failed to load cookies from {path}: {e}')
        return None
    return cookie_jar
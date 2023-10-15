import logging
import re
from datetime import datetime
from textwrap import shorten
from typing import Sequence, Optional

import aiohttp
import lxml.html
from pydantic import FilePath, Field, ValidationError

from core.interfaces import Record, MAX_REPR_LEN
from core.monitors import BaseFeedMonitorEntity, BaseFeedMonitor, BaseFeedMonitorConfig
from core.plugins import Plugins

GMAIL_SIMPLE_INTERFACE_URL = 'https://mail.google.com/mail/u/0/h/'


class SimpleGmailRecord(Record):
    address_from: str
    subject: str
    text_preview: str
    relative_date: Optional[str]

    def __str__(self) -> str:
        return f'{self.relative_date} [{self.address_from}] {self.subject} {self.text_preview}'

    def __repr__(self) -> str:
        return shorten(str(self), MAX_REPR_LEN)

    def hash(self) -> str:
        data = self.model_dump()
        data['relative_date'] = None
        return SimpleGmailRecord.model_validate(data).hash()



@Plugins.register('gmail', Plugins.kind.ACTOR_CONFIG)
class NitterMonitorConfig(BaseFeedMonitorConfig):
    pass

@Plugins.register('gmail', Plugins.kind.ACTOR_ENTITY)
class SimpleGmailEntity(BaseFeedMonitorEntity):
    cookies_file: FilePath
    update_interval: float = 900
    url: str = Field(default=GMAIL_SIMPLE_INTERFACE_URL, exclude=True)


@Plugins.register('gmail', Plugins.kind.ACTOR)
class SimpleGmail(BaseFeedMonitor):

    async def get_records(self, entity: SimpleGmailEntity, session: aiohttp.ClientSession) -> Sequence[SimpleGmailRecord]:
        page = await self._get_page(entity, session)
        if page is None:
            return []
        entries = self._parse_page(page, entity.url)
        messages = []
        for entry in entries:
            message = self._parse_message(entry)
            if message is not None:
                messages.append(message)
        return messages

    def get_record_id(self, record: SimpleGmailRecord) -> str:
        return record.hash()

    async def _get_page(self, entity: SimpleGmailEntity, session: aiohttp.ClientSession) -> Optional[str]:
        response = await self.request(entity.url, entity, session)
        if response is None:
            return None
        page = await response.text()
        return page

    def _parse_page(self, raw_page: str, base_url: str) -> Sequence[lxml.html.HtmlElement]:
       root = lxml.html.fromstring(raw_page)
       messages = root.xpath("//table[@class='th']/tr")
       return messages

    def _parse_message(self, raw_message: lxml.html.HtmlElement) -> Optional[SimpleGmailRecord]:
        try:
            _, name, text, date = raw_message.xpath("./td")
            address_from = name.text_content()
            address_from = re.sub('\(\d+\)$', '', address_from)
            text_preview = text.xpath(".//font[@color='#7777CC']")[0].text_content()
            subject_end = text.text_content().find(text_preview)
            subject = text.text_content()[:subject_end]
            relative_date = date.text_content()
            return SimpleGmailRecord(address_from=address_from, subject=subject, text_preview=text_preview, relative_date=relative_date)
        except (IndexError, TypeError, ValidationError) as e:
            logging.exception(f'failed to parse message: {e}')
            return None
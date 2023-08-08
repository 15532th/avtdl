import asyncio
import logging
from dataclasses import dataclass
from typing import List

import slixmpp

ON_ERROR_RETRY_DELAY = 60

def instantiate_loggers(names, level):
    '''
    Make instance of logger with specific name and loglevel
    before it gets done by someone else. Used to make
    logging.getLogger(name) call return instance with
    loglevel different from default.

    This script relies on logging.basicConfig() to set default
    logger level and format, so there is no need to pass
    these settings between modules.

    While aioxmpp.Client() accepts `logger` argument, allowing
    to specify loglevel, some underlying modules and libraries
    just create loggers by themself, using default loglevel
    set by logging.basicConfig().

    This leads to debug messages from aioxmpp modules being
    produced when loglevel is set to DEBUG for this script.
    '''
    for name in names:
        logger = logging.getLogger(name)
        logger.setLevel(level)

@dataclass
class Line:
    recipient: str
    message: str

class MSG2JBR:

    def __init__(self, username, passwd):
        self.user = username
        self.passwd = passwd
        self.send_query = []
        instantiate_loggers('slixmpp', logging.ERROR)

    def to_be_send(self, recipient, message):
        line = Line(recipient, message)
        self.send_query.append(line)

    async def asend_pending(self):
        if len(self.send_query) == 0:
            return
        client = SendMsgBot(self.user, self.passwd, messages=self.send_query)
        client.connect()
        await client.disconnected
        self.send_query = []

    async def run(self):
        while True:
            try:
                await self.asend_pending()
            except Exception:
                logging.exception(f'failed to send jabber messages')
                await asyncio.sleep(ON_ERROR_RETRY_DELAY)
            await asyncio.sleep(1)


class SendMsgBot(slixmpp.ClientXMPP):

    def __init__(self, jid: str, password: str, messages: List[Line]):
        super().__init__(jid, password)
        self.messages = messages
        self.add_event_handler('session_start', self.start)

    async def start(self, event):
        self.send_presence()
        await self.get_roster()
        for line in self.messages:
            recipient = slixmpp.JID(line.recipient)
            self.send_message(mto=recipient, mbody=line.message, mtype='chat')
        await self.disconnect()

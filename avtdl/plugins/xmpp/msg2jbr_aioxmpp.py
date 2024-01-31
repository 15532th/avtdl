import asyncio
import logging
from dataclasses import dataclass
import sys

import aioxmpp

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
    recepient: str
    message: str

class MSG2JBR:

    @staticmethod
    async def asend(msg, user, passwd, recepient):
        '''send string or list of strings msg to recepient using user/passwd as credentials'''
        if isinstance(msg, str):
            msg = [msg, ]
        if not msg:
            return
        client = aioxmpp.PresenceManagedClient(aioxmpp.JID.fromstr(user), aioxmpp.make_security_layer(passwd))
        async with client.connected():
            for line in msg:
                message = aioxmpp.Message(to=aioxmpp.JID.fromstr(recepient), type_=aioxmpp.MessageType.CHAT)
                message.body[None] = line
                await client.send(message)

    @staticmethod
    def send(msg, user, passwd, recepient):
        '''send blocking'''
        asyncio.get_event_loop().run_until_complete(MSG2JBR.asend(msg, user, passwd, recepient))

    def __init__(self, username, passwd, logger=None):
        self.user = username
        self.passwd = passwd
        self.send_query = []
        self.can_send = (username is not None and passwd is not None and 'aioxmpp' in sys.modules)
        instantiate_loggers(['aioopenssl', 'aiosasl', 'aioxmpp'], logging.ERROR)
        self.logger = logger or logging.getLogger('msg2jbr.aioxmpp')

    def to_be_send(self, recepient, message):
        line = Line(recepient, message)
        self.send_query.append(line)

    async def asend_pending(self):
        if not self.can_send:
            for line in self.send_query:
                warning = 'jabber module required but unable to send message to {}: {}'
                self.logger.debug(warning.format(line.recepient, line.message))
            self.send_query = []
            return
        if not self.send_query:
            return
        client = aioxmpp.PresenceManagedClient(aioxmpp.JID.fromstr(self.user), aioxmpp.make_security_layer(self.passwd))
        async with client.connected():
            while self.send_query:
                line = self.send_query.pop()
                message = aioxmpp.Message(to=aioxmpp.JID.fromstr(line.recepient), type_=aioxmpp.MessageType.CHAT)
                message.body[None] = line.message
                await client.send(message)

    async def run(self):
        while True:
            try:
                await self.asend_pending()
            except Exception:
                self.logger.exception(f'failed to send jabber messages')
                await asyncio.sleep(ON_ERROR_RETRY_DELAY)
            await asyncio.sleep(1)

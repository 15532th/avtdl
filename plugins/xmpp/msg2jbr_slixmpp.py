import asyncio
import logging
from dataclasses import dataclass
from typing import List

import slixmpp

ON_ERROR_RETRY_DELAY = 60

@dataclass
class Line:
    recipient: str
    message: str

class MSG2JBR:

    def __init__(self, username, passwd, logger=None):
        self.user = username
        self.passwd = passwd
        self.send_query = []
        self.logger = logger or logging.getLogger('msg2jbr')

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
                self.logger.exception(f'failed to send jabber messages')
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

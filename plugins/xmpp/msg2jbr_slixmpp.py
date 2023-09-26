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
        client = SendMsgBot(self.user, self.passwd, messages=self.send_query, logger=self.logger.getChild('slixmmp'))
        client.connect()
        await client.disconnected
        self.send_query = []

    async def run(self):
        while True:
            try:
                await self.asend_pending()
            except Exception as e:
                self.logger.exception(f'failed to send jabber messages: {e}')
                await asyncio.sleep(ON_ERROR_RETRY_DELAY)
            await asyncio.sleep(1)


class SendMsgBot(slixmpp.ClientXMPP):

    def __init__(self, jid: str, password: str, messages: List[Line], logger=None):
        super().__init__(jid, password)
        self.logger = logger or logging.getLogger('SendMsgBot')
        self.messages = messages
        self.register_plugin('xep_0333')
        self.add_event_handler('session_start', self.start)
        self.set_error_messages()

    async def start(self, event):
        self.send_presence()
        await self.get_roster()
        for line in self.messages:
            recipient = slixmpp.JID(line.recipient)
            self.send_message(mto=recipient, mbody=line.message, mtype='chat')
        await self.disconnect()

    def set_error_messages(self):
        error_messages = {'connection_failed': 'failed to connect',
                          'reconnect_delay': 'next connection attempt in',
                          'failed_all_auth': f'authentication failed for {self.boundjid.bare}',
                          'stream_error': 'sending messages failed',
                          'message_error': 'got error message from jabber server',
                          'message': 'got message',
                          'marker': 'marker'
                          }
        for event, message in error_messages.items():
            self.add_event_handler(event, self.get_error_handler(message))

    def get_error_handler(self, message):
        def error_handler(event):
            msg = f'{message}: {event}' if event else f'{message}'
            self.logger.debug(msg)
        return error_handler

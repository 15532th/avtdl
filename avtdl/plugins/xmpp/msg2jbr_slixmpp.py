import asyncio
import logging
import sys
from dataclasses import dataclass
from typing import Callable, Optional

import slixmpp

if sys.platform == 'win32':
    slixmpp.xmlstream.resolver.AIODNS_AVAILABLE = False

ON_ERROR_RETRY_DELAY = 60
DISCONNECT_AFTER_DONE_DELAY = 30


@dataclass
class Line:
    recipient: str
    message: str


class MSG2JBR:
    def __init__(self, username: str, passwd: str, logger: Optional[logging.Logger] = None):
        self.user = username
        self.passwd = passwd
        self.logger = logger or logging.getLogger('msg2jbr')
        # JabberClient gets initialized in async run() instead of __init__
        # to make sure it uses same event loop as run() instead of making its own,
        # making it possible to await JabberClient.disconnected there
        self.jabber: Optional[JabberClient] = None

    def to_be_send(self, recipient: str, message: str) -> None:
        if self.jabber is None:
            self.logger.debug(f'not running yet, message discarded: (to: {recipient}) "{message[:50]}"')
            return
        line = Line(recipient, message)
        self.jabber.send_query.put_nowait(line)

    async def run(self) -> None:
        self.jabber = JabberClient(self.user, self.passwd, self.logger.getChild('slixmpp'))
        while True:
            pending = self.jabber.send_query.qsize()
            if pending > 0:
                self.logger.debug(f'connecting to send {pending} pending messages')
                self.jabber.connect()
                try:
                    await asyncio.wait_for(self.jabber.disconnected, DISCONNECT_AFTER_DONE_DELAY * 10)
                except asyncio.TimeoutError:
                    self.logger.warning(f'sending messages takes too long, aborting to retry later')
                else:
                    self.logger.debug(f'done sending messages, disconnected')
            if self.jabber.fatal_error is not None:
                self.logger.warning(f'{self.jabber.fatal_error}, terminating')
                break
            pending = self.jabber.send_query.qsize()
            if pending > 0:
                self.logger.debug(f'{pending} messages left after disconnect, delaying next attempt')
                await asyncio.sleep(ON_ERROR_RETRY_DELAY)
            await asyncio.sleep(1)


class JabberClient(slixmpp.ClientXMPP):

    def __init__(self, username: str, passwd: str, logger: Optional[logging.Logger] = None) -> None:
        super().__init__(username, passwd)
        self.logger = logger or logging.getLogger('slixmppClient')
        self.send_query: asyncio.Queue = asyncio.Queue()
        self.fatal_error: Optional[str] = None
        self.add_event_handler('session_start', self.send_pending)
        self.add_event_handler('failed_all_auth', self.on_bad_auth)
        self.add_error_handlers()
        self.now_sending = False

    async def send_pending(self, _) -> None:
        self.logger.debug('got session_start event')
        if self.now_sending:
            self.logger.warning(f'session_start handler got called but now_sending={self.now_sending}, aborting')
            return
        self.now_sending = True
        try:
            self.send_presence()
            await self.get_roster()
            while True:
                line: Line = await asyncio.wait_for(self.send_query.get(), DISCONNECT_AFTER_DONE_DELAY)
                recipient = slixmpp.JID(line.recipient)
                self.send_message(mto=recipient, mbody=line.message, mtype='chat')
                self.logger.debug(f'sending message: {str(line)[:90]}')
                if self.send_query.empty():
                    self.logger.debug('put all pending messages in internal queue, waiting a bit for new ones')
        except asyncio.TimeoutError:
            self.logger.debug('disconnecting')
            await self.disconnect()
        except Exception as e:
            self.logger.exception(f'got error while sending messages: {e}')
        finally:
            self.now_sending = False

    def on_bad_auth(self, _) -> None:
        self.fatal_error = f'authentication failed for {self.boundjid.bare}'

    def add_error_handlers(self) -> None:
        error_messages = {'connection_failed': 'failed to connect',
                          'reconnect_delay': 'next connection attempt in',
                          'stream_error': 'stream error',
                          'killed': 'XML stream got aborted',
                          'message_error': 'got error message from jabber server',
                          'message': 'got message',
                          }
        for event, message in error_messages.items():
            self.add_event_handler(event, self.make_error_handler(message))

    def make_error_handler(self, message: str) -> Callable:
        def error_handler(event):
            msg = f'{message}: {event}' if event else f'{message}'
            self.logger.debug(msg)
        return error_handler

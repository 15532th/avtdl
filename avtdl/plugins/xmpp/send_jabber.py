import logging
from typing import Sequence

from avtdl.core.config import Plugins
from avtdl.core.interfaces import Action, ActionEntity, ActorConfig, Record

try:
    from avtdl.plugins.xmpp.msg2jbr_slixmpp import MSG2JBR
except ImportError:
    try:
        from avtdl.plugins.xmpp.msg2jbr_aioxmpp import MSG2JBR
    except ImportError:
        msg = f'No supported Jabber library installed or ImportError happened. Supported libraries are slixmpp and aioxmpp'
        logging.error(msg)
        raise ImportError(msg, name=__name__)


@Plugins.register('xmpp', Plugins.kind.ACTOR_CONFIG)
class JabberConfig(ActorConfig):
    xmpp_username: str
    """JID of the account to be used to send messages, including resource"""
    xmpp_pass: str
    """password of the account to be used to send messages"""


@Plugins.register('xmpp', Plugins.kind.ACTOR_ENTITY)
class JabberEntity(ActionEntity):
    jid: str
    """JID to send message to"""


@Plugins.register('xmpp', Plugins.kind.ACTOR)
class SendJabber(Action):
    """
    Send records as Jabber messages

    Converts records to a text representation and sends them as messages
    to specified recipients. Sends each record in a separate message,
    does not impose any limits on frequency or size of messages, leaving
    it to server side.
    """

    def __init__(self, conf: JabberConfig, entities: Sequence[JabberEntity]):
        super().__init__(conf, entities)
        self.jbr = MSG2JBR(conf.xmpp_username, conf.xmpp_pass, self.logger)

    def handle(self, entity: JabberEntity, record: Record):
        self.jbr.to_be_send(entity.jid, str(record))

    async def run(self):
        await self.jbr.run()


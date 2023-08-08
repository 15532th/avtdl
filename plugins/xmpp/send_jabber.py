import logging
from typing import Sequence

from core.config import Plugins
from core.interfaces import ActorConfig, Record, ActorEntity, Actor
try:
    from plugins.xmpp.msg2jbr_slixmpp import MSG2JBR
except ImportError:
    try:
        from plugins.xmpp.msg2jbr_aioxmpp import MSG2JBR
    except ImportError:
        msg = f'No supported Jabber library installed or ImportError happened. Supported libraries are slixmpp and aioxmpp'
        logging.error(msg)
        raise ImportError(msg, name=__name__)


@Plugins.register('xmpp', Plugins.kind.ACTOR_CONFIG)
class JabberConfig(ActorConfig):
    xmpp_username: str
    xmpp_pass: str

@Plugins.register('xmpp', Plugins.kind.ACTOR_ENTITY)
class JabberEntity(ActorEntity):
    name: str
    jid: str

@Plugins.register('xmpp', Plugins.kind.ACTOR)
class SendJabber(Actor):
    def __init__(self, conf: JabberConfig, entities: Sequence[JabberEntity]):
        super().__init__(conf, entities)
        self.jbr = MSG2JBR(conf.xmpp_username, conf.xmpp_pass)

    def handle(self, entity_name: str, record: Record):
        if entity_name not in self.entities:
            raise ValueError(f'Unable run command for {entity_name}: no entity found')
        entity = self.entities[entity_name]
        self.jbr.to_be_send(entity.jid, str(record))

    async def run(self):
        await self.jbr.run()


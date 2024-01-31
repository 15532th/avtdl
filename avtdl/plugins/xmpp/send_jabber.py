import datetime
import logging
from typing import Optional, Sequence

import dateutil
from pydantic import field_validator

from avtdl.core.config import Plugins
from avtdl.core.interfaces import Actor, ActorConfig, ActorEntity, Record

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
    """JID of the account to be used to send messages, resource included"""
    xmpp_pass: str
    """password of the account to be used to send messages"""

@Plugins.register('xmpp', Plugins.kind.ACTOR_ENTITY)
class JabberEntity(ActorEntity):
    jid: str
    """JID to send message to"""
    timezone: Optional[str] = None
    """takes timezone name from <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones> or OS settings if omitted, converts record fields containing date and time to this timezone"""

    @field_validator('timezone')
    @classmethod
    def check_timezone(cls, timezone: str) -> datetime.timezone:
        tz = dateutil.tz.gettz(timezone)
        if tz is None:
            raise ValueError(f'Unknown timezone: {timezone}')
        return tz

@Plugins.register('xmpp', Plugins.kind.ACTOR)
class SendJabber(Actor):
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
        self.jbr.to_be_send(entity.jid, str(record.as_timezone(entity.timezone)))

    async def run(self):
        await self.jbr.run()


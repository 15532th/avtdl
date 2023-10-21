import datetime
import logging
from typing import Sequence, Optional

import dateutil
from pydantic import field_validator

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
    timezone: Optional[str] = None # https://en.wikipedia.org/wiki/List_of_tz_database_time_zones

    @field_validator('timezone')
    @classmethod
    def check_timezone(cls, timezone: str) -> datetime.timezone:
        tz = dateutil.tz.gettz(timezone)
        if tz is None:
            raise ValueError(f'Unknown timezone: {timezone}')
        return tz

@Plugins.register('xmpp', Plugins.kind.ACTOR)
class SendJabber(Actor):
    def __init__(self, conf: JabberConfig, entities: Sequence[JabberEntity]):
        super().__init__(conf, entities)
        self.jbr = MSG2JBR(conf.xmpp_username, conf.xmpp_pass, self.logger)

    def handle(self, entity: JabberEntity, record: Record):
        self.jbr.to_be_send(entity.jid, str(record.as_timezone(entity.timezone)))

    async def run(self):
        await self.jbr.run()


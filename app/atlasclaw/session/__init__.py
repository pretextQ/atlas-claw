# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""

Session management module

Includes:
- context:Session context definitions(SessionKey, SessionScope, SessionMetadata etc.)
- manager:Session manager(CRUD, storage,)
- queue:Session serialization queue(SessionQueue)
- storage:storage adapter
"""

from app.atlasclaw.session.context import (
    SessionScope,
    ChatType,
    SessionKey,
    SessionKeyFactory,
    IdentityLinks,
    SessionOrigin,
    SessionMetadata,
    TranscriptEntry,
)
from app.atlasclaw.session.queue import SessionQueue, QueueMode
from app.atlasclaw.session.router import SessionManagerRouter

__all__ = [
    "SessionScope",
    "ChatType", 
    "SessionKey",
    "SessionKeyFactory",
    "IdentityLinks",
    "SessionOrigin",
    "SessionMetadata",
    "TranscriptEntry",
    "SessionQueue",
    "QueueMode",
    "SessionManagerRouter",
]

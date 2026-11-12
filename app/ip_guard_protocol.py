"""uvicorn HTTP protocols that drop TCP connections from denied IPs.

Injected through ``uvicorn.Config(http=...)`` (serve.py) and the CLI's
``--http ip_guard_protocol:GuardProtocol`` (start-local.ps1 auto-reload, and the
documented bare ``uvicorn main:app`` command). This is the *stronger* of the two
enforcement layers: a denied request is interrupted before ASGI exists. Where it
is not installed (bare uvicorn without the flag, or a uvicorn upgrade that makes
a custom protocol unusable), ``ip_guard.BlockedIPMiddleware`` still interrupts the
request at the ASGI boundary -- see the startup warning in ``main.lifespan``
that reports which layer is active.

The check runs in ``connection_made``, the first callback after the TCP
handshake and before any HTTP bytes are parsed. A denied peer gets exactly one
``deny <ip>`` application log line and an ``abort()`` (TCP RST). Because no ASGI
scope is built and no ``RequestResponseCycle`` is created, there is:

  * no HTTP response of any kind (not even 444) -- the port looks closed/reset;
  * no ``uvicorn.access`` line;
  * no request-log row and no middleware work.

That is the whole point of doing this below ASGI. ``peer_ip()`` cannot be used
here because it takes a Starlette ``Request``; the peer is read from the
transport, which is the same value uvicorn puts in ``scope["client"]`` and the
same one ``peer_ip()`` would return (X-Forwarded-* is never trusted).
"""

from __future__ import annotations

import logging
from typing import Any

from ip_guard import is_blocked
from uvicorn.protocols.http.httptools_impl import HttpToolsProtocol

logger = logging.getLogger("llm_hub")

#: Import sentinel for :func:`ip_guard.guard_protocol_active`. uvicorn imports
#: this module while resolving ``http="ip_guard_protocol:GuardProtocol"`` (or
#: ``serve.py``'s direct ``http=GuardProtocol``) before it imports the ASGI app,
#: so ``main.lifespan`` can tell from ``sys.modules`` whether the TCP guard is
#: really installed. A bare ``uvicorn main:app`` never imports us -> no sentinel.
GUARD_PROTOCOL_LOADED = True


class GuardProtocol(HttpToolsProtocol):
    """httptools-backed protocol with a TCP-peer deny check.

    httptools is a hard dependency of uvicorn's default ``auto`` protocol on this
    project (see requirements.txt); subclassing ``HttpToolsProtocol`` keeps the
    exact parser, keep-alive, pipeline, WebSocket-upgrade and concurrency-limit
    behaviour uvicorn 0.52 ships, overriding only connection setup.
    """

    def connection_made(self, transport: Any) -> None:
        # Let uvicorn register the connection and resolve self.client first; the
        # superclass also wires flow control / parser needed by connection_lost.
        super().connection_made(transport)
        client = self.client
        ip = client[0] if client else None
        if is_blocked(ip):
            logger.warning("deny %s", ip)
            # RST now: no response bytes, no ASGI scope, no request-log row.
            # abort() (unlike close()) does not wait for a peer FIN, so a scanner
            # cannot keep the slot alive by dribbling bytes or holding the socket.
            transport.abort()

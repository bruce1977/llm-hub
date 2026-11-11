"""Hardened uvicorn entrypoint for LLM Hub.

Why this file exists instead of `python -m uvicorn main:app`:
  * uvicorn's ProxyHeadersMiddleware rewrites ``scope["client"]`` from
    ``X-Forwarded-For`` whenever the TCP peer is in ``--forwarded-allow-ips``
    (127.0.0.1 by default, i.e. a same-host nginx). That would let a header forge
    the address used for rate-limit buckets and audit rows. We disable it here;
    no forwarded header is trusted at all - ``auth.peer_ip()`` always uses the
    real TCP peer.
  * It can bind the internal listener and the optional public listener in ONE
    process. A local listening socket is a kernel-assigned, unspoofable trust
    anchor: requests on ``security.public.port`` are the untrusted zone, with a
    mandatory key and a config-driven path allowlist.

Ports come from ``config.yaml`` ONLY - there are no LLMHUB_HOST / LLMHUB_PORT /
LLMHUB_PUBLIC_PORTS overrides. A socket that serves untrusted traffic must be
visible in the committed configuration.

Two-zone layout (recommended)::

    config.yaml : server.port: 8000 ; security.public.port: 8001
    docker run  : -p 192.168.10.5:8834:8000  -p 127.0.0.1:8835:8001
    nginx       : TLS termination -> 127.0.0.1:8835
                  (XFF is ignored: behind a proxy every request shares one TCP
                   peer, so IP-based rate limiting has a single bucket)

  8000 (internal zone): with ``security.internal.allow_anonymous: true`` normal
  APIs are key-free, /docs and /admin are reachable, and the management API
  still needs its console cookie. Publish this port on the LAN only.

  8001 (public zone): only the method+path pairs in
  ``security.public.path_allowlist`` exist here (everything else is 404 before
  routing), and every surviving request needs a key or console session. Point
  probes at 8000, never at 8001.

Public-only layout: set ``security.public.port: 8000`` (the same value as
``server.port``). One socket, untrusted zone, no anonymous HTTP path - use a TCP
port check for the container HEALTHCHECK.

Internal-only layout: leave ``security.public.port: null``. The single listener
is the trusted LAN zone. Mapping two Docker host ports to one container port
does NOT create two zones - the container sees one socket.
"""

from __future__ import annotations

import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn
from logging_config import uvicorn_log_config


def _bind(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
        sock.listen(2048)
        # uvicorn.Server.run(sockets=[...]) adopts these FDs in its own event loop.
        sock.set_inheritable(True)
        return sock
    except OSError:
        sock.close()
        raise


def run(application, *, host: str, port: int, public_port: int | None, log_level: str = "info") -> None:
    """Serve `application` on every distinct listener, in a single process.

    One process matters: ApiKeyStore keeps an in-memory hash set, so a second
    worker would keep accepting a revoked key until it restarted.
    """
    ports = sorted({int(port)} | ({int(public_port)} if public_port is not None else set()))
    # main.py's lifespan reads these for its startup log, so the log shows the
    # sockets actually bound rather than a second copy of the config.
    if hasattr(application, "state"):
        application.state.listen_ports = ports
        application.state.listen_endpoints = f"{host}:{','.join(str(p) for p in ports)}"
        application.state.public_ports = frozenset({int(public_port)}) if public_port is not None else frozenset()
    sockets: list[socket.socket] = []
    try:
        for listener in ports:
            sockets.append(_bind(host, listener))
        print(
            f"{time.strftime('%Y-%m-%d %H:%M:%S')} | LLM Hub listening on {host}:{', '.join(str(p) for p in ports)}; "
            f"public port: {public_port if public_port is not None else 'none'}; "
            "uvicorn proxy_headers=False",
            flush=True,
        )
        config = uvicorn.Config(
            application,
            host=host,
            port=int(port),
            log_level=log_level,
            # logging.json: timestamped access/error lines. Without it uvicorn's own
            # formatters override basicConfig and access lines carry no time at all.
            log_config=uvicorn_log_config(log_level),
            proxy_headers=False,
            forwarded_allow_ips=None,
        )
        uvicorn.Server(config).run(sockets=sockets)
    finally:
        for sock in sockets:
            sock.close()


def main() -> None:
    from config import ConfigManager
    from main import app

    cfg = ConfigManager().config
    run(
        app,
        host=cfg.server.host,
        port=cfg.server.port,
        public_port=cfg.security.public.port,
        log_level=cfg.server.log_level,
    )


if __name__ == "__main__":
    main()

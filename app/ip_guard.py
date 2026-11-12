"""IP deny-list for ``security.blocked_ips``: matching + two-layer enforcement.

The requirement is simply that a denied *request is interrupted before it is
served* -- one ``deny <ip>`` log line, no route, no middleware, no request-log
row. Enforcing it at the TCP layer is the strongest way to do that, but not the
only one, so there are two layers:

1. :mod:`ip_guard_protocol` (preferred). A custom uvicorn protocol aborts the
   socket in ``connection_made``, before an ASGI scope exists. Zero HTTP bytes,
   zero access-log lines, zero request-log rows. Active when the gateway starts
   through ``serve.py`` or with ``--http ip_guard_protocol:GuardProtocol``.
2. :class:`BlockedIPMiddleware` (safety net, always installed). A pure-ASGI
   outer middleware that interrupts a denied request without calling the
   downstream app or ``send``. It covers the cases layer 1 cannot see: a bare
   ``uvicorn main:app`` without ``--http``, and a keep-alive connection opened
   *before* an IP was added to the hot-reloaded deny-list. On uvicorn it also
   marks the request cycle disconnected and aborts the real socket, so
   ``run_asgi`` does not emit its "returned without starting response" error
   or a 500 fallback.

The two layers are mutually exclusive per request: when layer 1 drops the
connection there is no ASGI request, so exactly one ``deny <ip>`` line is
produced either way.

Matching uses ``ipaddress`` on the raw TCP peer (``scope["client"][0]`` /
``transport.get_extra_info("peername")``). Forwarded headers are never consulted;
this is the same "real TCP peer only" rule as rate limiting and audit. A single
address (``203.0.113.7``) and a CIDR (``10.0.0.0/8``, ``2001:db8::/32``) are both
accepted, and both address families are handled.

The list is published from config by :func:`configure`, which ``ConfigManager``
calls on every successful load. ``blocked_ips`` therefore hot-reloads: edit
config.yaml and the next connection/request is matched against the new set,
with no restart. Invalid entries are skipped with a warning rather than aborting
config load (a bad IP must not take the gateway offline); an empty/None set is a
no-op.
"""

from __future__ import annotations

import ipaddress
import logging
import sys
import threading
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger("llm_hub")

# Immutable snapshot, swapped atomically on reload. Readers (the event loop's
# protocol callbacks) grab one reference and match against it, so a reload never
# tears a connection mid-check. IPv4/IPv6 are kept apart so a v4 client is not
# compared against v6 networks (``IPv4Address in IPv6Network`` is simply False).
Network = ipaddress.IPv4Network | ipaddress.IPv6Network
Address = ipaddress.IPv4Address | ipaddress.IPv6Address

_lock = threading.Lock()
_networks: tuple[Network, ...] = ()
_rules: tuple[str, ...] = ()


def _parse(entries: Iterable[str]) -> tuple[tuple[Network, ...], tuple[str, ...]]:
    """Compile config strings into networks; drop invalid ones with a warning.

    ``strict=False`` accepts ``203.0.113.7/24`` as shorthand for the containing
    network, so a caller who writes a host address with a prefix gets the obvious
    result instead of a hard failure. A bare address becomes a /32 or /128.
    """
    networks: list[Network] = []
    rules: list[str] = []
    for raw in entries:
        if not isinstance(raw, str):
            logger.warning("security.blocked_ips: ignoring non-string entry %r", raw)
            continue
        text = raw.strip()
        if not text:
            continue
        try:
            network = ipaddress.ip_network(text, strict=False)
        except ValueError:
            logger.warning("security.blocked_ips: ignoring invalid IP/CIDR %r", text)
            continue
        # Dedupe after normalisation: "10.0.0.1" and "10.0.0.0/32" are one rule.
        if any(network == seen for seen in networks):
            continue
        networks.append(network)
        rules.append(str(network))
    return tuple(networks), tuple(rules)


def configure(entries: Iterable[str] | None) -> int:
    """Publish a new deny-list snapshot; return the number of active rules.

    Invalid entries are skipped (with a warning) rather than raising, so a typo
    in config.yaml cannot prevent the gateway from starting. Called by
    ``ConfigManager`` on each successful load, including hot reloads.
    """
    global _networks, _rules
    networks, rules = _parse(entries or ())
    with _lock:
        _networks, _rules = networks, rules
    return len(networks)


def is_blocked(ip: str | None) -> bool:
    """Whether the raw TCP peer address is denied. Invalid/missing IP -> False."""
    if not ip:
        return False
    try:
        address: Address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    # Snapshot the tuple once: a concurrent reload swaps the reference, never
    # mutates it, so this loop is safe without holding the lock.
    for network in _networks:
        if address.version == network.version and address in network:
            return True
    return False


def active_rules() -> tuple[str, ...]:
    """Normalised CIDRs currently in force (for startup logs and tests)."""
    return _rules


def guard_protocol_active() -> bool:
    """Whether uvicorn is actually running the socket-level deny check.

    ``serve.py`` imports ``GuardProtocol`` directly, and the CLI entrypoints pass
    ``--http ip_guard_protocol:GuardProtocol``. In both cases uvicorn resolves
    the protocol class in ``Config.load()`` *before* importing the ASGI app, so
    by the time ``main.lifespan`` runs the module is in ``sys.modules`` with its
    import sentinel set. A bare ``uvicorn main:app`` (no ``--http``) leaves it
    absent: config still parses and ``active_rules()`` still reports rules, but
    nothing enforces them. That is exactly the silent-failure case the startup
    warning in ``main.lifespan`` needs to catch.
    """
    module = sys.modules.get("ip_guard_protocol")
    return bool(module is not None and getattr(module, "GUARD_PROTOCOL_LOADED", False))


async def _interrupt(receive: Any, send: Any) -> None:
    """End a denied request without producing a business response.

    uvicorn passes bound methods of its ``RequestResponseCycle`` as ``receive``
    / ``send``. Marking the cycle ``disconnected`` makes ``send()`` a no-op and
    suppresses ``run_asgi``'s "returned without starting response" error and
    its 500 fallback; aborting the transport resets the real socket. Because
    ``send`` is never successfully called with ``http.response.start``, uvicorn
    also emits no ``uvicorn.access`` line.

    On an ASGI server that exposes no such cycle (e.g. Starlette's TestClient),
    there is no transport to reset, so we still terminate the request with a
    bodyless 444 (nginx's "drop the connection" convention). No route runs and
    no request-log row is written in either case. This branch never applies to
    a normal uvicorn deployment.
    """
    cycle = getattr(receive, "__self__", None) or getattr(send, "__self__", None)
    transport = getattr(cycle, "transport", None)
    if hasattr(cycle, "disconnected"):
        # uvicorn: one log line, a reset socket, zero response bytes.
        cycle.disconnected = True
    if transport is not None:
        try:
            transport.abort()
        except Exception:
            try:
                transport.close()
            except Exception:
                pass
        return
    # No transport available: end the request so a client never hangs. 444 has
    # no body and never reaches any gateway route / middleware.
    await send({"type": "http.response.start", "status": 444,
                "headers": [(b"content-length", b"0")]})
    await send({"type": "http.response.body", "body": b""})


class BlockedIPMiddleware:
    """Pure-ASGI outer middleware: interrupt denied requests before any route.

    Registered with ``app.add_middleware`` so it wraps the whole Starlette
    middleware stack. It deliberately does *not* use BaseHTTPMiddleware: that
    class always produces a ``Response``, which is exactly what a deny must not
    do. See the module docstring for how this complements the protocol layer.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        ip = client[0] if client else None
        if not is_blocked(ip):
            await self.app(scope, receive, send)
            return
        logger.warning("deny %s", ip)
        await _interrupt(receive, send)


def reset() -> None:
    """Clear the list (used by tests to isolate cases)."""
    configure([])

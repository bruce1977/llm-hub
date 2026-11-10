"""Path policy for the internet-facing (``security.public.port``) listener.

The public listener is not a second proxy process: it is the *same* in-process app
bound to a second socket. One process is deliberate - the API-key cache, rate-limit
buckets, login lockout and config cache are shared, so revoking a key or banning an
IP takes effect on both zones immediately, and a streaming response is never
re-framed by a second HTTP hop.

What differs on a public request is the policy envelope below, applied before
routing. It has two independent decisions:

  * **does the path exist here** - :func:`bridge_denial`. The management plane
    (``/v1/management/*`` and the ``/admin`` console) is closed unless
    ``security.public.admin_endpoints`` is ``allow``; malformed paths (``..``
    segments, NUL) are never a public route; **every other path must be listed in
    `security.public.path_allowlist`**, keyed by HTTP method, as a full-match
    regular expression. Anything not listed returns 404 *before* routing, with or
    without a valid key;
  * **may this caller use it** - :func:`public_auth_denial`. A listed path still
    needs a caller key or a signed console session, and that is checked here
    rather than left to each route's ``Depends(require_api_key)``.

The management plane is the one deliberate exception to "the config file is the
complete surface": when an operator sets ``admin_endpoints: allow`` they are asking
for a remotely reachable console, and enumerating ``/admin`` assets in a regex list
would be both fragile and pointless. The prefix therefore opens as a unit - but
never anonymously: the console shell and the login handshake are the only two
paths that carry no credential (they are where a cookie is obtained), and every
other management route still demands that cookie through its own dependency.

There is deliberately no code-level list of "hidden" routes, no
``require_api_key`` toggle and no ``LLMHUB_PUBLIC_ADMIN`` switch. Adding a business
endpoint is an explicit operator decision in the config file, and listing it never
makes the endpoint anonymous. The trust anchor is the kernel-assigned local
listening socket (``scope["server"]``) - no request header can pretend to be the
internal port.

Point container/balancer probes at the INTERNAL port (``server.port``). A
public-only instance (``security.public.port == server.port``) has no anonymous
``/health`` at all: use a TCP port check instead.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from auth import authorize, has_admin_session

_MANAGEMENT_PREFIX = "/v1/management"
_ADMIN_PREFIX = "/admin"

#: The only management paths that are anonymous when the plane is opened: the static
#: console shell (which holds no data) and the login handshake (which is where a session
#: cookie is obtained). Everything else under these prefixes still needs that cookie.
_LOGIN_PATH = "/v1/management/auth/login"


def _in_admin_plane(path: str) -> bool:
    return (
        path == _MANAGEMENT_PREFIX
        or path.startswith(_MANAGEMENT_PREFIX + "/")
        or path == _ADMIN_PREFIX
        or path.startswith(_ADMIN_PREFIX + "/")
    )


def admin_plane_open(cfg: Any) -> bool:
    """True when ``security.public.admin_endpoints: allow`` exposes the console remotely."""
    return cfg.security.public.admin_endpoints == "allow"


def is_anonymous_admin_path(path: str) -> bool:
    """Paths that need no credential once the management plane is open.

    The console shell is a static SPA with no data in it, and ``POST
    /v1/management/auth/login`` is where a cookie is *obtained* - requiring a
    credential to log in would make the remote console unreachable. Nothing else
    qualifies: ``/v1/management/*`` still answers 401 without the cookie, and a
    gateway API key is never an admin credential.
    """
    return path == _ADMIN_PREFIX or path.startswith(_ADMIN_PREFIX + "/") or path.rstrip("/") == _LOGIN_PATH


def bridge_denial(method: str, path: str, cfg: Any) -> tuple[int, str] | None:
    """Return ``(status, detail)`` when a public request must stop before routing.

    Only call this for requests that actually landed on the public listener.
    ``path`` is ``request.url.path`` (URL-decoded, without the query string).

    The management plane is decided *first* because it is a prefix rule, not an
    allowlist entry: with ``admin_endpoints: allow`` it opens as a unit (its own
    authentication still applies), and in every other mode it is a hard 404 even if
    an operator accidentally lists it in ``path_allowlist``.
    """
    # 1. Never let a malformed path become a public route, even under a broad regex
    #    such as ^/v1/.*$ . proxy.forward() rejects these with 400 on the internal
    #    zone; on the public zone an unknown path must simply not exist.
    if "\x00" in path or ".." in path.split("/"):
        return (404, "Not found.")

    # 2. The management plane: one prefix, one decision.
    if _in_admin_plane(path):
        if not admin_plane_open(cfg):
            return (404, "Not found.")
        return None

    # 3. The config file is the COMPLETE public surface. Method matters: a path
    #    allowed for POST can still be 404 for GET, and vice versa.
    if not cfg.security.public.allows(method, path):
        return (404, "Not found.")
    return None


async def public_auth_denial(
    request: Any, cfg: Any
) -> tuple[int, str, dict[str, str]] | None:
    """Return ``(status, detail, headers)`` when a public request has no credential.

    The other half of the envelope, and deliberately separate from
    :func:`bridge_denial`: listing a path decides whether it *exists* on this
    socket, never whether it is anonymous. So the order on the public listener is
    404 for an unlisted path (even with a valid key), then 401/403 for a listed
    path that arrives without one.

    The check runs in the middleware, i.e. as a property of the zone, and not only
    inside each route's ``Depends(require_api_key)``: a handler that ever forgets
    the dependency would otherwise be silently reachable unauthenticated from the
    internet. ``auth.authorize()`` holds the rules (IP fence, missing key, invalid
    key), so this only translates its exception into the middleware's denial
    envelope - the response a route-level dependency would have produced is
    unchanged. A signed console session is accepted here exactly as
    ``require_api_key`` accepts it, on either zone.

    When ``admin_endpoints: allow`` puts the console on this listener, the shell
    and the login handshake are exempt (see :func:`is_anonymous_admin_path`);
    every other management route keeps requiring the session cookie, which its
    route-level dependency enforces.
    """
    if has_admin_session(request):
        return None
    if admin_plane_open(cfg) and is_anonymous_admin_path(request.url.path):
        return None
    try:
        await authorize(request, cfg)
    except HTTPException as exc:
        return (int(exc.status_code), str(exc.detail), dict(exc.headers or {}))
    return None

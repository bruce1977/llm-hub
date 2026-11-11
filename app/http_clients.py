"""Outbound HTTP clients: a queued inference pool and an unconstrained control pool.

Local model backends (Ollama, LM Studio, OpenCode chat) are the scarce resource.
High request volume is *normal* traffic here, so the gateway does not reject a
burst with 429. Instead ``httpx`` / ``httpcore`` provides the back-pressure: its
connection pool keeps at most ``server.max_concurrency`` requests in flight and
queues the rest until a slot is free. Streaming responses keep their slot until
the last chunk, which bounds the number of simultaneous generation jobs and so
protects VRAM.

Control-plane traffic (System One backend, web search provider, model discovery,
health probes, management API) deliberately uses a separate, larger pool: a busy
model must not make the console report every upstream as offline.

The two pools are built once in the lifespan; changing ``max_concurrency`` or
``upstream_queue_timeout`` in config.yaml requires a restart.
"""

from __future__ import annotations

import httpx

# Discovery probes, System One and web search are short, cheap calls. This pool
# is never the inference bottleneck and is not part of the concurrency gate.
CONTROL_POOL_SIZE = 64


def inference_timeout(seconds: float, cfg) -> httpx.Timeout:
    """Per-upstream timeout that also caps how long a request waits for a slot.

    ``client.request(timeout=<float>)`` would replace the whole client timeout
    dict and silently drop the ``pool`` value, so every inference call must pass
    a ``Timeout`` object instead of a bare number.
    """
    return httpx.Timeout(
        seconds,
        connect=10.0,
        pool=float(cfg.server.upstream_queue_timeout),
    )


def build_clients(cfg) -> tuple[httpx.AsyncClient, httpx.AsyncClient]:
    """Return ``(control_client, inference_client)``.

    ``trust_env=False`` keeps ambient HTTP(S)_PROXY from breaking pooled
    keep-alive connections to LAN hosts, and ``follow_redirects=False`` stops an
    upstream 307 from replaying a destructive body on a path the deny list never
    saw. Both clients need the same guarantees.
    """
    limit = max(1, int(cfg.server.max_concurrency))
    keepalive = cfg.server.upstream_keepalive_expiry
    common = {"follow_redirects": False, "trust_env": False}

    control = httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=max(CONTROL_POOL_SIZE, limit),
            max_keepalive_connections=32,
            keepalive_expiry=keepalive,
        ),
        timeout=httpx.Timeout(cfg.server.request_timeout, connect=10.0),
        **common,
    )
    inference = httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=limit,
            max_keepalive_connections=limit,
            keepalive_expiry=keepalive,
        ),
        timeout=inference_timeout(cfg.server.request_timeout, cfg),
        **common,
    )
    return control, inference

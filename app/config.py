"""
Configuration loading module.

Reads the gateway configuration from /data/config.yaml and provides model routing and alias
resolution. Supports hot reload (checks the file mtime at most once per second).

The file is YAML; because YAML 1.2 is a superset of JSON, a `CONFIG_PATH` that still points at
a `.json` file parses exactly the same way.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path
from typing import Any, ClassVar, Literal

import yaml
from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator

logger = logging.getLogger("llm_hub")

CONFIG_PATH = os.getenv("CONFIG_PATH", "/data/config.yaml")

#: A deployment that still only has the pre-YAML `config.json` next to the new default path
#: keeps working: it is loaded (with a warning) instead of being replaced by a fresh template.
LEGACY_CONFIG_NAME = "config.json"

# --------------------------------------------------------------------------- #
# Guards for config-controlled outbound destinations and credential names.
#
# A caller who can write config.yaml could otherwise point `api_key_env` at
# ADMIN_PASSWORD and have the gateway mail that value to a host of their choosing
# as an Authorization header. Note that RFC1918 / loopback
# addresses are **allowed**: routing to local Ollama / LM Studio instances is the
# whole point of this gateway. What is refused are non-HTTP schemes and the cloud
# metadata endpoints that are never a legitimate inference backend.
# --------------------------------------------------------------------------- #
_KEY_ENV_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*_(?:API_KEY|API_TOKEN)$")
_METADATA_HOSTS = {
    "metadata",
    "metadata.google.internal",
    "169.254.169.254",
    "100.100.100.200",
}


def validate_key_env(name: str) -> str:
    """Only provider-key variable names may be read from the process environment."""
    if not name:
        return name  # "" = unsigned upstream (websearch default)
    if not _KEY_ENV_PATTERN.fullmatch(name):
        raise ValueError(
            f"api_key_env {name!r} must be a provider key name (uppercase, ending in "
            "_API_KEY / _API_TOKEN); pointing it at ADMIN_PASSWORD would leak the console "
            "password to the upstream"
        )
    return name


def validate_service_url(url: str) -> str:
    """Reject schemes and metadata targets that must never be an outbound backend."""
    from urllib.parse import urlsplit

    parsed = urlsplit(str(url).strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"URL {url!r} must use http or https")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError(f"URL {url!r} has no host")
    if host in _METADATA_HOSTS or "169.254." in host:
        raise ValueError(f"URL {url!r} points at a cloud metadata endpoint")
    if parsed.username or parsed.password:
        raise ValueError(f"URL {url!r} must not embed credentials")
    return str(url).strip()


#: Listener selection and zone policy live ONLY in config.yaml now (server.port and
#: security.public.port). These names are kept so a stale deployment can be warned
#: about instead of silently ignoring a port an operator thought they had opened.
LEGACY_LISTENER_ENV = (
    "LLMHUB_PORT",
    "LLMHUB_PUBLIC_PORTS",
    "LLMHUB_PUBLIC_PORT",
    "LLMHUB_PUBLIC_ADMIN",
    "LLMHUB_HOST",
    "LLMHUB_LOG_LEVEL",
    "GATEWAY_AUTH_DISABLED",
)

#: HTTP verbs accepted as keys of `security.public.path_allowlist`. A method that is
#: absent from the mapping is simply not served on the public listener (404).
HTTP_METHOD_NAMES = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})

#: Ready-made public surface. The *config file* is authoritative - this only seeds a
#: fresh config.yaml so opening a public listener is useful out of the box. Narrow it
#: in `security.public.path_allowlist` to the endpoints you actually expose.
DEFAULT_PUBLIC_PATHS: dict[str, list[str]] = {
    "GET": [r"^/v1/models$"],
    "POST": [
        r"^/v1/chat/completions$",
        r"^/v1/messages$",
        r"^/api/generate$",
        r"^/api/chat$",
    ],
}

def apply_legacy_listener_env(raw: dict[str, Any]) -> dict[str, Any]:
    """Map the pre-1.3 listener env vars onto the new config tree, with warnings.

    ``config.yaml`` is now the single source of truth for ports and zones. An upgrade
    must not silently close a port an operator believes is public, though, so the
    legacy variables are read *once per load* only to fill gaps the file left open.
    Explicit values in config.yaml always win. Everything else in
    :data:`LEGACY_LISTENER_ENV` is ignored with a warning - notably
    ``LLMHUB_PUBLIC_ADMIN`` (the console is internal-only now) and ``LLMHUB_PORT``
    (the internal port comes from ``server.port``).
    """
    present = [name for name in LEGACY_LISTENER_ENV if os.getenv(name, "").strip()]
    if not present:
        return raw
    logger.warning(
        "Legacy listener environment variable(s) %s: ports and zone policy now live in "
        "config.yaml (server.port / security.public.port). Remove them from the environment; "
        "they are honoured only as a one-time migration hint and explicit config wins.",
        ", ".join(present),
    )

    raw = dict(raw)
    sec = dict(raw.get("security") or {})
    public = dict(sec.get("public") or {})
    internal = dict(sec.get("internal") or {})

    legacy_ports = (
        os.getenv("LLMHUB_PUBLIC_PORTS", "").strip()
        or os.getenv("LLMHUB_PUBLIC_PORT", "").strip()
    )
    if legacy_ports and public.get("port") is None:
        tokens = [t.strip() for t in legacy_ports.replace(";", ",").split(",") if t.strip()]
        valid = [int(t) for t in tokens if t.isdigit() and 1 <= int(t) <= 65535]
        if valid:
            public["port"] = valid[0]
            logger.warning(
                "Migrating legacy public listener port %d into security.public.port; write it "
                "into config.yaml and delete the environment variable",
                valid[0],
            )
            if len(valid) > 1:
                logger.warning(
                    "Legacy %s listed %d ports; only the first (%d) is supported now. "
                    "A two-listener layout needs one internal and one public port.",
                    "LLMHUB_PUBLIC_PORTS",
                    len(valid),
                    valid[0],
                )
        else:
            logger.warning("Ignoring unparseable legacy public port value %r", legacy_ports)

    disabled = os.getenv("GATEWAY_AUTH_DISABLED", "").strip().lower() in ("true", "1", "yes")
    if disabled and "allow_anonymous" not in internal:
        internal["allow_anonymous"] = True
        logger.warning(
            "Migrating GATEWAY_AUTH_DISABLED=true into security.internal.allow_anonymous=true; "
            "the public zone still requires a key and cannot be disabled"
        )

    if public:
        sec["public"] = public
    if internal:
        sec["internal"] = internal
    if sec:
        raw["security"] = sec
    return raw


# Qwen3-Reranker official prompt template (yes/no binary scoring)
DEFAULT_RERANK_TEMPLATE = (
    "<|im_start|>system\n"
    "Judge whether the Document meets the requirements based on the Query and the "
    'Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n'
    "<|im_start|>user\n"
    "<Instruct>: {instruction}\n"
    "<Query>: {query}\n"
    "<Document>: {document}<|im_end|>\n"
    "<|im_start|>assistant\n"
    "<think>\n\n</think>\n\n"
)

# The instruction is the main lever against "topic matching": without an explicit
# constraint, a generative reranker happily answers "yes" for anything that shares the
# query's broad theme (e.g. "how much water per day" -> "bananas contain potassium").
DEFAULT_RERANK_INSTRUCTION = (
    "Given a web search query, retrieve relevant passages that answer the query. "
    "A passage is relevant only if it directly answers the specific question asked. "
    "Passages that merely share the same broad topic, mention related keywords, or "
    "talk about the same field or entity without answering the question are NOT relevant."
)


# --------------------------------------------------------------------------- #
# Configuration models
# --------------------------------------------------------------------------- #
class ServerConfig(BaseModel):
    """Base listener settings. This is the only place a port is configured.

    ``port`` is the internal (trusted) listener. The internet-facing listener, if
    any, is ``security.public.port``. Matching the two values makes a public-only
    instance. No `LLMHUB_PORT` / `LLMHUB_PUBLIC_PORTS` environment override exists
    any more: a socket that serves untrusted traffic must be visible in config.yaml.
    """

    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"
    request_timeout: float = 600.0
    max_concurrency: int = 64
    upstream_keepalive_expiry: float = 30.0


class InternalSecurityConfig(BaseModel):
    """Policy for the trusted ``server.port`` listener.

    The kernel-assigned local port is the trust anchor: a request that arrived on
    ``server.port`` and NOT on ``security.public.port`` is on the LAN zone. There is
    no IP / X-Forwarded-For allowlist to configure here - a header can be forged,
    and one socket cannot be both trusted and untrusted.
    """

    allow_anonymous: bool = True
    # Anonymous `GET /` still answers {"name": ...}; opt in to also carrying the build.
    expose_version: bool = True
    # Interactive docs and their vendored Swagger assets are an internal-only
    # convenience. The public zone can never reach them (404 via path_allowlist).
    expose_docs: bool = True


class PublicSecurityConfig(BaseModel):
    """Policy for the internet-facing listener.

    ``port`` is the *only* switch that opens a public listener. Setting it equal to
    ``server.port`` makes this a public-only instance (one socket, untrusted zone);
    setting it to a different port gives a two-listener internal + public layout;
    leaving it null means no public listener at all.

    Every request on this listener needs a valid caller key or console session, and
    only method+path pairs listed in ``path_allowlist`` exist here - everything else
    returns 404 *before* routing. The *config file* is the complete source of the
    public surface; no code-level hidden-path list complements it. There is
    deliberately no `require_api_key` toggle: key enforcement is not configurable
    away on the public zone.

    ``admin_endpoints`` is public-only and governs the whole management plane there:
    Ollama's own model API (`api/pull`, `api/push`, `api/create`, `api/delete`,
    `api/copy`, `api/blobs/*`) **and** the console (`/admin`) plus `/v1/management/*`.
    The internal listener is a trusted LAN socket where pulling a model or opening
    the console is a normal operation, so nothing is blocked there:

      "deny"     (default) - admin calls, the console and `/v1/management/*` are 404
      "readonly" - `pull` (model download) passes the admin filter, the rest is 404
      "allow"    - every admin call is forwarded untouched, `api/blobs/*` included,
                   and the management plane opens: `/admin` and `/v1/management/*`
                   stop being 404 and are routed to their real handlers

    For Ollama's endpoints no mode opens a path on its own: the request has to
    survive ``path_allowlist`` first, so `pull` needs ``readonly`` **or** ``allow``
    *and* a ``POST: [^/api/pull$]`` entry; changing the mode while the path stays
    unlisted changes nothing (the 404 comes from the allowlist).

    The management plane is the opposite kind of rule - a prefix, not an entry - so
    ``allow`` opens it as a unit and ``path_allowlist`` is not consulted. That is
    deliberate: an operator asking for a remote console would otherwise have to
    enumerate every `/admin/assets/*` file in a regex. It is still never anonymous.
    Only the console shell (a static SPA holding no data) and
    ``POST /v1/management/auth/login`` (where the session cookie is obtained) skip
    the credential check; every other management route keeps requiring that cookie,
    and a gateway API key is never an admin credential.
    """

    port: int | None = None
    path_allowlist: dict[str, list[str]] = Field(
        default_factory=lambda: {verb: list(patterns) for verb, patterns in DEFAULT_PUBLIC_PATHS.items()}
    )
    admin_endpoints: Literal["deny", "readonly", "allow"] = "deny"

    @field_validator("port")
    @classmethod
    def _check_port(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool) or not (1 <= value <= 65535):
            raise ValueError("security.public.port must be a TCP port in 1..65535 or null")
        return value

    @field_validator("path_allowlist", mode="before")
    @classmethod
    def _normalise_allowlist(cls, value: Any) -> Any:
        """Upper-case the verb keys and keep only syntactically valid regexes.

        An invalid regex is dropped with a warning rather than aborting config load:
        with a whitelist, dropping a rule can only *narrow* the public surface.
        """
        if value is None:
            return {}
        if not isinstance(value, dict):
            return value  # let pydantic produce the type error
        out: dict[str, list[str]] = {}
        for method, patterns in value.items():
            verb = str(method).strip().upper()
            if verb not in HTTP_METHOD_NAMES:
                raise ValueError(
                    f"security.public.path_allowlist: {method!r} is not an HTTP method "
                    f"({', '.join(sorted(HTTP_METHOD_NAMES))})"
                )
            if isinstance(patterns, str):
                patterns = [patterns]
            if not isinstance(patterns, (list, tuple)):
                raise ValueError(f"security.public.path_allowlist[{verb}] must be a list of regex strings")
            kept: list[str] = []
            for pat in patterns:
                if not isinstance(pat, str):
                    raise ValueError(f"security.public.path_allowlist[{verb}] contains a non-string pattern")
                try:
                    re.compile(pat)
                except re.error as exc:
                    logger.warning("Ignoring invalid public path_allowlist regex %r for %s: %s", pat, verb, exc)
                    continue
                kept.append(pat)
            out[verb] = kept
        return out

    _compiled_cache: ClassVar[dict[tuple[str, tuple[str, ...]], tuple[re.Pattern, ...]]] = {}

    def allows(self, method: str, path: str) -> bool:
        """True when ``path`` fullmatches one of the regexes for ``method``.

        Full match only - a prefix rule like `^/v1/` would otherwise admit
        `/v1/../../api/delete` (the catch-all normalises dot segments upstream).
        """
        verb = method.upper()
        patterns = tuple(self.path_allowlist.get(verb, ()))
        if not patterns:
            return False
        key = (verb, patterns)
        compiled = self._compiled_cache.get(key)
        if compiled is None:
            compiled = tuple(re.compile(p) for p in patterns)
            self._compiled_cache[key] = compiled
        return any(rx.fullmatch(path) for rx in compiled)

    def visible_paths(self) -> list[str]:
        """Stable, log-friendly rendering of the configured public surface."""
        return [f"{verb} {pat}" for verb in sorted(self.path_allowlist) for pat in self.path_allowlist[verb]]

    # Ollama management endpoints that are unsafe to forward on an untrusted listener.
    ADMIN_DESTRUCTIVE: ClassVar[set] = {"api/delete", "api/copy", "api/create", "api/push"}
    ADMIN_DISK_FILL: ClassVar[set] = {"api/pull"}  # downloads models -> can fill the disk
    ADMIN_BLOBS: ClassVar[set] = {"api/blobs"}  # blob upload / delete

    def blocked_admin_paths(self) -> set:
        if self.admin_endpoints == "allow":
            return set()
        blocked = set(self.ADMIN_DESTRUCTIVE) | set(self.ADMIN_BLOBS)
        if self.admin_endpoints == "deny":
            blocked |= set(self.ADMIN_DISK_FILL)
        return blocked

    def reachable_admin_paths(self) -> list[str]:
        """Admin endpoints that survive BOTH the mode and the allowlist.

        ``admin_endpoints`` never opens a path by itself - ``path_allowlist`` is the
        surface, so ``allow`` with no matching ``POST`` rule leaves every admin endpoint
        at 404 and looks like the setting "does nothing". This is what the startup log
        reports, so that confusion is answered by the log instead of by a bare 404.
        """
        blocked = self.blocked_admin_paths()
        names = ["api/pull", "api/push", "api/create", "api/delete", "api/copy"]
        reachable = [n for n in names if n not in blocked and self.allows("POST", "/" + n)]
        if "api/blobs" not in blocked and self.allows("POST", "/api/blobs/<digest>"):
            reachable.append("api/blobs/*")
        return reachable


class RoutingConfig(BaseModel):
    """Routing policy. When strict=True, models not declared on any upstream return 404."""

    strict: bool = False


class UpstreamConfig(BaseModel):
    """A single backend instance: Ollama (default), LM Studio, or an OpenCode API Gateway.

    `models` supports exact names or prefix wildcards (e.g. qwen3:*).
    `model_aliases` maps a short exposed name to the real upstream id, which is handy
    for verbose LM Studio ids such as "qwen/qwen3.5-9b".
    """

    name: str
    base_url: str
    # Backend flavor: "ollama" (native API), "lmstudio" (OpenAI-compatible API),
    # or "opencode" (OpenCode API Gateway: OpenAI-compatible chat + web search)
    type: Literal["ollama", "lmstudio", "opencode"] = "ollama"
    models: list[str] = Field(default_factory=list)
    # Short exposed name -> real upstream id, e.g. {"qwen3.5:9b": "qwen/qwen3.5-9b"}
    model_aliases: dict[str, str] = Field(default_factory=dict)
    timeout: float | None = None
    description: str = ""
    # Extra headers attached when forwarding (optional, e.g. when the upstream has its own auth)
    headers: dict[str, str] = Field(default_factory=dict)
    # Total VRAM of the host running this upstream, for the management console's
    # "used / total" gauge. Not discoverable over the API, so it is declared here.
    vram_total_gb: float | None = None
    # Scheduling priority used when the SAME model is declared by several upstreams.
    # The lowest value wins; ties keep the order in which upstreams are listed.
    priority: int = 0

    @field_validator("base_url")
    @classmethod
    def _check_base_url(cls, value: str) -> str:
        return validate_service_url(value)

    def base_url_stripped(self) -> str:
        return self.base_url.rstrip("/")

    # ------------------------------------------------------------ backend specifics ---
    def tags_path(self) -> str:
        """Model discovery endpoint (differs between Ollama and OpenAI-compatible backends)."""
        return "api/tags" if self.type == "ollama" else "v1/models"

    def health_path(self) -> str:
        """Liveness endpoint used by /health probes."""
        if self.type == "opencode":
            return "health"
        return "api/version" if self.type == "ollama" else "v1/models"

    def parse_model_names(self, payload: dict[str, Any]) -> list[str]:
        """Extract model ids from a discovery response according to the backend flavor."""
        if self.type == "ollama":
            return [m.get("name") for m in payload.get("models", []) if isinstance(m, dict) and m.get("name")]
        # OpenAI-compatible (LM Studio): {"object": "list", "data": [{"id": "..."}]}
        return [m.get("id") for m in payload.get("data", []) if isinstance(m, dict) and m.get("id")]


class AliasConfig(BaseModel):
    """Model alias: maps the exposed name to a real model and can force-inject extra parameters."""

    model: str
    upstream: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    description: str = ""


class RerankConfig(BaseModel):
    enabled: bool = True
    endpoint: str = "/v1/rerank"
    mode: str = "logprobs"  # generative yes/no scoring (the only supported mode)
    models: list[str] = Field(default_factory=list)
    instruction: str = DEFAULT_RERANK_INSTRUCTION
    template: str = DEFAULT_RERANK_TEMPLATE
    yes_token: str = "yes"
    no_token: str = "no"
    # Extra accepted spellings (matched case-insensitively, punctuation stripped),
    # e.g. yes_aliases ["是"] / no_aliases ["否"] for a Chinese-tuned reranker.
    yes_aliases: list[str] = Field(default_factory=list)
    no_aliases: list[str] = Field(default_factory=list)
    temperature: float = 0.0
    top_logprobs: int = 20
    # Small budget: the answer is a single token. >1 keeps a leading "\n"/"<think>"
    # token from swallowing the whole budget (see token_scan_depth).
    max_tokens: int = 4
    # How many generated token positions are searched for the yes/no candidate.
    token_scan_depth: int = 4
    # Stop sequences handed to the upstream (empty = rely on max_tokens only).
    stop: list[str] = Field(default_factory=list)
    # Extra upstream options merged into every scoring call.
    # KEEP num_ctx TIGHT: Ollama pre-allocates the whole KV cache up front, so
    # num_ctx dominates VRAM. qwen3-reranker:4b at its default 40960 context costs
    # ~9.3 GB; the same model at 8192 costs ~4.0 GB - same scores, half the memory.
    options: dict[str, Any] = Field(default_factory=dict)
    # Ollama only. Unload the reranker from VRAM after this many seconds idle.
    # Reranking is bursty (one scoring burst per query), so keeping a 4B model
    # resident steals VRAM from the chat models. 0 = unload right after the burst,
    # "5m" = keep it warm for 5 minutes, -1 (default) = leave the server default.
    keep_alive: Any = -1
    # When an endpoint style returns no (usable) logprobs, try the next one
    # (/api/generate -> /api/chat -> /v1/chat/completions) before degrading to text.
    logprobs_fallback: bool = True
    # Scores used when the upstream returns no usable logprobs. "unknown" is 0.0 on
    # purpose: an unparsable answer must not look like "half relevant".
    fallback_yes: float = 0.9
    fallback_no: float = 0.1
    fallback_unknown: float = 0.0
    # Off by default: logprobs already produce probabilities in [0, 1] and min-max
    # normalization would force the best hit to 1.0 / the worst to 0.0, which makes
    # scores incomparable across requests (and meaningless for a single document).
    normalize: bool = False
    max_concurrency: int = 8
    return_documents: bool = True
    # Per-model mode override, e.g. {"qwen3-reranker:4b": "logprobs"}
    model_modes: dict[str, str] = Field(default_factory=dict)
    # Which upstream call to use for generative scoring:
    #   "auto"     -> Ollama: /api/generate (raw prompt), OpenAI-compatible: /v1/chat/completions
    #   "generate" -> always Ollama /api/generate
    #   "chat"     -> always the chat endpoint (/api/chat or /v1/chat/completions)
    upstream_api: str = "auto"
    # Send the rendered template verbatim, without applying the model's chat template.
    # Required for the Qwen3-Reranker prompt, which already is a full chat prompt.
    raw_prompt: bool = True
    # Thinking models would waste the tiny token budget on <think>...</think>.
    disable_thinking: bool = True
    # Guard rails (0 = unlimited)
    max_documents: int = 0
    max_document_chars: int = 0
    # What to do when a single document cannot be scored:
    #   "score_zero" -> score it 0.0 and keep going
    #   "fail"       -> return 502
    # (if EVERY document fails, 502 is returned in both cases)
    on_error: str = "score_zero"

    #: Accepted spellings for the logprobs scoring mode. The former embedding/cosine
    #: mode was removed: re-ranking with the retrieval embedding model adds no signal.
    MODE_ALIASES: ClassVar[dict[str, str]] = {
        "logprob": "logprobs",
        "logprobs": "logprobs",
        "generation": "logprobs",
        "generate": "logprobs",
        "generative": "logprobs",
        "cross-encoder": "logprobs",
        "cross_encoder": "logprobs",
        "crossencoder": "logprobs",
        "yes_no": "logprobs",
        "yes-no": "logprobs",
        # Kept so an old config still fails with a helpful message instead of a
        # confusing "unsupported mode" error.
        "embedding": "embedding",
    }

    def mode_for(self, model: str, target: str | None = None) -> str:
        """Resolve the scoring mode for a model (per-model override wins)."""
        raw = self.model_modes.get(model)
        if not raw and target:
            raw = self.model_modes.get(target)
        if not raw:
            raw = self.mode
        normalized = str(raw).strip().lower()
        return self.MODE_ALIASES.get(normalized, normalized)


class SystemOneConfig(BaseModel):
    """Proxy configuration for the System One decision API (`POST /v1/systemone`).

    Two kinds of backend serve that path: a laya-server deployment, and Ollama 0.40+,
    which exposes it natively. llm-hub authenticates the *caller* with its own API key
    (minted at /admin -> API keys and hashed into the SQLite api_keys table), then
    forwards to `backend` and signs that call with the key read from the environment
    variable named by `api_key_env` (default SYSTEMONE_SERVER_API_KEY), so the backend key
    never has to live in config.yaml. A backend that needs no credential just ignores it.

    `model` is the default model name injected into the forwarded body. "local" is a
    sentinel meaning "do not inject" - the caller then has to set `model` itself. laya-server
    reads `model` as a routing hint and only accepts the literal enum `auto` / `english` /
    `multilingual` / `typed-decisions` (anything else is HTTP 422), while an Ollama backend
    expects a real model name and answers **404** for `auto`. Pointing at Ollama therefore
    means naming a model that instance actually serves.
    """

    enabled: bool = False
    # System One backend base URL, e.g. http://127.0.0.1:8080 (laya-server) or
    # http://llm.home:11435 (Ollama 0.40+).
    backend: str = "http://127.0.0.1:8080"
    # Default model to send; "local" = do-not-override sentinel (see class docstring).
    model: str = "auto"
    # Name of the environment variable holding the backend's API key.
    api_key_env: str = "SYSTEMONE_SERVER_API_KEY"
    # Endpoint path on the System One backend.
    path: str = "v1/systemone"
    timeout: float = 60.0

    @field_validator("backend")
    @classmethod
    def _check_backend(cls, value: str) -> str:
        return validate_service_url(value)

    @field_validator("api_key_env")
    @classmethod
    def _check_key_env(cls, value: str) -> str:
        return validate_key_env(value)


class WebSearchConfig(BaseModel):
    """Web search served by an OpenCode API Gateway upstream (`POST /v1/web-search`).

    The caller authenticates against llm-hub with the gateway's own API key and posts to
    `POST /v1/tools/web-search`; the body is forwarded verbatim to the search provider's
    `path`, and its JSON response is passed back unchanged.

    The provider is an entry in `upstreams` (usually `type: "opencode"`), so its address,
    timeout and extra headers are configured once and shared with model routing and
    `/v1/models` aggregation:
      - `upstream` selects it by name; empty means "the first `opencode` upstream".
        An explicit name that matches no upstream is an error, never a silent fallback.
      - `api_key_env` names an environment variable holding the provider's API key.
        Empty (default) = the provider is called unsigned, which is all the current
        OpenCode gateway needs. When set, its value is sent as `Authorization: Bearer`.
    """

    enabled: bool = False
    # Name of the upstream providing web search; "" = first upstream of type "opencode".
    upstream: str = ""
    # Search endpoint path on that upstream.
    path: str = "v1/web-search"
    # Name of the environment variable holding the provider API key; "" = no signing.
    api_key_env: str = ""
    timeout: float = 60.0

    @field_validator("api_key_env")
    @classmethod
    def _check_key_env(cls, value: str) -> str:
        return validate_key_env(value)


class ApiModelsConfig(BaseModel):
    """Which models each API family may offer, read by the console's API testers.

    `upstreams[].models` says what an instance declares, not what role it plays: nothing
    there tells a tester that `bge-m3` answers `/api/embed` and `qwen3.5:9b` answers
    `/api/generate`. This block is that missing statement, one list per API family:

      - `generate`  -> `POST /api/generate` and `POST /v1/chat/completions`
      - `embed`     -> `POST /api/embed` and `POST /v1/embeddings`
      - `rerank`    -> `POST /v1/rerank` (empty falls back to `rerank.models`)
      - `systemone` -> `POST /v1/systemone`
      - `websearch` -> `POST /v1/tools/web-search` (the provider picks when a list is empty)

    Every list is optional and a name may be a real model id or any alias. With a list
    left empty the choices are derived instead (everything declared, minus the names
    claimed by another family), so a config written before this block existed keeps
    working - the lists only ever narrow what the selector shows.
    """

    generate: list[str] = Field(default_factory=list)
    embed: list[str] = Field(default_factory=list)
    rerank: list[str] = Field(default_factory=list)
    systemone: list[str] = Field(default_factory=list)
    websearch: list[str] = Field(default_factory=list)


class RequestLogConfig(BaseModel):
    """SQLite request log behind the web console's "Requests" view.

    `enabled` is the per-request write switch: with it off the gateway records nothing at
    all -- request/response bodies are not even copied -- and the console shows an empty,
    disabled log. It is read on every request, so flipping it in `config.yaml` takes
    effect immediately (hot reload), without a restart.

    Storage location and retention are deployment-level and stay in the environment:
    `REQUEST_LOG_DB` and `REQUEST_LOG_RETENTION_DAYS`.
    """

    enabled: bool = True


class RateLimitConfig(BaseModel):
    """In-memory token-bucket rate limiter (single-process only).

    `by` selects the bucket key: "ip" (default) or "key" (per API key).
    `burst` is the bucket capacity (max immediate requests); `per_minute` is the
    sustained refill rate. Requests beyond the budget get HTTP 429. Disabled by
    default - enable it either behind a reverse proxy that already rate-limits, or
    as a cheap first line of defence on a public gateway.
    """

    enabled: bool = False
    per_minute: int = 60
    burst: int = 20
    by: Literal["ip", "key"] = "ip"


class SecurityConfig(BaseModel):
    """One security tree for BOTH listeners, split into ``internal`` and ``public``.

    Everything that is not zone-specific stays here as a common hardening base.
    Ollama's model-management blocklist is **not** here: it lives in
    ``security.public.admin_endpoints`` and is only enforced on the public
    listener, because pulling a model is a normal operation on a trusted LAN.

    ``internal`` controls the trusted ``server.port`` listener; ``public`` controls the
    internet-facing ``security.public.port`` listener. Authentication is *not* a
    toggle any more: the public zone always requires a key or console session and
    serves only the method+path pairs in ``public.path_allowlist``. The former
    ``auth`` node, ``allowed_http_methods``, global ``path_allowlist``,
    ``allow_anonymous_health``, ``expose_health_details`` and IP trust anchors have
    been removed - see ``_migrate_legacy_security`` below for the upgrade mapping.
    """

    internal: InternalSecurityConfig = Field(default_factory=InternalSecurityConfig)
    public: PublicSecurityConfig = Field(default_factory=PublicSecurityConfig)

    blocked_paths: list[str] = Field(default_factory=list)
    cors_origins: list[str] = Field(default_factory=list)
    max_body_bytes: int = 32 * 1024 * 1024
    allow_query_api_key: bool = False
    allowed_client_ips: list[str] = Field(default_factory=list)
    # CIDR list of reverse proxies whose X-Forwarded-For may be trusted for IP
    # fencing, rate-limit buckets and request-log audit rows. This is NOT a zone
    # anchor: with proxy_headers=False, an undeclared proxy is simply untrusted.
    trusted_proxies: list[str] = Field(default_factory=list)
    # In-memory token-bucket rate limiter (DoS / brute-force mitigation).
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)

    @field_validator("allowed_client_ips", "trusted_proxies")
    @classmethod
    def _validate_cidrs(cls, values: list[str]) -> list[str]:
        for entry in values:
            try:
                ipaddress.ip_network(entry, strict=False)
            except ValueError as exc:
                raise ValueError(f"security CIDR entry {entry!r} is invalid: {exc}") from exc
        return values

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_security(cls, data: Any) -> Any:
        """Translate the pre-1.3 flat security fields into the two-zone tree.

        Old ``path_allowlist`` was a global list of regexes with no verb information.
        It is mapped onto every verb from old ``allowed_http_methods`` (default
        GET/POST) so a restrictive deployment does not silently widen on upgrade.
        If the new ``public.path_allowlist`` is present it always wins.
        """
        if not isinstance(data, dict):
            return data
        data = dict(data)
        legacy_methods = data.pop("allowed_http_methods", None)
        legacy_paths = data.pop("path_allowlist", None)
        legacy_version = data.pop("expose_version", None)
        legacy_admin = data.pop("admin_endpoints", None)
        had_health_details = "expose_health_details" in data
        data.pop("expose_health_details", None)

        dropped = []
        if had_health_details:
            dropped.append("expose_health_details (no-op)")
        if legacy_methods is not None:
            dropped.append("allowed_http_methods (now public.path_allowlist keys)")
        if legacy_paths is not None:
            dropped.append("path_allowlist (now public.path_allowlist values)")
        if legacy_version is not None:
            dropped.append("expose_version (now internal.expose_version)")
        if legacy_admin is not None:
            dropped.append("admin_endpoints (now public.admin_endpoints)")
        if dropped:
            logger.warning(
                "Legacy security field(s) migrated/ignored: %s. Update config.yaml to the "
                "security.internal / security.public structure",
                "; ".join(dropped),
            )

        internal = dict(data.get("internal") or {})
        if legacy_version is not None and "expose_version" not in internal:
            internal["expose_version"] = bool(legacy_version)
        if internal:
            data["internal"] = internal

        public = dict(data.get("public") or {})
        if legacy_admin is not None and "admin_endpoints" not in public:
            public["admin_endpoints"] = legacy_admin
        if legacy_paths and "path_allowlist" not in public:
            verbs = [str(v).strip().upper() for v in (legacy_methods or ["GET", "POST"])]
            mapped = {v: list(legacy_paths) for v in verbs if v in HTTP_METHOD_NAMES}
            if mapped:
                public["path_allowlist"] = mapped
                logger.warning(
                    "Migrated legacy global path_allowlist (%d pattern(s)) to "
                    "security.public.path_allowlist for verbs %s; verify this is the "
                    "public surface you want",
                    len(legacy_paths),
                    ", ".join(sorted(mapped)),
                )
        if public:
            data["public"] = public
        return data

    # Ollama management endpoints are blocked on the PUBLIC zone only
    # (``security.public.admin_endpoints``); see ``is_path_blocked``.

    def is_path_blocked(self, path_clean: str, public: bool = True) -> bool:
        """Whether the proxy must refuse this path.

        ``public`` selects the zone: the Ollama admin blocklist is applied only to
        the untrusted listener (the default, so a caller that forgets the flag stays
        fail-closed). ``blocked_paths`` is an explicit operator deny-list and applies
        everywhere.

        ``api/blobs/<digest>`` is a prefix, not a literal name, so it is matched
        separately - but only when the mode actually blocks blobs, so
        ``admin_endpoints: allow`` really forwards every admin call.
        """
        if public:
            blocked = self.public.blocked_admin_paths()
            if path_clean in blocked or ("api/blobs" in blocked and path_clean.startswith("api/blobs")):
                return True
        for pat in self.blocked_paths:
            p = pat.strip("/")
            if p == path_clean or (p.endswith("*") and path_clean.startswith(p[:-1])):
                return True
        return False


class Config(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    upstreams: list[UpstreamConfig] = Field(default_factory=list)
    default_upstream: str | None = None
    aliases: dict[str, AliasConfig] = Field(default_factory=dict)
    # num_ctx 固化：按真实模型名（resolved.target）强制覆盖 Ollama 上游的 options.num_ctx，
    # 忽略客户端请求里携带的 num_ctx。仅对 type=="ollama" 的 upstream 生效
    # （LM Studio / OpenAI 兼容用 max_tokens，不认 num_ctx）。
    # 目的：客户端无法控制上下文长度时，由网关统一钳制，避免同一模型因不同 ctx
    # 被 Ollama 反复新建实例、显存叠加。例: {"qwen3.6:35b-pruned": 65536, "qwen3.5:9b": 16384}
    num_ctx_overrides: dict[str, int] = Field(default_factory=dict)
    rerank: RerankConfig = Field(default_factory=RerankConfig)
    systemone: SystemOneConfig = Field(default_factory=SystemOneConfig)
    websearch: WebSearchConfig = Field(default_factory=WebSearchConfig)
    api_models: ApiModelsConfig = Field(default_factory=ApiModelsConfig)
    request_log: RequestLogConfig = Field(default_factory=RequestLogConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)

    # Names generated by `_expand_upstream_aliases` below; never read from config.yaml.
    _renamed_alias_names: set[str] = PrivateAttr(default_factory=set)

    @model_validator(mode="before")
    @classmethod
    def _migrate_auth_node(cls, data: Any) -> Any:
        """Fold the removed top-level ``auth`` node into ``security.internal``.

        ``auth.enabled: true`` meant "callers need a key" on the only listener that
        existed. That is no longer the axis: the public listener is keyed
        unconditionally, so ``auth.enabled`` only decides whether the *LAN* socket is
        keyed, and only when no ``security.public.port`` exists (a single, possibly
        internet-exposed socket stays fail-closed). With a public listener configured
        the LAN socket goes back to anonymous - the whole point of splitting the
        zones. An explicit ``security.internal.allow_anonymous`` always wins.

        The IP whitelists and ``allow_anonymous_health`` are dropped: the zone is now
        decided by the listening socket, and liveness probes follow the same zone rule
        as every other path. A stale ``auth.api_keys`` list never reaches the key
        store; keys live in the SQLite ``api_keys`` table.
        """
        if not isinstance(data, dict) or "auth" not in data:
            return data
        data = dict(data)
        auth = data.pop("auth")
        if not isinstance(auth, dict):
            logger.warning("Top-level auth is not a mapping - ignoring it; use security.internal / security.public")
            return data

        sec = dict(data.get("security") or {})
        internal = dict(sec.get("internal") or {})
        if "enabled" in auth and "allow_anonymous" not in internal:
            # Legacy `auth.enabled: true` locked the only listener that existed. The
            # internet-facing side is now keyed by definition, so what is left to decide
            # is the LAN socket:
            #   * a public listener is configured -> the old intent is already covered
            #     there, and the LAN socket goes back to being key-free (the point of
            #     having two zones);
            #   * no public listener -> this single socket may still face the internet,
            #     so stay fail-closed and keep it keyed.
            public_raw = sec.get("public") or {}
            public_port = (
                public_raw.get("port")
                if isinstance(public_raw, dict)
                else getattr(public_raw, "port", None)
            )
            keyed = bool(auth["enabled"]) and public_port is None
            internal["allow_anonymous"] = not keyed
            if keyed:
                logger.warning(
                    "Migrating auth.enabled=true to security.internal.allow_anonymous=false: "
                    "no security.public.port is configured, so this single socket may still be "
                    "reachable from the internet. For a key-free LAN listener plus a keyed "
                    "internet listener, set security.public.port to a second port and "
                    "security.internal.allow_anonymous: true"
                )
            else:
                logger.warning(
                    "Legacy auth.enabled=%s is obsolete: security.public.port=%s already "
                    "requires a key on every request, so security.internal.allow_anonymous "
                    "defaults to true for the LAN socket. Set it explicitly if you want the "
                    "LAN keyed as well",
                    auth["enabled"],
                    public_port,
                )
        for key in ("allow_anonymous_health", "trusted_networks", "allow_access_ip_whitelist",
                    "allow_access_ip_whilelist", "allow_ip_whitelist", "api_keys"):
            if key in auth:
                logger.warning(
                    "auth.%s is no longer supported and is ignored: trust zones are decided "
                    "by server.port / security.public.port, keys live in the SQLite api_keys table",
                    key,
                )
        if internal:
            sec["internal"] = internal
        data["security"] = sec
        return data

    def public_ports(self) -> frozenset[int]:
        """Sockets that serve untrusted traffic (empty = no public listener)."""
        port = self.security.public.port
        return frozenset({port}) if port is not None else frozenset()

    def listen_ports(self) -> list[int]:
        """Every distinct TCP port ``serve.py`` must bind, internal first."""
        ports = [self.server.port]
        public = self.security.public.port
        if public is not None and public not in ports:
            ports.append(public)
        return ports

    @model_validator(mode="after")
    def _expand_upstream_aliases(self) -> Config:
        """Turn each upstream's `model_aliases` into real alias entries.

        This lets a verbose upstream id (e.g. LM Studio's "qwen/qwen3.5-9b") be reached
        through a short friendly name ("qwen3.5:9b") without duplicating it in `aliases`.
        The generated names are recorded because the two nodes mean different things: a
        `model_aliases` entry only renames an existing model, while an entry written under
        root `aliases:` is a new model that carries its own fixed parameters.
        """
        for up in self.upstreams:
            for short_name, real_id in up.model_aliases.items():
                if short_name in self.aliases:
                    continue  # an explicit global alias wins
                self.aliases[short_name] = AliasConfig(
                    model=real_id,
                    upstream=up.name,
                    description=f"Short name for upstream '{up.name}' model '{real_id}'",
                )
                self._renamed_alias_names.add(short_name)
        return self

    # ---------------------------------------------------------------- alias kinds ---
    def is_renamed_model(self, name: str) -> bool:
        """True when `name` came from `upstreams[].model_aliases`: a pure display rename."""
        return name in self._renamed_alias_names

    def derived_aliases(self) -> dict[str, AliasConfig]:
        """Entries declared under root `aliases:` - each is a model of its own."""
        return {name: alias for name, alias in self.aliases.items() if not self.is_renamed_model(name)}

    # ---------------------------------------------------------------- query ---
    def get_upstream(self, name: str | None) -> UpstreamConfig | None:
        if not name:
            return None
        for up in self.upstreams:
            if up.name == name:
                return up
        return None

    def default_upstream_obj(self) -> UpstreamConfig | None:
        """The explicitly configured default upstream, or ``None`` if none is set.

        Unlike a silent "first upstream" fallback, an unset ``default_upstream`` means there
        is no default: callers (proxy routing, the Messages API) then return 404 rather than
        routing to an arbitrary upstream the operator never chose.
        """
        if not self.default_upstream:
            return None
        return self.get_upstream(self.default_upstream)

    def websearch_upstream_obj(self) -> UpstreamConfig | None:
        """Locate the upstream that provides web search.

        An explicit `websearch.upstream` name wins and never falls back silently; with no
        name configured, the first `type: "opencode"` upstream is the search provider.
        """
        name = (self.websearch.upstream or "").strip()
        if name:
            return self.get_upstream(name)
        return next((up for up in self.upstreams if up.type == "opencode"), None)

    @staticmethod
    def _match(up: UpstreamConfig, model: str) -> bool:
        """Exact or prefix-wildcard match (qwen3:* matches qwen3:8b)."""
        for pattern in up.models:
            if pattern == model:
                return True
            if pattern.endswith("*") and model.startswith(pattern[:-1]):
                return True
        return False

    def find_upstream_for_model(self, model: str) -> UpstreamConfig | None:
        """Return the upstream serving `model`, honoring the lowest `priority` value.

        When several upstreams declare the same model, the one with the smallest
        `priority` is preferred; ties preserve the order defined in `upstreams`.
        """
        best: UpstreamConfig | None = None
        best_priority: int = 0
        for up in self.upstreams:
            if self._match(up, model) and (best is None or up.priority < best_priority):
                best = up
                best_priority = up.priority
        return best

    # ------------------------------------------------------------ alias resolution ---
    def resolve(self, model: str) -> ResolvedTarget:
        """Resolve the requested model name into (real model name, target upstream, params to inject)."""
        alias = self.aliases.get(model)
        if alias is not None:
            upstream = self.get_upstream(alias.upstream) or self.find_upstream_for_model(alias.model)
            if upstream is None:
                upstream = self.default_upstream_obj()
            return ResolvedTarget(
                requested=model,
                target=alias.model,
                upstream=upstream,
                extra_params=dict(alias.params),
                is_alias=True,
                alias_description=alias.description,
            )

        upstream = self.find_upstream_for_model(model)
        # `routing.strict` refuses to guess a *named* model; an empty name is not a guess
        # but the "no model in this body" case (GETs and the Ollama admin calls, which
        # carry `name`), so those always land on the default upstream.
        if upstream is None and (not self.routing.strict or not model):
            upstream = self.default_upstream_obj()
        return ResolvedTarget(
            requested=model,
            target=model,
            upstream=upstream,
            extra_params={},
            is_alias=False,
        )

    # --------------------------------------------------- Aggregated model listing ---
    def aggregated_models(self) -> list[dict[str, Any]]:
        """Returns all models declared by upstreams plus all aliases, for /v1/models."""
        items: list[dict[str, Any]] = []
        seen = set()
        # When a model is declared by several upstreams, the lowest-priority one is the
        # preferred (owner) upstream - this mirrors routing behavior in find_upstream_for_model.
        owner: dict[str, UpstreamConfig] = {}
        for up in self.upstreams:
            for m in up.models:
                if m not in owner or up.priority < owner[m].priority:
                    owner[m] = up
        for m, up in owner.items():
            if m in seen:
                continue
            seen.add(m)
            items.append(
                {
                    "id": m,
                    "object": "model",
                    "created": 0,
                    "owned_by": "ollama",
                    "upstream": up.name,
                    "type": "upstream",
                    "wildcard": m.endswith("*"),
                }
            )
        for alias_name, alias in self.aliases.items():
            if alias_name in seen:
                continue
            seen.add(alias_name)
            upstream = self.get_upstream(alias.upstream) or self.find_upstream_for_model(alias.model)
            items.append(
                {
                    "id": alias_name,
                    "object": "model",
                    "created": 0,
                    "owned_by": "ollama",
                    "upstream": upstream.name if upstream else None,
                    "type": "alias",
                    "target": alias.model,
                }
            )
        return items


@dataclass
class ResolvedTarget:
    requested: str
    target: str
    upstream: UpstreamConfig | None
    extra_params: dict[str, Any] = dc_field(default_factory=dict)
    is_alias: bool = False
    alias_description: str = ""


# --------------------------------------------------------------------------- #
# Default configuration
# --------------------------------------------------------------------------- #
def build_default_config() -> dict[str, Any]:
    return {
        "server": {
            "host": "0.0.0.0",
            "port": 8000,
            "log_level": "info",
            "request_timeout": 600.0,
            "max_concurrency": 64,
        },
        "routing": {"strict": False},
        "upstreams": [
            {
                "name": "local",
                "type": "ollama",
                "base_url": "http://host.docker.internal:11434",
                "models": ["qwen3:8b", "qwen3:4b", "bge-m3:latest", "qwen3-reranker:4b"],
                "timeout": 600,
                "description": "Default local Ollama instance",
            }
        ],
        "default_upstream": "local",
        "aliases": {
            "qwen3:8b-nothink": {
                "model": "qwen3:8b",
                "params": {"think": False},
                "description": "8B model with thinking disabled",
            }
        },
        "rerank": {
            "enabled": True,
            "endpoint": "/v1/rerank",
            "mode": "logprobs",
            "models": ["qwen3-reranker:4b"],
            "instruction": DEFAULT_RERANK_INSTRUCTION,
            "yes_token": "yes",
            "no_token": "no",
            "yes_aliases": [],
            "no_aliases": [],
            "temperature": 0.0,
            "top_logprobs": 20,
            "max_tokens": 4,
            "token_scan_depth": 4,
            "stop": [],
            "options": {"num_ctx": 8192},
            "keep_alive": "3m",
            "logprobs_fallback": True,
            "fallback_yes": 0.9,
            "fallback_no": 0.1,
            "fallback_unknown": 0.0,
            "normalize": False,
            "max_concurrency": 8,
            "return_documents": True,
            "model_modes": {},
            "upstream_api": "auto",
            "raw_prompt": True,
            "disable_thinking": True,
            "max_documents": 0,
            "max_document_chars": 0,
            "on_error": "score_zero",
        },
        "systemone": {
            # Proxy for the System One decision API (laya-server, or Ollama 0.40+ which
            # exposes /v1/systemone natively and needs a real model name instead of "auto").
            # The caller authenticates with a gateway API key (minted at /admin);
            # the gateway then forwards to `backend` using the key from `api_key_env`.
            "enabled": False,
            "backend": "http://127.0.0.1:8080",
            "model": "local",
            "api_key_env": "SYSTEMONE_SERVER_API_KEY",
            "path": "v1/systemone",
            "timeout": 60.0,
        },
        "websearch": {
            # Web search served by an OpenCode API Gateway upstream (see `upstreams`).
            # The caller authenticates with a gateway API key (minted at /admin);
            # the provider address comes from the selected upstream's `base_url`.
            "enabled": False,
            "upstream": "",
            "path": "v1/web-search",
            "api_key_env": "",
            "timeout": 60.0,
        },
        "request_log": {
            # Writes every forwarded request into the SQLite log the web console reads.
            # Turn it off to keep no request/answer history on disk at all.
            "enabled": True,
        },
        "api_models": {
            # Model choices for the console's per-endpoint API testers. Empty = derive
            # from `upstreams[].models` (see the ApiModelsConfig docstring).
            "generate": [],
            "embed": [],
            "rerank": [],
            "systemone": [],
            "websearch": [],
        },
        "security": {
            "internal": {
                # Trusted LAN listener (server.port). Callers here may be anonymous.
                "allow_anonymous": True,
                "expose_version": True,
                "expose_docs": True,
            },
            "public": {
                # null = no public listener. Set to 8001 for a two-zone instance, or
                # to server.port (8000) for a public-only instance.
                "port": None,
                # The COMPLETE public surface. Unlisted method+path pairs are 404
                # before routing; every listed pair still needs a key or session.
                "path_allowlist": {verb: list(pats) for verb, pats in DEFAULT_PUBLIC_PATHS.items()},
                # Ollama model management (pull/push/create/delete/copy/blobs) is
                # blocked on the public listener only; the internal LAN listener
                # forwards these untouched.
                "admin_endpoints": "deny",
            },
            "blocked_paths": [],
            "cors_origins": [],
            "max_body_bytes": 32 * 1024 * 1024,
            "allow_query_api_key": False,
            "allowed_client_ips": [],
            "trusted_proxies": [],
            "rate_limit": {"enabled": False, "per_minute": 60, "burst": 20, "by": "ip"},
        },
    }


# --------------------------------------------------------------------------- #
# Serialization
# --------------------------------------------------------------------------- #
def _dump_yaml(data: dict[str, Any]) -> str:
    """Render the config mapping as YAML: readable, order-preserving, non-ASCII kept literal."""
    header = (
        "# llm-hub gateway configuration.\n"
        "# Secrets do not belong here: caller API keys live in llmhub.db and are minted\n"
        "# at /admin -> API keys; the console password is ADMIN_PASSWORD in the environment.\n"
    )
    return header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)


# --------------------------------------------------------------------------- #
# Configuration manager (hot reload)
# --------------------------------------------------------------------------- #
class ConfigManager:
    """Holds the current config and reloads it when the file mtime changes."""

    def __init__(self, path: str = CONFIG_PATH):
        self.path = Path(path)
        self._config: Config = Config()
        self._mtime: float = 0.0
        self._last_check: float = 0.0
        self._lock = threading.RLock()
        self._reload_if_needed(force=True)

    # ------------------------------------------------------------------ loading ---
    def _active_path(self) -> Path:
        """The file to read: the configured path, or the legacy `config.json` beside it."""
        if self.path.exists():
            return self.path
        legacy = self.path.parent / LEGACY_CONFIG_NAME
        if legacy.is_file() and legacy != self.path:
            logger.warning(
                "%s not found, falling back to the legacy %s - rename it to %s",
                self.path,
                legacy,
                self.path.name,
            )
            return legacy
        return self.path

    def _load(self) -> Config:
        path = self._active_path()
        if not path.exists():
            logger.warning("Config file %s not found, generating a default template", path)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(_dump_yaml(build_default_config()), encoding="utf-8")
                logger.info("Default config written to %s (edit upstreams and aliases as needed)", path)
            except Exception as exc:  # pragma: no cover
                logger.error("Failed to write the default config: %s", exc)
            cfg = Config(**apply_legacy_listener_env(build_default_config()))
            return cfg

        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("Failed to parse config %s: %s, keeping the previous config", path, exc)
            return self._config

        if not isinstance(raw, dict):
            logger.error(
                "Config %s is %s, expected a mapping, keeping the previous config",
                path,
                type(raw).__name__,
            )
            return self._config

        # Listener/zone env vars are a migration hint only; config.yaml values win.
        raw = apply_legacy_listener_env(raw)

        try:
            cfg = Config(**raw)
        except Exception as exc:
            logger.error("Config validation failed: %s, keeping the previous config", exc)
            return self._config

        # `default_upstream` is the ONLY routing input for calls that carry no `model`
        # (api/pull sends `name`, api/tags sends nothing). A value that names no upstream is
        # otherwise silent until those calls answer 404 with a message about a missing model.
        if cfg.default_upstream and cfg.get_upstream(cfg.default_upstream) is None:
            logger.error(
                "default_upstream=%r names no upstream in this config (available: %s). Every call "
                "without a `model` field - api/pull, api/push, api/create, api/delete, api/copy, "
                "api/tags, api/ps - answers 404 until it matches an upstream's `name`.",
                cfg.default_upstream,
                ", ".join(u.name for u in cfg.upstreams) or "none",
            )

        logger.info(
            "Config loaded: %s | upstreams=%d | aliases=%d | listen=%s | public=%s",
            path,
            len(cfg.upstreams),
            len(cfg.aliases),
            ",".join(str(p) for p in cfg.listen_ports()),
            ",".join(str(p) for p in sorted(cfg.public_ports())) or "none",
        )
        return cfg

    def _reload_if_needed(self, force: bool = False) -> None:
        with self._lock:
            now = time.time()
            if not force and now - self._last_check < 1.0:
                return
            self._last_check = now
            try:
                mtime = self._active_path().stat().st_mtime
            except OSError:
                mtime = 0.0
            if force or mtime != self._mtime:
                self._mtime = mtime
                self._config = self._load()

    # ------------------------------------------------------------------ public API ---
    @property
    def config(self) -> Config:
        self._reload_if_needed()
        return self._config

    def reload(self) -> Config:
        with self._lock:
            self._config = self._load()
            try:
                self._mtime = self._active_path().stat().st_mtime
            except OSError:
                self._mtime = 0.0
            return self._config

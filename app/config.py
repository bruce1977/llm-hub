"""
Configuration loading module.

Reads the gateway configuration from /data/config.yaml and provides model routing and alias
resolution. Supports hot reload (checks the file mtime at most once per second).

The file is YAML; because YAML 1.2 is a superset of JSON, a `CONFIG_PATH` that still points at
a `.json` file parses exactly the same way.
"""

from __future__ import annotations

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
from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger("llm_hub")

CONFIG_PATH = os.getenv("CONFIG_PATH", "/data/config.yaml")

#: A deployment that still only has the pre-YAML `config.json` next to the new default path
#: keeps working: it is loaded (with a warning) instead of being replaced by a fresh template.
LEGACY_CONFIG_NAME = "config.json"


#: Environment variables that provide API keys without writing them into config.yaml
ENV_API_KEYS = "GATEWAY_API_KEYS"
ENV_API_KEYS_FILE = "GATEWAY_API_KEYS_FILE"

#: Environment variable to disable authentication entirely (e.g. GATEWAY_AUTH_DISABLED=true)
ENV_AUTH_DISABLED = "GATEWAY_AUTH_DISABLED"

#: API keys are configured by the environment only: inline `GATEWAY_API_KEYS`, or
#: `GATEWAY_API_KEYS_FILE` pointing at a mounted secret file (`data/.keys`). They live in this
#: module-level store instead of on `AuthConfig`, so config.yaml - which gets committed, pasted
#: into issues and read by the console - can neither carry nor revive a credential.
_runtime_api_keys: list[str] = []


def set_api_keys(keys: list[str]) -> None:
    """Replace the runtime key list; called once per config load."""
    global _runtime_api_keys
    _runtime_api_keys = list(keys)


def get_api_keys() -> list[str]:
    """The keys collected from the environment by the last config load."""
    return _runtime_api_keys


def default_keys_file() -> Path:
    """The implicit key file: `.keys` next to config.yaml (e.g. /config/.keys, /data/.keys).

    Resolved from the *current* CONFIG_PATH environment value rather than the module
    constant, so a test or launcher that repoints CONFIG_PATH also repoints the secret.
    """
    return Path(os.getenv("CONFIG_PATH", CONFIG_PATH)).parent / ".keys"


def collect_env_api_keys() -> list[str]:
    """Collect API keys from the environment.

    Three sources are supported (they may be combined):
      - GATEWAY_API_KEYS      inline value, comma/newline/space separated
      - GATEWAY_API_KEYS_FILE path to a file holding the keys (Docker/Kubernetes secrets
                              are mounted as files, which keeps them out of `docker inspect`)
      - `.keys` beside config.yaml, read automatically when GATEWAY_API_KEYS_FILE is unset,
        so a deployment only has to mount `/config/.keys` (or `/data/.keys`) - no extra
        environment variable. An explicitly set GATEWAY_API_KEYS_FILE always wins.
    """
    collected: list[str] = []

    inline = os.getenv(ENV_API_KEYS, "").strip()
    if inline:
        collected.extend(p.strip() for p in re.split(r"[\s,]+", inline) if p.strip())

    key_file = os.getenv(ENV_API_KEYS_FILE, "").strip()
    if not key_file:
        implicit = default_keys_file()
        if implicit.is_file():
            key_file = str(implicit)
    if key_file:
        try:
            content = Path(key_file).read_text(encoding="utf-8")
            collected.extend(p.strip() for p in re.split(r"[\s,]+", content) if p.strip())
            logger.info("Loaded API key(s) from %s", key_file)
        except OSError as exc:
            logger.error("Failed to read %s=%s: %s", ENV_API_KEYS_FILE, key_file, exc)

    return collected


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
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"
    request_timeout: float = 600.0
    max_concurrency: int = 64
    upstream_keepalive_expiry: float = 30.0


class AuthConfig(BaseModel):
    """Caller authentication. Keys are never configured here - see ENV_API_KEYS above."""

    enabled: bool = True
    allow_anonymous_health: bool = True
    # Regexes for client addresses that may open /docs and are exempt from the API-key check.
    # Matched against the TCP peer, deliberately not X-Forwarded-For: any client can put an
    # arbitrary address in that header, and honouring it here would be a key bypass.
    allow_access_ip_whitelist: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _drop_legacy_api_keys(cls, data: Any) -> Any:
        if isinstance(data, dict) and "api_keys" in data:
            logger.warning(
                "auth.api_keys no longer exists - %d key(s) in config.yaml are ignored; "
                "supply keys through %s / %s (the .keys file) instead",
                len(data["api_keys"] or []),
                ENV_API_KEYS,
                ENV_API_KEYS_FILE,
            )
            data = {k: v for k, v in data.items() if k != "api_keys"}

        if isinstance(data, dict):
            # Pydantic ignores unknown fields by default, so the common misspelling
            # "whilelist" would silently leave the whitelist empty (/docs 404, no key
            # bypass) without any error. Accept it, warn, and normalise to the real name.
            for typo in ("allow_access_ip_whilelist", "allow_ip_whitelist"):
                if typo in data:
                    value = data.pop(typo)
                    logger.warning(
                        "auth.%s is not a field - read as auth.allow_access_ip_whitelist; "
                        "please rename it in config.yaml",
                        typo,
                    )
                    data.setdefault("allow_access_ip_whitelist", value)
        return data

    @property
    def api_keys(self) -> list[str]:
        """Keys collected from the environment by the last load; unreadable from config.yaml."""
        return _runtime_api_keys


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
    (cfg.auth.api_keys, sourced from `.env` / GATEWAY_API_KEYS - the `.keys` config), then
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
    """Public-facing hardening. All Ollama model-management endpoints are blocked by default.

    admin_endpoints:
      "deny"     (default) - block pull/push/create/delete/copy and blob up/downloads
      "readonly" - block delete/copy/push/create and blobs (still allow `pull` to fetch models)
      "allow"    - forward everything (only for trusted internal networks)

    allowed_http_methods:
      only these verbs are served; anything else (PUT/PATCH/DELETE/HEAD/TRACE/OPTIONS)
      is rejected with 405. Defaults to GET/POST - that is all an LLM gateway needs.

    path_allowlist:
      regex perimeter control - when non-empty, a request path must match at least one
      pattern or it is rejected with 403. Empty = allow every path (disabled).

    rate_limit:
      optional in-memory token-bucket limiter to blunt brute-force / DoS.
    """

    admin_endpoints: Literal["deny", "readonly", "allow"] = "deny"
    blocked_paths: list[str] = Field(default_factory=list)
    cors_origins: list[str] = Field(default_factory=list)
    max_body_bytes: int = 32 * 1024 * 1024
    allow_query_api_key: bool = False
    # Legacy: /health answers {"ok": true} only, so nothing reads this any more.
    expose_health_details: bool = False
    fail_on_weak_keys: bool = True
    allowed_client_ips: list[str] = Field(default_factory=list)
    # Only these HTTP methods are accepted; everything else returns 405.
    allowed_http_methods: list[str] = Field(default_factory=lambda: ["GET", "POST"])
    # Regex path allowlist (perimeter control). Empty = allow all paths (disabled).
    path_allowlist: list[str] = Field(default_factory=list)
    # In-memory token-bucket rate limiter (DoS / brute-force mitigation).
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)

    # Ollama management endpoints that are unsafe to expose on a public gateway.
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

    def is_path_blocked(self, path_clean: str) -> bool:
        if path_clean in self.blocked_admin_paths() or path_clean.startswith("api/blobs"):
            return True
        for pat in self.blocked_paths:
            p = pat.strip("/")
            if p == path_clean or (p.endswith("*") and path_clean.startswith(p[:-1])):
                return True
        return False


class Config(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
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
    request_log: RequestLogConfig = Field(default_factory=RequestLogConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)

    @model_validator(mode="after")
    def _expand_upstream_aliases(self) -> Config:
        """Turn each upstream's `model_aliases` into real alias entries.

        This lets a verbose upstream id (e.g. LM Studio's "qwen/qwen3.5-9b") be reached
        through a short friendly name ("qwen3.5:9b") without duplicating it in `aliases`.
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
        return self

    # ---------------------------------------------------------------- query ---
    def get_upstream(self, name: str | None) -> UpstreamConfig | None:
        if not name:
            return None
        for up in self.upstreams:
            if up.name == name:
                return up
        return None

    def default_upstream_obj(self) -> UpstreamConfig | None:
        if self.default_upstream:
            found = self.get_upstream(self.default_upstream)
            if found:
                return found
        # Falls back to the first upstream when not specified
        return self.upstreams[0] if self.upstreams else None

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
        if upstream is None and not self.routing.strict:
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
        "auth": {
            # Keys are not configurable here: provide them through the GATEWAY_API_KEYS env var
            # or GATEWAY_API_KEYS_FILE (data/.keys), so secrets never live in config.yaml.
            "enabled": True,
            "allow_anonymous_health": True,
            # Regexes for addresses that may open /docs and skip the API-key check.
            "allow_access_ip_whitelist": [],
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
            # The caller authenticates with the gateway's own API key (GATEWAY_API_KEYS);
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
            # The caller authenticates with the gateway's own API key (GATEWAY_API_KEYS);
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
    }


# --------------------------------------------------------------------------- #
# Serialization
# --------------------------------------------------------------------------- #
def _dump_yaml(data: dict[str, Any]) -> str:
    """Render the config mapping as YAML: readable, order-preserving, non-ASCII kept literal."""
    header = (
        "# llm-hub gateway configuration.\n"
        "# Secrets do not belong here: API keys come from GATEWAY_API_KEYS / GATEWAY_API_KEYS_FILE.\n"
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
            return Config(**build_default_config())

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

        try:
            cfg = Config(**raw)
        except Exception as exc:
            logger.error("Config validation failed: %s, keeping the previous config", exc)
            return self._config

        # Keys come from the environment only, so config.yaml never has to hold a secret.
        set_api_keys(collect_env_api_keys())

        # Allow disabling auth via environment variable (takes precedence over config file)
        if os.getenv(ENV_AUTH_DISABLED, "").lower() in ("true", "1", "yes"):
            cfg.auth.enabled = False
            logger.info("Authentication disabled via %s=true", ENV_AUTH_DISABLED)

        logger.info(
            "Config loaded: %s | upstreams=%d | aliases=%d | api_keys=%d",
            path,
            len(cfg.upstreams),
            len(cfg.aliases),
            len(cfg.auth.api_keys),
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

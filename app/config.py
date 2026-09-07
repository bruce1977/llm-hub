"""
Configuration loading module.

Reads the gateway configuration from /data/config.json and provides model routing and alias resolution.
Supports hot reload (checks the file mtime at most once per second).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger("llm_hub")

CONFIG_PATH = os.getenv("CONFIG_PATH", "/data/config.json")


#: Environment variables that provide API keys without writing them into config.json
ENV_API_KEYS = "GATEWAY_API_KEYS"
ENV_API_KEYS_FILE = "GATEWAY_API_KEYS_FILE"


def collect_env_api_keys() -> list[str]:
    """Collect API keys from the environment.

    Two sources are supported (they may be combined):
      - GATEWAY_API_KEYS      inline value, comma/newline/space separated
      - GATEWAY_API_KEYS_FILE path to a file holding the keys (Docker/Kubernetes secrets
                              are mounted as files, which keeps them out of `docker inspect`)
    """
    collected: list[str] = []

    inline = os.getenv(ENV_API_KEYS, "").strip()
    if inline:
        collected.extend(p.strip() for p in re.split(r"[\s,]+", inline) if p.strip())

    key_file = os.getenv(ENV_API_KEYS_FILE, "").strip()
    if key_file:
        try:
            content = Path(key_file).read_text(encoding="utf-8")
            collected.extend(p.strip() for p in re.split(r"[\s,]+", content) if p.strip())
            logger.info("Loaded API key(s) from %s", key_file)
        except OSError as exc:
            logger.error("Failed to read %s=%s: %s", ENV_API_KEYS_FILE, key_file, exc)

    return collected


def merge_env_api_keys(keys: list[str]) -> list[str]:
    """Merge API keys coming from the environment into the configured keys, preserving order."""
    extras = collect_env_api_keys()
    if not extras:
        return keys
    merged = list(keys)
    added = 0
    for k in extras:
        if k not in merged:
            merged.append(k)
            added += 1
    if added:
        logger.info("Loaded %d API key(s) from the environment", added)
    return merged


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
    enabled: bool = True
    api_keys: list[str] = Field(default_factory=list)
    allow_anonymous_health: bool = True


class RoutingConfig(BaseModel):
    """Routing policy. When strict=True, models not declared on any upstream return 404."""

    strict: bool = False


class UpstreamConfig(BaseModel):
    """A single backend instance: either Ollama (default) or LM Studio.

    `models` supports exact names or prefix wildcards (e.g. qwen3:*).
    `model_aliases` maps a short exposed name to the real upstream id, which is handy
    for verbose LM Studio ids such as "qwen/qwen3.5-9b".
    """

    name: str
    base_url: str
    # Backend flavor: "ollama" (native API) or "lmstudio" (OpenAI-compatible API)
    type: Literal["ollama", "lmstudio"] = "ollama"
    models: list[str] = Field(default_factory=list)
    # Short exposed name -> real upstream id, e.g. {"qwen3.5:9b": "qwen/qwen3.5-9b"}
    model_aliases: dict[str, str] = Field(default_factory=dict)
    timeout: float | None = None
    description: str = ""
    # Extra headers attached when forwarding (optional, e.g. when the upstream has its own auth)
    headers: dict[str, str] = Field(default_factory=dict)
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
    mode: str = "logprobs"  # logprobs (generative scoring) | embedding (vector cosine)
    models: list[str] = Field(default_factory=list)
    instruction: str = "Given a web search query, retrieve relevant passages that answer the query"
    template: str = DEFAULT_RERANK_TEMPLATE
    yes_token: str = "yes"
    no_token: str = "no"
    temperature: float = 0.0
    top_logprobs: int = 20
    max_tokens: int = 1
    normalize: bool = True
    max_concurrency: int = 8
    return_documents: bool = False
    # Per-model mode override, e.g. {"bge-m3:latest": "embedding"}
    model_modes: dict[str, str] = Field(default_factory=dict)

    def mode_for(self, model: str) -> str:
        return self.model_modes.get(model, self.mode)


class SecurityConfig(BaseModel):
    """Public-facing hardening. All Ollama model-management endpoints are blocked by default.

    admin_endpoints:
      "deny"     (default) - block pull/push/create/delete/copy and blob up/downloads
      "readonly" - block delete/copy/push/create and blobs (still allow `pull` to fetch models)
      "allow"    - forward everything (only for trusted internal networks)
    """

    admin_endpoints: Literal["deny", "readonly", "allow"] = "deny"
    blocked_paths: list[str] = Field(default_factory=list)
    allow_docs: bool = False
    cors_origins: list[str] = Field(default_factory=list)
    max_body_bytes: int = 32 * 1024 * 1024
    allow_query_api_key: bool = False
    expose_health_details: bool = False
    fail_on_weak_keys: bool = True
    allowed_client_ips: list[str] = Field(default_factory=list)

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
    rerank: RerankConfig = Field(default_factory=RerankConfig)
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
            # Deliberately empty: provide keys through the GATEWAY_API_KEYS env var
            # (or GATEWAY_API_KEYS_FILE) so secrets are never written to config.json.
            "enabled": True,
            "api_keys": [],
            "allow_anonymous_health": True,
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
            "instruction": ("Given a web search query, retrieve relevant passages that answer the query"),
            "yes_token": "yes",
            "no_token": "no",
            "temperature": 0.0,
            "top_logprobs": 20,
            "max_tokens": 1,
            "normalize": True,
            "max_concurrency": 8,
            "return_documents": False,
            "model_modes": {"bge-m3:latest": "embedding"},
        },
    }


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
    def _load(self) -> Config:
        if not self.path.exists():
            logger.warning("Config file %s not found, generating a default template", self.path)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(
                    json.dumps(build_default_config(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                logger.info("Default config written to %s (edit api_keys and upstreams as needed)", self.path)
            except Exception as exc:  # pragma: no cover
                logger.error("Failed to write the default config: %s", exc)
            return Config(**build_default_config())

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse config %s: %s, keeping the previous config", self.path, exc)
            return self._config
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to read the config: %s", exc)
            return self._config

        try:
            cfg = Config(**raw)
        except Exception as exc:
            logger.error("Config validation failed: %s, keeping the previous config", exc)
            return self._config

        # Merge keys from the environment so secrets do not have to live in config.json
        cfg.auth.api_keys = merge_env_api_keys(cfg.auth.api_keys)

        logger.info(
            "Config loaded: %s | upstreams=%d | aliases=%d | api_keys=%d",
            self.path,
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
                mtime = self.path.stat().st_mtime
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
                self._mtime = self.path.stat().st_mtime
            except OSError:
                self._mtime = 0.0
            return self._config

    def snapshot(self) -> tuple[Config, float]:
        """Returns (config, current mtime) so a request sees a consistent view."""
        cfg = self.config
        return cfg, self._mtime

"""Log formatting shared by every entrypoint (``serve.py``, ``main.py``, the Windows launcher).

uvicorn installs its own logging configuration when it starts (``uvicorn.config.LOGGING_CONFIG``),
which replaces whatever ``logging.basicConfig()`` set up. Its stock formatters carry **no
timestamp**, so a burst of access lines looks like::

    INFO:     167.104.161.192:33932 - "GET /v1/models HTTP/1.1" 404 Not Found

That is impossible to line up against a restart, a config change or a scan window after the fact.
``app/logging.json`` is the single source of truth for the format and is consumed three ways:

  * ``serve.py`` passes the loaded dict to ``uvicorn.Config(log_config=...)``;
  * ``main.py``'s ``setup_logging()`` reuses ``LOG_FORMAT`` / ``DATE_FORMAT`` for its
    ``basicConfig`` so application log lines match byte-for-byte;
  * ``scripts/start-local.ps1`` passes ``--log-config logging.json`` to the uvicorn CLI
    (auto-reload path), where a file path is the only thing uvicorn accepts.

Handlers stay on stderr for server/app lines and stdout for access lines, exactly as uvicorn
ships them, so existing log collectors keep working.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

#: Used by ``main.py``'s basicConfig. Must match the ``fmt`` values in logging.json.
LOG_FORMAT = "%(asctime)s.%(msecs)03d | %(levelname)-7s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

LOG_CONFIG_PATH = Path(__file__).with_name("logging.json")

#: uvicorn hard-codes ``uvicorn.error`` as the logger for *all* server lifecycle
#: messages, including harmless INFO lines like "Application startup complete."
#: ``%(name)s`` therefore prints "uvicorn.error" on a line that is not an error at
#: all, which reads like a failure in the console. These aliases rewrite only the
#: name shown in the log line; the real logger identity, level, handler routing and
#: ``--log-level`` are untouched. Application logs keep their own ``llm_hub`` name.
LOG_NAME_ALIASES: dict[str, str] = {
    "uvicorn": "gateway",
    "uvicorn.error": "gateway",
    "uvicorn.access": "gateway.access",
}


class LoggerNameAliasFilter(logging.Filter):
    """Rewrite ``record.name`` for display only (the ``%(name)s`` log column).

    dictConfig instantiates this from ``logging.json`` for the two entrypoints that
    let uvicorn build the handlers (``serve.py`` and the CLI's ``--log-config``,
    including the auto-reload parent). ``apply_to_existing_handlers`` attaches the
    same filter for the bare ``uvicorn main:app`` path, where uvicorn installed its
    stock handlers before the app's lifespan ran. ``filter`` is idempotent: a second
    pass sees the already-aliased name and leaves it alone.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.name = LOG_NAME_ALIASES.get(record.name, record.name)
        return True


def uvicorn_log_config(level: str = "info") -> dict[str, Any]:
    """Return logging.json with every level pinned to ``server.log_level``.

    uvicorn would otherwise apply ``--log-level`` to ``uvicorn.error``/``uvicorn.access`` only,
    leaving root and ``llm_hub`` at the file's default. Doing it here keeps one knob
    (``server.log_level``) authoritative for the whole process.
    """
    config: dict[str, Any] = json.loads(LOG_CONFIG_PATH.read_text(encoding="utf-8"))
    name = str(level or "info").upper()
    for logger_config in config.get("loggers", {}).values():
        logger_config["level"] = name
    config.get("root", {})["level"] = name
    return config


def apply_to_existing_handlers(level: str = "info") -> None:
    """Re-format handlers uvicorn has ALREADY installed (the ``uvicorn main:app`` path).

    Only the formatters are replaced - handler class, stream and logger levels stay exactly as
    uvicorn set them, so stdout/stderr routing and ``--log-level`` are untouched. In the
    ``serve.py`` path this is a harmless no-op: ``logging.json`` already supplied these
    formatters. It matters when uvicorn's stock LOGGING_CONFIG ran instead, because then every
    access line would print without a timestamp.
    """
    try:
        from uvicorn.logging import AccessFormatter, DefaultFormatter
    except ImportError:  # pragma: no cover - uvicorn is a hard dependency
        return

    config = uvicorn_log_config(level)
    formatters = config["formatters"]
    default_fmt = formatters["default"]
    access_fmt = formatters["access"]
    for logger_name, formatter_cls, spec in (
        ("uvicorn", DefaultFormatter, default_fmt),
        ("uvicorn.error", DefaultFormatter, default_fmt),
        ("uvicorn.access", AccessFormatter, access_fmt),
    ):
        for handler in logging.getLogger(logger_name).handlers:
            handler.setFormatter(
                formatter_cls(fmt=spec["fmt"], datefmt=spec["datefmt"], use_colors=False)
            )
            # The bare `uvicorn main:app` path never loads logging.json, so its
            # handlers would otherwise keep printing "uvicorn.error". Attach the
            # same display-name filter the JSON path installs. Guard by type so a
            # reload (or an explicit logging.json handler) cannot stack duplicates.
            if not any(isinstance(f, LoggerNameAliasFilter) for f in handler.filters):
                handler.addFilter(LoggerNameAliasFilter())

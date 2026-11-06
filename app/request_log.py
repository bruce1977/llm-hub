"""
Request log for the web management console.

One row per proxied request in a local SQLite database (WAL mode), written through a
bounded in-memory queue: the forwarding path never waits for a disk write. A single
background task batches the queued rows and flushes them with ``executemany``.

The ``requests`` table holds the full detail of a call -- when it arrived (``ts``) and
finished (``ts_end``), how long it took (``duration_ms`` / ``ttfb_ms``), the request
parameters (``method`` / ``path`` / ``query`` / ``request_body``, plus ``requested_model``
as the caller wrote it and ``model`` after alias resolution), and the result (``status``
/ ``response_body`` / ``error`` / ``tokens_in`` / ``tokens_out`` / ``tokens`` / ``req_size``
/ ``res_size`` / ``client_ip``).
Writing is switched on and off by ``config.yaml`` -> ``request_log.enabled``; the flag is
read per request, so hot reload turns recording on or off without a restart.

Storage defaults to ``llmhub.db`` next to the active ``config.yaml`` (so the Docker
layout puts it at ``/data/llmhub.db``); override with ``REQUEST_LOG_DB``. Rows older
than ``REQUEST_LOG_RETENTION_DAYS`` (default 30, 0 = keep forever) are pruned hourly.

Bodies are truncated to 8 KB and obvious credentials (``api_key``, ``token``,
``password``, ...) are masked before anything touches disk.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from config import CONFIG_PATH

logger = logging.getLogger("llm_hub")

MAX_BODY_CHARS = 8192
DEFAULT_PAGE_SIZE = 15
MAX_PAGE_SIZE = 200
MAX_QUEUE = 10000
BATCH_SIZE = 100

# What the console counts and lists. The gateway only ever serves `/v1/*` and `/api/*`, so
# every other path is a liveness probe, a `/docs` fetch, a favicon request or bot noise: it is
# still recorded (a 403/404 there can matter), but it must not bend the request counts, the
# percentiles, the error rate or the distribution pies. `get_request` by id stays unscoped, so a
# deep link to one of those rows still opens.
API_PATH_SCOPE = "(path LIKE '/v1/%' OR path LIKE '/api/%')"

# Grouping expressions for /v1/management/stats/distribution. Rows that never reached an
# upstream (blocked, oversized, unrouted) bucket as "-", the same convention as `failures`.
# `status` counts every 4xx as an error, unlike the 5xx-only `failures` view and `error_rate`:
# the pie answers "did the caller get what it asked for", not "did the gateway break".
_DISTRIBUTION_KEYS = {
    "upstream": "COALESCE(upstream, '-')",
    "model": "COALESCE(model, '-')",
    "status": "CASE WHEN status >= 400 THEN 'error' ELSE 'ok' END",
}

_SENSITIVE_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "x-api-key",
    "password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    model TEXT,
    upstream TEXT,
    status INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    ttfb_ms INTEGER,
    stream INTEGER NOT NULL DEFAULT 0,
    tokens_in INTEGER,
    tokens_out INTEGER,
    tokens INTEGER,
    req_size INTEGER,
    res_size INTEGER,
    client_ip TEXT,
    error TEXT,
    request_body TEXT,
    response_body TEXT,
    request_body_truncated INTEGER NOT NULL DEFAULT 0,
    response_body_truncated INTEGER NOT NULL DEFAULT 0,
    ts_end TEXT,
    requested_model TEXT,
    query TEXT
);
CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests(ts);
CREATE INDEX IF NOT EXISTS idx_requests_model ON requests(model);
CREATE INDEX IF NOT EXISTS idx_requests_upstream ON requests(upstream);
CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status);
"""

_COLUMNS = (
    "ts, method, path, model, upstream, status, duration_ms, ttfb_ms, stream, "
    "tokens_in, tokens_out, tokens, req_size, res_size, client_ip, error, request_body, response_body, "
    "request_body_truncated, response_body_truncated, ts_end, requested_model, query"
)

# Columns appended after the first release; added with ALTER TABLE on older databases.
_ADDED_COLUMNS = (
    ("ts_end", "TEXT"),
    ("requested_model", "TEXT"),
    ("query", "TEXT"),
    ("tokens_in", "INTEGER"),
    ("tokens_out", "INTEGER"),
)


# --------------------------------------------------------------------------- #
# Helpers shared with the proxy
# --------------------------------------------------------------------------- #
def now_ts() -> str:
    """UTC timestamp without an offset suffix, so string comparison equals time order."""
    return datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")


def normalize_ts(value: str | None) -> str | None:
    """Parse an ISO8601 filter value into the same naive-UTC form used by ``ts``."""
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"Invalid timestamp {value!r}: expected ISO8601.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="seconds")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("***" if str(k).lower() in _SENSITIVE_KEYS else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def sanitize_body(raw: bytes | None) -> tuple[str | None, bool]:
    """Return (text, truncated) for storage: 8 KB cap, credentials masked."""
    if not raw:
        return None, False
    truncated = len(raw) > MAX_BODY_CHARS
    data = raw[:MAX_BODY_CHARS]
    text = data.decode("utf-8", errors="replace")
    if not truncated:
        try:
            text = json.dumps(_redact(json.loads(data)), ensure_ascii=False)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    return text, truncated


def _as_int(value: Any) -> int | None:
    """Token counts arrive as integers, but some providers send numeric strings."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def extract_token_usage(raw: bytes | None) -> tuple[int | None, int | None, int | None]:
    """Return (input, output, total) token usage from an OpenAI-compatible or Ollama body.

    ``None`` in either direction means the response never reported it, which is
    deliberately different from a real ``0``; ``total`` prefers the provider's own
    figure, because it can exceed input + output when it counts reasoning tokens."""
    if not raw:
        return None, None, None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, None, None
    if not isinstance(data, dict):
        return None, None, None

    usage = data.get("usage")
    if isinstance(usage, dict):
        prompt = _as_int(usage.get("prompt_tokens"))
        completion = _as_int(usage.get("completion_tokens"))
        if prompt is not None or completion is not None:
            total = _as_int(usage.get("total_tokens"))
            if total is None:
                total = (prompt or 0) + (completion or 0)
            return prompt, completion, total

    prompt = _as_int(data.get("prompt_eval_count"))
    completion = _as_int(data.get("eval_count"))
    if prompt is not None or completion is not None:
        return prompt, completion, (prompt or 0) + (completion or 0)
    return None, None, None


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #
class RequestLog:
    def __init__(self, db_path: str | Path | None = None, retention_days: int | None = None):
        self.db_path = Path(db_path or os.getenv("REQUEST_LOG_DB") or Path(CONFIG_PATH).parent / "llmhub.db")
        if retention_days is None:
            try:
                retention_days = int(os.getenv("REQUEST_LOG_RETENTION_DAYS", "30"))
            except ValueError:
                retention_days = 30
        self.retention_days = max(retention_days, 0)
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=MAX_QUEUE)
        self._writer: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._started = False
        self.dropped = 0
        self._last_prune = 0.0

    # --------------------------------------------------------------- lifecycle ---
    async def start(self) -> None:
        if self._started:
            return
        await asyncio.to_thread(self._init_db)
        self._started = True
        self._writer = asyncio.create_task(self._write_loop())
        logger.info(
            "Request log ready: %s (retention=%s days); writes follow config request_log.enabled",
            self.db_path,
            self.retention_days or "unlimited",
        )

    async def stop(self) -> None:
        if not self._started:
            return
        try:
            await asyncio.wait_for(self._queue.join(), timeout=5.0)
        except TimeoutError:
            logger.warning("Request log queue did not drain before shutdown; dropping pending rows")
        self._stop.set()
        if self._writer is not None:
            try:
                await asyncio.wait_for(self._writer, timeout=5.0)
            except (TimeoutError, asyncio.CancelledError):
                self._writer.cancel()
        self._started = False

    async def flush(self) -> None:
        """Wait until everything queued so far has been written (used before reads)."""
        if not self._started:
            return
        try:
            await asyncio.wait_for(self._queue.join(), timeout=5.0)
        except TimeoutError:
            logger.warning("Request log flush timed out")

    def record(self, entry: dict[str, Any]) -> None:
        """Queue one row; never blocks and never raises."""
        if not self._started:
            return
        try:
            self._queue.put_nowait(entry)
        except asyncio.QueueFull:
            self.dropped += 1
            if self.dropped % 100 == 1:
                logger.warning("Request log queue full; %d row(s) dropped", self.dropped)

    # ------------------------------------------------------------------ writer ---
    async def _write_loop(self) -> None:
        while True:
            batch: list[dict[str, Any]] = []
            try:
                first = await asyncio.wait_for(self._queue.get(), timeout=0.5)
                batch.append(first)
                while len(batch) < BATCH_SIZE:
                    try:
                        batch.append(self._queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
            except TimeoutError:
                if self._stop.is_set():
                    return
                await self._maybe_prune()
                continue

            try:
                await asyncio.to_thread(self._insert_many, batch)
            except Exception as exc:  # never let a bad row kill the writer
                logger.error("Failed to write %d request log row(s): %s", len(batch), exc)
            finally:
                for _ in batch:
                    self._queue.task_done()
            await self._maybe_prune()

    async def _maybe_prune(self) -> None:
        if not self.retention_days:
            return
        now = time.time()
        if now - self._last_prune < 3600:
            return
        self._last_prune = now
        cutoff = (datetime.now(UTC).replace(tzinfo=None) - timedelta(days=self.retention_days)).isoformat(
            timespec="seconds"
        )
        try:
            removed = await asyncio.to_thread(self._prune, cutoff)
            if removed:
                logger.info("Request log pruned %d row(s) older than %s", removed, cutoff)
        except Exception as exc:
            logger.warning("Request log prune failed: %s", exc)

    # -------------------------------------------------------------------- sqlite ---
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _db(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(SCHEMA)
            present = {row["name"] for row in conn.execute("PRAGMA table_info(requests)")}
            for column, column_type in _ADDED_COLUMNS:
                if column not in present:
                    conn.execute(f"ALTER TABLE requests ADD COLUMN {column} {column_type}")

    def _insert_many(self, batch: list[dict[str, Any]]) -> None:
        rows = [tuple(entry.get(col) for col in _COLUMNS.split(", ")) for entry in batch]
        placeholders = ", ".join("?" * len(rows[0]))
        with self._db() as conn:
            conn.executemany(f"INSERT INTO requests ({_COLUMNS}) VALUES ({placeholders})", rows)

    def _prune(self, cutoff: str) -> int:
        with self._db() as conn:
            cur = conn.execute("DELETE FROM requests WHERE ts < ?", (cutoff,))
            return cur.rowcount

    # -------------------------------------------------------------------- queries ---
    @staticmethod
    def _where(
        start: str | None,
        end: str | None,
        upstream: str | None,
        model: str | None,
        status: str | None,
        path: str | None,
        method: str | None = None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = [API_PATH_SCOPE]
        params: list[Any] = []
        if start:
            clauses.append("ts >= ?")
            params.append(start)
        if end:
            clauses.append("ts <= ?")
            params.append(end)
        if upstream:
            clauses.append("upstream = ?")
            params.append(upstream)
        if model:
            # Match both the resolved real model id and the alias the client actually sent,
            # so a `model_aliases` rename and its real target are filtered as one group.
            clauses.append("(model = ? OR requested_model = ?)")
            params.append(model)
            params.append(model)
        if path:
            clauses.append("path LIKE ?")
            params.append(f"%{path}%")
        if method:
            clauses.append("method = ?")
            params.append(method)
        if status:
            ranges = {"2xx": (200, 299), "3xx": (300, 399), "4xx": (400, 499), "5xx": (500, 599)}
            low, high = ranges[status]
            clauses.append("status BETWEEN ? AND ?")
            params.extend([low, high])
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    @staticmethod
    def _percentile(conn: sqlite3.Connection, where: str, params: list[Any], count: int, pct: float) -> int:
        if count <= 0:
            return 0
        rank = max(1, math.ceil(count * pct / 100.0))
        row = conn.execute(
            f"SELECT duration_ms FROM requests{where} ORDER BY duration_ms LIMIT 1 OFFSET ?",
            (*params, rank - 1),
        ).fetchone()
        return int(row[0]) if row else 0

    async def query_requests(
        self,
        *,
        start: str | None = None,
        end: str | None = None,
        upstream: str | None = None,
        model: str | None = None,
        status: str | None = None,
        path: str | None = None,
        method: str | None = None,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._query_requests_sync,
            start=start,
            end=end,
            upstream=upstream,
            model=model,
            status=status,
            path=path,
            method=method,
            page=page,
            page_size=page_size,
        )

    def _query_requests_sync(
        self,
        *,
        start: str | None,
        end: str | None,
        upstream: str | None,
        model: str | None,
        status: str | None,
        path: str | None,
        method: str | None = None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        where, params = self._where(start, end, upstream, model, status, path, method)
        error_clause = " AND " if where else " WHERE "
        with self._db() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM requests{where}", params).fetchone()[0]
            errors = conn.execute(
                f"SELECT COUNT(*) FROM requests{where}{error_clause}status >= 500", params
            ).fetchone()[0]
            stats = {
                "count": total,
                "p50_ms": self._percentile(conn, where, params, total, 50),
                "p95_ms": self._percentile(conn, where, params, total, 95),
                "p99_ms": self._percentile(conn, where, params, total, 99),
                "error_rate": round(errors / total, 6) if total else 0.0,
            }
            offset = (page - 1) * page_size
            rows = conn.execute(
                f"SELECT id, ts, ts_end, method, path, query, requested_model, model, upstream, "
                f"status, duration_ms, ttfb_ms, stream, tokens_in, tokens_out, tokens "
                f"FROM requests{where} ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
                (*params, page_size, offset),
            ).fetchall()
        return {
            "stats": stats,
            "rows": [dict(row) for row in rows],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "pages": math.ceil(total / page_size) if total else 0,
            },
        }

    async def get_request(self, record_id: int) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_request_sync, record_id)

    def _get_request_sync(self, record_id: int) -> dict[str, Any] | None:
        with self._db() as conn:
            row = conn.execute("SELECT * FROM requests WHERE id = ?", (record_id,)).fetchone()
        return dict(row) if row else None

    async def timeseries(self, *, start: str, end: str, bucket: str = "hour") -> dict[str, Any]:
        return await asyncio.to_thread(self._timeseries_sync, start, end, bucket)

    def _timeseries_sync(self, start: str, end: str, bucket: str) -> dict[str, Any]:
        fmt = "%Y-%m-%dT%H:00" if bucket == "hour" else "%Y-%m-%d"
        sql = f"""
            WITH base AS (
                SELECT strftime('{fmt}', ts) AS bucket, duration_ms,
                       CASE WHEN status >= 500 THEN 1 ELSE 0 END AS is_err
                FROM requests WHERE ts >= ? AND ts <= ? AND {API_PATH_SCOPE}
            ), ranked AS (
                SELECT bucket, duration_ms, is_err,
                       COUNT(*) OVER (PARTITION BY bucket) AS cnt,
                       ROW_NUMBER() OVER (PARTITION BY bucket ORDER BY duration_ms) AS rn
                FROM base
            )
            SELECT bucket, cnt AS count, SUM(is_err) AS errors,
                   MAX(CASE WHEN rn = MAX(1, MIN(cnt, CAST(cnt * 0.95 + 0.999 AS INTEGER)))
                            THEN duration_ms END) AS p95
            FROM ranked GROUP BY bucket ORDER BY bucket
        """
        with self._db() as conn:
            rows = conn.execute(sql, (start, end)).fetchall()
        return {
            "bucket": bucket,
            "start": start,
            "end": end,
            "points": [
                {"ts": row["bucket"], "count": row["count"], "errors": row["errors"] or 0, "p95": row["p95"] or 0}
                for row in rows
            ],
        }

    async def distribution(self, *, start: str, end: str, group_by: str = "upstream") -> dict[str, Any]:
        return await asyncio.to_thread(self._distribution_sync, start, end, group_by)

    def _distribution_sync(self, start: str, end: str, group_by: str) -> dict[str, Any]:
        # The three Dashboard pies. Keys come from a fixed table, never from the caller,
        # so the interpolated expression cannot be injected.
        expr = _DISTRIBUTION_KEYS[group_by]
        sql = (
            f"SELECT {expr} AS key, COUNT(*) AS c FROM requests "
            f"WHERE ts >= ? AND ts <= ? AND {API_PATH_SCOPE} GROUP BY key ORDER BY c DESC"
        )
        with self._db() as conn:
            rows = conn.execute(sql, (start, end)).fetchall()
        return {
            "group_by": group_by,
            "start": start,
            "end": end,
            "items": [{"key": row["key"], "count": row["c"]} for row in rows],
        }

    async def failures(self, *, start: str, end: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._failures_sync, start, end)

    def _failures_sync(self, start: str, end: str) -> dict[str, Any]:
        where = f" WHERE ts >= ? AND ts <= ? AND {API_PATH_SCOPE}"
        params = (start, end)
        error_where = f"{where} AND status >= 500"
        with self._db() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM requests{where}", params).fetchone()[0]
            errors = conn.execute(f"SELECT COUNT(*) FROM requests{error_where}", params).fetchone()[0]
            by_code = {
                str(row["status"]): row["c"]
                for row in conn.execute(
                    f"SELECT status, COUNT(*) AS c FROM requests{error_where} GROUP BY status ORDER BY c DESC",
                    params,
                )
            }
            by_upstream = {
                (row["upstream"] or "-"): row["c"]
                for row in conn.execute(
                    f"SELECT upstream, COUNT(*) AS c FROM requests{error_where} GROUP BY upstream ORDER BY c DESC",
                    params,
                )
            }
            samples = conn.execute(
                f"SELECT ts, path, model, upstream, status, duration_ms FROM requests{error_where} "
                "ORDER BY ts DESC, id DESC LIMIT 20",
                params,
            ).fetchall()
        return {
            "total": total,
            "ok": total - errors,
            "error_rate": round(errors / total, 6) if total else 0.0,
            "by_code": by_code,
            "by_upstream": by_upstream,
            "samples": [dict(row) for row in samples],
        }

    async def today_summary(self) -> dict[str, Any]:
        start = (
            datetime.now(UTC)
            .replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
            .isoformat(timespec="seconds")
        )
        end = now_ts()
        return await asyncio.to_thread(self._today_summary_sync, start, end)

    def _today_summary_sync(self, start: str, end: str) -> dict[str, Any]:
        where, params = self._where(start, end, None, None, None, None)
        error_clause = " AND " if where else " WHERE "
        # Streaming rows carry no usage yet, so fall back to the split when the total is absent.
        token_sum = (
            "COALESCE(SUM(COALESCE(tokens, COALESCE(tokens_in, 0) + COALESCE(tokens_out, 0))), 0)"
        )
        with self._db() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM requests{where}", params).fetchone()[0]
            errors = conn.execute(
                f"SELECT COUNT(*) FROM requests{where}{error_clause}status >= 500", params
            ).fetchone()[0]
            p95 = self._percentile(conn, where, params, total, 95)
            row = conn.execute(f"SELECT {token_sum} FROM requests{where}", params).fetchone()
        return {
            "requests_today": total,
            "tokens_today": int(row[0] or 0),
            "p95_ms": p95,
            "error_rate": round(errors / total, 6) if total else 0.0,
        }

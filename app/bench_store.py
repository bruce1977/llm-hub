"""
SQLite storage for benchmark results (the management console's speed-test feature).

Lives in the same ``llmhub.db`` file as the request log (so there is one data file per
deployment). Each ``(upstream, model)`` keeps only its **latest 3** records -- a rolling
window that matches the console's hover popover, which shows recent history without
growing the database unbounded.

Writing goes through a bounded in-memory queue like the request log: the bench job never
blocks on a disk write. A single background task flushes the queue in batches.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config import CONFIG_PATH

logger = logging.getLogger("llm_hub")

KEEP_PER_KEY = 3  # latest N records per (upstream, model)
MAX_QUEUE = 1000
BATCH_SIZE = 50

SCHEMA = """
CREATE TABLE IF NOT EXISTS bench_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upstream TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL,
    server TEXT,
    host TEXT,
    port INTEGER,
    rounds INTEGER,
    warmup INTEGER,
    max_tokens INTEGER,
    decode_tok_s REAL,
    decode_sd REAL,
    prefill_tok_s REAL,
    ttft_ms REAL,
    timing_source TEXT,
    prompt_tokens INTEGER,
    gen_tokens INTEGER,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bench_upstream_model ON bench_results(upstream, model, id DESC);
"""


def now_ts() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")


class BenchStore:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or os.getenv("LLMHUB_DB") or Path(CONFIG_PATH).parent / "llmhub.db")
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=MAX_QUEUE)
        self._writer: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = False
        self.dropped = 0

    # --------------------------------------------------------------- lifecycle ---
    async def start(self) -> None:
        if self._started:
            return
        await asyncio.to_thread(self._init_db)
        self._loop = asyncio.get_running_loop()
        self._started = True
        self._writer = asyncio.create_task(self._write_loop())
        logger.info("Bench store ready: %s (keep=%s per upstream/model)", self.db_path, KEEP_PER_KEY)

    async def stop(self) -> None:
        if not self._started:
            return
        try:
            await asyncio.wait_for(self._queue.join(), timeout=5.0)
        except TimeoutError:
            logger.warning("Bench store queue did not drain before shutdown")
        self._stop.set()
        if self._writer is not None:
            try:
                await asyncio.wait_for(self._writer, timeout=5.0)
            except (TimeoutError, asyncio.CancelledError):
                self._writer.cancel()
        self._started = False

    def record(self, entry: dict[str, Any]) -> None:
        """Queue one result row. Safe to call from another thread (the bench worker)."""
        if not self._started or self._loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            self._enqueue(entry)
        else:
            # The benchmark runs on its own loop in a worker thread; the queue (and its
            # writer) belong to the application loop, so hand the row over thread-safely.
            self._loop.call_soon_threadsafe(self._enqueue, entry)

    def _enqueue(self, entry: dict[str, Any]) -> None:
        try:
            self._queue.put_nowait(entry)
        except asyncio.QueueFull:
            self.dropped += 1
            if self.dropped % 50 == 1:
                logger.warning("Bench store queue full; %d row(s) dropped", self.dropped)

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
                continue
            try:
                await asyncio.to_thread(self._insert_many, batch)
            except Exception as exc:  # never let a bad row kill the writer
                logger.error("Failed to write %d bench row(s): %s", len(batch), exc)
            finally:
                for _ in batch:
                    self._queue.task_done()

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

    def _insert_many(self, batch: list[dict[str, Any]]) -> None:
        with self._db() as conn:
            for entry in batch:
                cur = conn.execute(
                    """
                    INSERT INTO bench_results (
                        upstream, model, created_at, server, host, port, rounds, warmup,
                        max_tokens, decode_tok_s, decode_sd, prefill_tok_s, ttft_ms,
                        timing_source, prompt_tokens, gen_tokens, payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry["upstream"],
                        entry["model"],
                        entry["created_at"],
                        entry.get("server"),
                        entry.get("host"),
                        entry.get("port"),
                        entry.get("rounds"),
                        entry.get("warmup"),
                        entry.get("max_tokens"),
                        entry.get("decode_tok_s"),
                        entry.get("decode_sd"),
                        entry.get("prefill_tok_s"),
                        entry.get("ttft_ms"),
                        entry.get("timing_source"),
                        entry.get("prompt_tokens"),
                        entry.get("gen_tokens"),
                        entry["payload"],
                    ),
                )
                # Keep only the latest KEEP_PER_KEY rows for this (upstream, model).
                conn.execute(
                    """
                    DELETE FROM bench_results
                    WHERE upstream = ? AND model = ?
                      AND id NOT IN (
                        SELECT id FROM bench_results
                        WHERE upstream = ? AND model = ?
                        ORDER BY created_at DESC, id DESC
                        LIMIT ?
                      )
                    """,
                    (entry["upstream"], entry["model"], entry["upstream"], entry["model"], KEEP_PER_KEY),
                )

    # -------------------------------------------------------------------- read ---
    async def flush(self) -> None:
        """Wait until every queued row is on disk.

        `record()` is fire-and-forget, so a client that polls `history` right after a job
        reports "done" could read a stale table. The management route flushes first.
        """
        await self._queue.join()

    async def history(self, upstream: str, model: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._history_sync, upstream, model)

    def _history_sync(self, upstream: str, model: str) -> list[dict[str, Any]]:
        with self._db() as conn:
            rows = conn.execute(
                """
                SELECT id, upstream, model, created_at, server, host, port, rounds, warmup,
                       max_tokens, decode_tok_s, decode_sd, prefill_tok_s, ttft_ms,
                       timing_source, prompt_tokens, gen_tokens, payload
                FROM bench_results
                WHERE upstream = ? AND model = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (upstream, model, KEEP_PER_KEY),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            rec = dict(row)
            try:
                rec["payload"] = json.loads(rec["payload"])
            except (json.JSONDecodeError, TypeError):
                rec["payload"] = None
            out.append(rec)
        return out

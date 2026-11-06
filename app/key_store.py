"""
Gateway API keys, kept in ``llmhub.db`` as hashes.

The console mints and revokes keys at runtime, so a key is a row rather than a line in a
secret file: ``api_keys`` carries one row per key and nothing else. No row holds the secret --
only ``sha256(<plaintext>)`` plus a display mask -- so a stolen database cannot be replayed and
the plaintext is not recoverable from it. The plaintext exists only in the response of the call
that created it.

Why SHA-256 rather than bcrypt/argon2: a generated key is 238 bits of randomness, so there is no
passphrase left to brute-force, and the gateway verifies a key on **every** client request. A slow
KDF would tax every call by tens of milliseconds and turn the endpoint into a DoS amplifier while
buying nothing against a secret nobody can guess.

Keys used to come only from ``GATEWAY_API_KEYS`` / ``GATEWAY_API_KEYS_FILE`` (``data/.keys``).
They are still honoured: the first start against an empty table imports them as
``source="file"``, and from then on the table is the only source of truth -- the file is never
re-read, so a key revoked in the console stays revoked, and editing the file changes nothing
(startup says so out loud).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import secrets
import sqlite3
import string
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config import CONFIG_PATH

logger = logging.getLogger("llm_hub")

#: Everything a caller sends starts with this, so a leaked-looking string is recognisable in
#: logs and in a client config. It is not a secret -- the 40 random characters after it are.
KEY_PREFIX = "sk-hub-"
SECRET_BODY = 40  # base62 characters after the prefix: ~238 bits
KEY_LENGTH = len(KEY_PREFIX) + SECRET_BODY  # 47, always
_ALPHABET = string.ascii_letters + string.digits

SOURCE_CONSOLE = "console"
SOURCE_FILE = "file"

LABEL_MAX_CHARS = 100

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hash TEXT NOT NULL UNIQUE,
    masked TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'console',
    created_at TEXT NOT NULL
);
"""


def hash_key(secret: str) -> str:
    """The stored form of a key: sha256 of its UTF-8 bytes, lowercase hex."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def generate_key() -> str:
    """A new random key: ``sk-hub-`` plus 40 base62 characters, 47 in total."""
    return KEY_PREFIX + "".join(secrets.choice(_ALPHABET) for _ in range(SECRET_BODY))


def mask_key(secret: str) -> str:
    """A display form that identifies a key without disclosing it.

    Generated keys keep their prefix and 4+4 characters of the body. Imported keys have any
    shape (an old ``sk-...`` from a file), so they fall back to head-and-tail of the whole
    string, and a short one is covered completely.
    """
    if secret.startswith(KEY_PREFIX) and len(secret) > len(KEY_PREFIX) + 8:
        body = secret[len(KEY_PREFIX) :]
        return f"{KEY_PREFIX}{body[:4]}…{body[-4:]}"
    if len(secret) <= 12:
        return "•" * len(secret)
    return f"{secret[:8]}…{secret[-4:]}"


def now_ts() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")


class ApiKeyStore:
    """Hashed gateway keys, with the verification set cached in memory.

    The request path only ever touches ``verify()``, a dict lookup against a frozenset of
    hashes, so no client call waits on sqlite. Every write refreshes that cache, which is why
    a created key works immediately and a deleted one stops working immediately.
    """

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or os.getenv("LLMHUB_DB") or Path(CONFIG_PATH).parent / "llmhub.db")
        self._hashes: frozenset[str] = frozenset()
        self._started = False

    # ------------------------------------------------------------- lifecycle ---
    async def start(self) -> None:
        if self._started:
            return
        await asyncio.to_thread(self._init_db)
        await asyncio.to_thread(self._load_cache)
        self._started = True
        logger.info("API key store ready: %s (%d key(s))", self.db_path, len(self._hashes))

    # ------------------------------------------------------ request-path check ---
    def verify(self, candidate: str) -> bool:
        """True when ``candidate`` hashes to a stored key. Never touches the database."""
        if not candidate:
            return False
        return hash_key(candidate) in self._hashes

    def count(self) -> int:
        return len(self._hashes)

    # ------------------------------------------------------------ management ---
    async def list_keys(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_sync)

    async def create(self, label: str) -> tuple[dict[str, Any], str]:
        """Add a key and return ``(row, plaintext)`` -- the plaintext is never stored."""
        label = _clean_label(label)
        secret = generate_key()
        row = await asyncio.to_thread(self._insert_sync, secret, label, SOURCE_CONSOLE)
        await asyncio.to_thread(self._load_cache)
        return row, secret

    async def delete(self, key_id: int) -> bool:
        removed = await asyncio.to_thread(self._delete_sync, key_id)
        await asyncio.to_thread(self._load_cache)
        return removed

    async def import_plaintext(self, keys: list[str], *, source: str = SOURCE_FILE) -> int:
        """Seed the table from environment / ``.keys`` values, skipping what is already there."""
        inserted = await asyncio.to_thread(self._import_sync, keys, source)
        await asyncio.to_thread(self._load_cache)
        return inserted

    def unserved_env_keys(self, keys: list[str]) -> list[str]:
        """Environment keys the table does not contain, as masks.

        After the first import the file stops being the source of truth, so these are dead
        credentials the operator probably believes still work -- startup warns about them.
        """
        return [mask_key(k) for k in keys if k and hash_key(k) not in self._hashes]

    # ----------------------------------------------------------------- sqlite ---
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

    def _load_cache(self) -> None:
        with self._db() as conn:
            self._hashes = frozenset(row["hash"] for row in conn.execute("SELECT hash FROM api_keys"))

    def _list_sync(self) -> list[dict[str, Any]]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT id, masked, label, source, created_at FROM api_keys ORDER BY created_at DESC, id DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def _insert_sync(self, secret: str, label: str, source: str) -> dict[str, Any]:
        created_at = now_ts()
        with self._db() as conn:
            cur = conn.execute(
                "INSERT INTO api_keys (hash, masked, label, source, created_at) VALUES (?, ?, ?, ?, ?)",
                (hash_key(secret), mask_key(secret), label, source, created_at),
            )
            row = conn.execute(
                "SELECT id, masked, label, source, created_at FROM api_keys WHERE id = ?",
                (cur.lastrowid,),
            ).fetchone()
        return dict(row)

    def _delete_sync(self, key_id: int) -> bool:
        with self._db() as conn:
            return conn.execute("DELETE FROM api_keys WHERE id = ?", (key_id,)).rowcount > 0

    def _import_sync(self, keys: list[str], source: str) -> int:
        inserted = 0
        with self._db() as conn:
            for secret in keys:
                if not secret or conn.execute(
                    "SELECT 1 FROM api_keys WHERE hash = ?", (hash_key(secret),)
                ).fetchone():
                    continue
                conn.execute(
                    "INSERT INTO api_keys (hash, masked, label, source, created_at) VALUES (?, ?, ?, ?, ?)",
                    # An imported key has no description to carry over, and the console is what
                    # renders one, so the label stays empty and `source="file"` does the telling.
                    (hash_key(secret), mask_key(secret), "", source, now_ts()),
                )
                inserted += 1
        return inserted


def _clean_label(label: str) -> str:
    text = (label or "").strip()
    return text[:LABEL_MAX_CHARS]


# --------------------------------------------------------------------------- #
# Process-wide handle: the auth dependency is a plain function, and every client request
# needs the verification set without threading the store through each caller.
# --------------------------------------------------------------------------- #
_store: ApiKeyStore | None = None


def set_store(store: ApiKeyStore | None) -> None:
    global _store
    _store = store


def get_store() -> ApiKeyStore | None:
    return _store

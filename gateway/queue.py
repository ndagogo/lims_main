"""SQLite durable outbound queue with retry/backoff and idempotency keys."""
import json
import sqlite3
import time
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent / 'gateway_queue.sqlite3'


class GatewayQueue:
    def __init__(self, db_path=None):
        self.db_path = Path(db_path or DEFAULT_DB)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self):
        return sqlite3.connect(str(self.db_path))

    def _init(self):
        with self._conn() as conn:
            conn.execute(
                '''CREATE TABLE IF NOT EXISTS queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT UNIQUE NOT NULL,
                    payload TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL,
                    last_error TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at REAL NOT NULL
                )'''
            )

    def enqueue(self, idempotency_key: str, payload: dict) -> bool:
        try:
            with self._conn() as conn:
                conn.execute(
                    'INSERT INTO queue (idempotency_key, payload, next_attempt_at, created_at) VALUES (?, ?, ?, ?)',
                    (idempotency_key, json.dumps(payload), time.time(), time.time()),
                )
            return True
        except sqlite3.IntegrityError:
            return False  # duplicate idempotency key

    def due_items(self, limit=20):
        now = time.time()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, idempotency_key, payload, attempts FROM queue WHERE status='pending' AND next_attempt_at<=? ORDER BY id LIMIT ?",
                (now, limit),
            ).fetchall()
        return [
            {'id': r[0], 'idempotency_key': r[1], 'payload': json.loads(r[2]), 'attempts': r[3]}
            for r in rows
        ]

    def mark_done(self, item_id):
        with self._conn() as conn:
            conn.execute("UPDATE queue SET status='done' WHERE id=?", (item_id,))

    def mark_failed(self, item_id, error, *, max_attempts=8):
        with self._conn() as conn:
            row = conn.execute('SELECT attempts FROM queue WHERE id=?', (item_id,)).fetchone()
            attempts = (row[0] if row else 0) + 1
            if attempts >= max_attempts:
                conn.execute(
                    "UPDATE queue SET status='failed', attempts=?, last_error=? WHERE id=?",
                    (attempts, str(error)[:500], item_id),
                )
            else:
                backoff = min(3600, 2 ** attempts)
                conn.execute(
                    "UPDATE queue SET attempts=?, last_error=?, next_attempt_at=? WHERE id=?",
                    (attempts, str(error)[:500], time.time() + backoff, item_id),
                )

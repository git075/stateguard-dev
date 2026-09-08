"""SQLite Audit Store for StateGuard.

Provides a persistent, append-only event log backed by a local SQLite database
in WAL (Write-Ahead Logging) mode. A single-writer asyncio queue prevents
SQLITE_BUSY errors during concurrent writes from multiple agents.

For CLI and synchronous reads, a separate sync read path is provided.
"""

import asyncio
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("stateguard.store")

# ── Event Types ────────────────────────────────────────────────────────────────

class EventType(str, Enum):
    COMMIT         = "commit"
    ROLLBACK       = "rollback"
    INVARIANT_WARN = "invariant_warn"
    INVARIANT_BLOCK= "invariant_block"
    COMPENSATION   = "compensation"
    SAGA_START     = "saga_start"
    SAGA_END       = "saga_end"


# ── Audit Event Dataclass ──────────────────────────────────────────────────────

@dataclass
class AuditEvent:
    """Represents a single recorded state-transition event."""

    event_type: EventType
    saga_id: str                              = "—"
    step_name: str                            = "—"
    rule_name: str                            = "—"
    state_before: Optional[Dict[str, Any]]   = None
    state_after: Optional[Dict[str, Any]]    = None
    message: str                              = ""
    timestamp: datetime                       = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def to_row(self) -> tuple:
        return (
            self.timestamp.isoformat(),
            self.event_type.value,
            self.saga_id,
            self.step_name,
            self.rule_name,
            _dumps(self.state_before),
            _dumps(self.state_after),
            self.message,
        )

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "AuditEvent":
        return cls(
            timestamp    = datetime.fromisoformat(row["timestamp"]),
            event_type   = EventType(row["event_type"]),
            saga_id      = row["saga_id"],
            step_name    = row["step_name"],
            rule_name    = row["rule_name"],
            state_before = _loads(row["state_before"]),
            state_after  = _loads(row["state_after"]),
            message      = row["message"],
        )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _dumps(obj: Any) -> Optional[str]:
    if obj is None:
        return None
    try:
        return json.dumps(obj, default=str)
    except (TypeError, ValueError):
        return str(obj)


def _loads(text: Optional[str]) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS stateguard_audit (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT    NOT NULL,
    event_type   TEXT    NOT NULL,
    saga_id      TEXT    NOT NULL DEFAULT '—',
    step_name    TEXT    NOT NULL DEFAULT '—',
    rule_name    TEXT    NOT NULL DEFAULT '—',
    state_before TEXT,
    state_after  TEXT,
    message      TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sg_saga    ON stateguard_audit(saga_id);
CREATE INDEX IF NOT EXISTS idx_sg_type    ON stateguard_audit(event_type);
CREATE INDEX IF NOT EXISTS idx_sg_ts      ON stateguard_audit(timestamp);
"""

_INSERT = """
INSERT INTO stateguard_audit
    (timestamp, event_type, saga_id, step_name, rule_name,
     state_before, state_after, message)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""


# ── Async Audit Store ──────────────────────────────────────────────────────────

class AuditStore:
    """Async, single-writer SQLite audit store.

    Usage (async context manager)::

        async with AuditStore("./stateguard.db") as store:
            await store.log(AuditEvent(event_type=EventType.COMMIT, saga_id="s1"))

    Or manual lifecycle::

        store = AuditStore("./stateguard.db")
        await store.start()
        await store.log(...)
        await store.stop()
    """

    def __init__(self, db_path: str = ".stateguard/audit.db") -> None:
        self._path = Path(db_path)
        self._queue: asyncio.Queue[Optional[AuditEvent]] = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None  # type: ignore[type-arg]
        self._started = False

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Create the database file, apply schema, and start the writer loop."""
        if self._started:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Schema is applied synchronously on startup — safe before writer loop.
        conn = sqlite3.connect(str(self._path))
        conn.executescript(_SCHEMA)
        conn.commit()
        conn.close()

        self._task = asyncio.create_task(self._writer_loop(), name="stateguard-writer")
        self._started = True
        logger.debug(f"[STORE] Audit store started at {self._path}")

    async def stop(self) -> None:
        """Drain the queue and shut down the writer loop cleanly."""
        if not self._started:
            return
        await self._queue.put(None)           # Sentinel to stop the loop
        if self._task:
            await self._task
        self._started = False
        logger.debug("[STORE] Audit store stopped.")

    async def __aenter__(self) -> "AuditStore":
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.stop()

    # ── Write API ──────────────────────────────────────────────────────────────

    async def log(self, event: AuditEvent) -> None:
        """Enqueue an audit event for writing. Returns immediately (non-blocking)."""
        if not self._started:
            raise RuntimeError("AuditStore not started. Call await store.start() first.")
        await self._queue.put(event)

    # Convenience helpers
    async def log_commit(self, saga_id: str, step_name: str,
                         state_before: Dict, state_after: Dict) -> None:
        await self.log(AuditEvent(EventType.COMMIT, saga_id=saga_id,
                                  step_name=step_name,
                                  state_before=state_before, state_after=state_after))

    async def log_rollback(self, saga_id: str, reason: str = "") -> None:
        await self.log(AuditEvent(EventType.ROLLBACK, saga_id=saga_id, message=reason))

    async def log_warn(self, saga_id: str, rule_name: str,
                       state: Dict, message: str = "") -> None:
        await self.log(AuditEvent(EventType.INVARIANT_WARN, saga_id=saga_id,
                                  rule_name=rule_name, state_before=state,
                                  message=message))

    async def log_block(self, saga_id: str, rule_name: str,
                        state: Dict, message: str = "") -> None:
        await self.log(AuditEvent(EventType.INVARIANT_BLOCK, saga_id=saga_id,
                                  rule_name=rule_name, state_before=state,
                                  message=message))

    async def log_compensation(self, saga_id: str, step_name: str,
                               message: str = "") -> None:
        await self.log(AuditEvent(EventType.COMPENSATION, saga_id=saga_id,
                                  step_name=step_name, message=message))

    async def log_saga_start(self, saga_id: str) -> None:
        await self.log(AuditEvent(EventType.SAGA_START, saga_id=saga_id))

    async def log_saga_end(self, saga_id: str, success: bool = True) -> None:
        msg = "completed" if success else "failed"
        await self.log(AuditEvent(EventType.SAGA_END, saga_id=saga_id, message=msg))

    # ── Writer Loop (single writer) ────────────────────────────────────────────

    async def _writer_loop(self) -> None:
        """Single background task — the only writer to the SQLite file."""
        import aiosqlite
        async with aiosqlite.connect(str(self._path)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
            while True:
                event = await self._queue.get()
                if event is None:            # Sentinel received — stop
                    self._queue.task_done()
                    break
                try:
                    await db.execute(_INSERT, event.to_row())
                    await db.commit()
                except Exception as e:
                    logger.error(f"[STORE] Write failed: {e}")
                finally:
                    self._queue.task_done()

    # ── Synchronous Read API (for CLI) ─────────────────────────────────────────

    def read_last(self, n: int = 20,
                  event_type: Optional[str] = None,
                  saga_id: Optional[str] = None) -> List[AuditEvent]:
        """Read recent events synchronously (safe to call from CLI / non-async code)."""
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")

        clauses, params = [], []
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if saga_id:
            clauses.append("saga_id = ?")
            params.append(saga_id)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(n)

        rows = conn.execute(
            f"SELECT * FROM stateguard_audit {where} ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
        conn.close()
        return [AuditEvent.from_row(r) for r in reversed(rows)]

    def read_saga(self, saga_id: str) -> List[AuditEvent]:
        """Read all events for a saga, oldest first."""
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM stateguard_audit WHERE saga_id = ? ORDER BY id ASC",
            (saga_id,),
        ).fetchall()
        conn.close()
        return [AuditEvent.from_row(r) for r in rows]

    def list_sagas(self) -> List[str]:
        """Return a list of distinct saga IDs, most recent first."""
        conn = sqlite3.connect(str(self._path))
        rows = conn.execute(
            "SELECT DISTINCT saga_id FROM stateguard_audit "
            "WHERE saga_id != '—' ORDER BY id DESC"
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]

    def export_json(self, saga_id: Optional[str] = None, last: int = 100) -> str:
        """Export events as a JSON string."""
        events = self.read_saga(saga_id) if saga_id else self.read_last(last)
        payload = [
            {
                "timestamp":    e.timestamp.isoformat(),
                "event_type":   e.event_type.value,
                "saga_id":      e.saga_id,
                "step_name":    e.step_name,
                "rule_name":    e.rule_name,
                "state_before": e.state_before,
                "state_after":  e.state_after,
                "message":      e.message,
            }
            for e in events
        ]
        return json.dumps(payload, indent=2, default=str)

"""SQLite persistence layer."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.data.models import Candle
from src.signals.models import Result, Setup, SignalKind
from src.storage.models import ALL_TABLES, INDEXES, MIGRATIONS
from src.utils.logging import get_logger
from src.utils.time import now_utc

logger = get_logger(__name__)


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        for ddl in ALL_TABLES:
            self._conn.execute(ddl)
        for ddl in INDEXES:
            self._conn.execute(ddl)
        self._migrate()
        self._conn.commit()
        logger.info("Database ready at %s", self.path)

    def _migrate(self) -> None:
        """Adds columns introduced after a database was first created."""
        for table, columns in MIGRATIONS.items():
            existing = {
                row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")
            }
            for column, sql_type in columns.items():
                if column not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
                    logger.info("Database migrated: %s.%s added", table, column)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        return self._conn

    # ---------------------------------------------------------------- write
    def save_setup(self, setup: Setup) -> None:
        row = setup.as_row()
        columns = ", ".join(row.keys())
        placeholders = ", ".join(f":{key}" for key in row)
        updates = ", ".join(f"{key}=excluded.{key}" for key in row if key != "setup_id")
        try:
            self.conn.execute(
                f"INSERT INTO setups ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT(setup_id) DO UPDATE SET {updates}",
                row,
            )
            self.conn.commit()
        except sqlite3.Error as exc:
            logger.error("Failed to save setup %s: %s", setup.setup_id, exc)

    def save_signal(self, setup: Setup, kind: SignalKind, detail: str = "") -> None:
        try:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO signals (
                    setup_id, kind, asset, timeframe, pattern, direction,
                    entry_time, entry_price, payout, expiration, expiry_time,
                    result, detail, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    setup.setup_id,
                    kind.value,
                    setup.asset,
                    setup.timeframe,
                    setup.pattern.value,
                    setup.direction.value,
                    setup.entry_confirmation_time.isoformat()
                    if setup.entry_confirmation_time
                    else None,
                    setup.entry_price,
                    setup.payout,
                    setup.expiration,
                    setup.expiry_time.isoformat() if setup.expiry_time else None,
                    setup.result.value,
                    detail,
                    now_utc().isoformat(),
                ),
            )
            self.conn.commit()
        except sqlite3.Error as exc:
            logger.error("Failed to save signal %s/%s: %s", setup.setup_id, kind.value, exc)

    def save_candle(self, candle: Candle) -> None:
        row = candle.as_row()
        try:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO candles
                (asset, timeframe, timestamp, open, high, low, close, volume, source)
                VALUES (:asset, :timeframe, :timestamp, :open, :high, :low, :close,
                        :volume, :source)
                """,
                row,
            )
            self.conn.commit()
        except sqlite3.Error as exc:
            logger.error("Failed to save candle: %s", exc)

    def update_result(self, setup_id: str, result: Result) -> None:
        try:
            self.conn.execute(
                "UPDATE setups SET result=?, updated_at=? WHERE setup_id=?",
                (result.value, now_utc().isoformat(), setup_id),
            )
            self.conn.execute(
                "UPDATE signals SET result=? WHERE setup_id=? AND kind=?",
                (result.value, setup_id, SignalKind.PHASE3_EXECUTION.value),
            )
            self.conn.commit()
        except sqlite3.Error as exc:
            logger.error("Failed to update result for %s: %s", setup_id, exc)

    # ----------------------------------------------------------------- read
    def pending_outcomes(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT setup_id, asset, timeframe, direction, entry_price, expiry_time
                FROM setups
                WHERE status='EXECUTED' AND result='UNKNOWN' AND expiry_time IS NOT NULL
                """
            )
        )

    def stats(self) -> dict:
        row = self.conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status='EXECUTED' THEN 1 ELSE 0 END) AS executed,
                SUM(CASE WHEN status='INVALIDATED' THEN 1 ELSE 0 END) AS invalidated,
                SUM(CASE WHEN status='EXPIRED' THEN 1 ELSE 0 END) AS expired,
                SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) AS losses
            FROM setups
            """
        ).fetchone()
        return dict(row) if row else {}

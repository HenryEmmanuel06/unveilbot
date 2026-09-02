"""SQLite schema (README 58, 59). Portable to PostgreSQL later."""

SETUPS_TABLE = """
CREATE TABLE IF NOT EXISTS setups (
    setup_id TEXT PRIMARY KEY,
    asset TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    pattern TEXT NOT NULL,
    level_1 REAL NOT NULL,
    level_mid REAL NOT NULL,
    level_2 REAL NOT NULL,
    retracement REAL,
    pattern_confirmation_time TEXT,
    pullback_start TEXT,
    pullback_candle_count INTEGER DEFAULT 0,
    reference_price REAL,
    entry_confirmation TEXT,
    entry_price REAL,
    direction TEXT,
    payout INTEGER,
    expiration TEXT,
    expiry_time TEXT,
    result TEXT DEFAULT 'UNKNOWN',
    status TEXT NOT NULL,
    phase TEXT NOT NULL,
    invalidation_reason TEXT,
    source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

SIGNALS_TABLE = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    setup_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    asset TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    pattern TEXT NOT NULL,
    direction TEXT,
    entry_time TEXT,
    entry_price REAL,
    payout INTEGER,
    expiration TEXT,
    expiry_time TEXT,
    result TEXT DEFAULT 'UNKNOWN',
    detail TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (setup_id, kind),
    FOREIGN KEY (setup_id) REFERENCES setups (setup_id)
);
"""

CANDLES_TABLE = """
CREATE TABLE IF NOT EXISTS candles (
    asset TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL,
    source TEXT,
    PRIMARY KEY (asset, timeframe, timestamp)
);
"""

#: Columns added after the first release: applied to existing databases.
MIGRATIONS: dict[str, dict[str, str]] = {
    "setups": {"payout": "INTEGER"},
    "signals": {"payout": "INTEGER"},
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_setups_asset_tf ON setups (asset, timeframe);",
    "CREATE INDEX IF NOT EXISTS idx_signals_setup ON signals (setup_id);",
]

ALL_TABLES = [SETUPS_TABLE, SIGNALS_TABLE, CANDLES_TABLE]

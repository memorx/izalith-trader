"""
Izalith Trading System - SQLite Database
"""
import aiosqlite
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from core.utils.logger import get_logger

logger = get_logger("database")


class Database:
    """Async SQLite database manager."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        """Connect to the database and create tables."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._create_tables()
        logger.info(f"Database connected: {self.db_path}")

    async def disconnect(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("Database disconnected")

    async def _create_tables(self) -> None:
        """Create all required tables."""
        await self._conn.executescript("""
            -- Trades executed
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                module TEXT NOT NULL,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                quantity REAL NOT NULL,
                pnl REAL,
                pnl_pct REAL,
                status TEXT NOT NULL DEFAULT 'open',
                strategy TEXT,
                notes TEXT
            );

            -- Signals generated
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                direction TEXT NOT NULL,
                strength REAL NOT NULL,
                price_at_signal REAL NOT NULL,
                sent_telegram INTEGER DEFAULT 0,
                sent_discord INTEGER DEFAULT 0
            );

            -- DCA orders
            CREATE TABLE IF NOT EXISTS dca_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                amount_usd REAL NOT NULL,
                frequency TEXT NOT NULL,
                next_execution TEXT,
                last_execution TEXT,
                total_invested REAL DEFAULT 0,
                total_quantity REAL DEFAULT 0,
                avg_price REAL DEFAULT 0
            );

            -- Grid levels
            CREATE TABLE IF NOT EXISTS grid_levels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                level_price REAL NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                filled_at TEXT
            );

            -- Portfolio snapshots
            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                total_value_usd REAL NOT NULL,
                cash_usd REAL NOT NULL,
                positions_value_usd REAL NOT NULL,
                unrealized_pnl REAL DEFAULT 0,
                realized_pnl_today REAL DEFAULT 0
            );

            -- Create indexes
            CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
            CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
            CREATE INDEX IF NOT EXISTS idx_signals_timestamp ON signals(timestamp);
            CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
        """)
        await self._conn.commit()

    # Trade methods
    async def insert_trade(
        self,
        module: str,
        symbol: str,
        market: str,
        side: str,
        entry_price: float,
        quantity: float,
        strategy: Optional[str] = None,
        notes: Optional[str] = None
    ) -> int:
        """Insert a new trade."""
        cursor = await self._conn.execute(
            """
            INSERT INTO trades (timestamp, module, symbol, market, side, entry_price, quantity, status, strategy, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
            """,
            (datetime.utcnow().isoformat(), module, symbol, market, side, entry_price, quantity, strategy, notes)
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def close_trade(self, trade_id: int, exit_price: float) -> None:
        """Close a trade with exit price and calculate P/L."""
        trade = await self.get_trade(trade_id)
        if not trade:
            return

        entry_price = trade["entry_price"]
        quantity = trade["quantity"]
        side = trade["side"]

        if side == "buy":
            pnl = (exit_price - entry_price) * quantity
        else:
            pnl = (entry_price - exit_price) * quantity

        pnl_pct = (pnl / (entry_price * quantity)) * 100 if entry_price else 0

        await self._conn.execute(
            """
            UPDATE trades SET exit_price = ?, pnl = ?, pnl_pct = ?, status = 'closed'
            WHERE id = ?
            """,
            (exit_price, pnl, pnl_pct, trade_id)
        )
        await self._conn.commit()

    async def get_trade(self, trade_id: int) -> Optional[Dict[str, Any]]:
        """Get a trade by ID."""
        cursor = await self._conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_open_trades(self, module: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all open trades."""
        if module:
            cursor = await self._conn.execute(
                "SELECT * FROM trades WHERE status = 'open' AND module = ? ORDER BY timestamp DESC",
                (module,)
            )
        else:
            cursor = await self._conn.execute(
                "SELECT * FROM trades WHERE status = 'open' ORDER BY timestamp DESC"
            )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_trades_today(self, module: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all trades from today."""
        today = datetime.utcnow().date().isoformat()
        if module:
            cursor = await self._conn.execute(
                "SELECT * FROM trades WHERE timestamp >= ? AND module = ? ORDER BY timestamp DESC",
                (today, module)
            )
        else:
            cursor = await self._conn.execute(
                "SELECT * FROM trades WHERE timestamp >= ? ORDER BY timestamp DESC",
                (today,)
            )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_recent_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent trades."""
        cursor = await self._conn.execute(
            "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # Signal methods
    async def insert_signal(
        self,
        symbol: str,
        signal_type: str,
        direction: str,
        strength: float,
        price: float
    ) -> int:
        """Insert a new signal."""
        cursor = await self._conn.execute(
            """
            INSERT INTO signals (timestamp, symbol, signal_type, direction, strength, price_at_signal)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (datetime.utcnow().isoformat(), symbol, signal_type, direction, strength, price)
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def mark_signal_sent(self, signal_id: int, channel: str) -> None:
        """Mark a signal as sent to a channel."""
        column = f"sent_{channel}"
        await self._conn.execute(
            f"UPDATE signals SET {column} = 1 WHERE id = ?",
            (signal_id,)
        )
        await self._conn.commit()

    async def get_recent_signals(self, symbol: str, signal_type: str, minutes: int = 15) -> List[Dict[str, Any]]:
        """Get recent signals to avoid duplicates."""
        cursor = await self._conn.execute(
            """
            SELECT * FROM signals
            WHERE symbol = ? AND signal_type = ?
            AND datetime(timestamp) >= datetime('now', ? || ' minutes')
            ORDER BY timestamp DESC
            """,
            (symbol, signal_type, -minutes)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_signals_today(self) -> List[Dict[str, Any]]:
        """Get all signals from today."""
        today = datetime.utcnow().date().isoformat()
        cursor = await self._conn.execute(
            "SELECT * FROM signals WHERE timestamp >= ? ORDER BY timestamp DESC",
            (today,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # DCA methods
    async def get_dca_orders(self) -> List[Dict[str, Any]]:
        """Get all DCA orders."""
        cursor = await self._conn.execute("SELECT * FROM dca_orders ORDER BY symbol")
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def upsert_dca_order(
        self,
        symbol: str,
        amount_usd: float,
        frequency: str,
        next_execution: Optional[str] = None
    ) -> int:
        """Insert or update a DCA order."""
        existing = await self._conn.execute(
            "SELECT id FROM dca_orders WHERE symbol = ?", (symbol,)
        )
        row = await existing.fetchone()

        if row:
            await self._conn.execute(
                """
                UPDATE dca_orders SET amount_usd = ?, frequency = ?, next_execution = ?
                WHERE symbol = ?
                """,
                (amount_usd, frequency, next_execution, symbol)
            )
            await self._conn.commit()
            return row["id"]
        else:
            cursor = await self._conn.execute(
                """
                INSERT INTO dca_orders (symbol, amount_usd, frequency, next_execution)
                VALUES (?, ?, ?, ?)
                """,
                (symbol, amount_usd, frequency, next_execution)
            )
            await self._conn.commit()
            return cursor.lastrowid

    async def update_dca_execution(
        self,
        symbol: str,
        quantity: float,
        price: float,
        next_execution: str
    ) -> None:
        """Update DCA order after execution."""
        await self._conn.execute(
            """
            UPDATE dca_orders
            SET last_execution = ?,
                next_execution = ?,
                total_invested = total_invested + ?,
                total_quantity = total_quantity + ?,
                avg_price = (total_invested + ?) / (total_quantity + ?)
            WHERE symbol = ?
            """,
            (
                datetime.utcnow().isoformat(),
                next_execution,
                quantity * price,
                quantity,
                quantity * price,
                quantity,
                symbol
            )
        )
        await self._conn.commit()

    # Grid methods
    async def get_grid_levels(self, symbol: str) -> List[Dict[str, Any]]:
        """Get all grid levels for a symbol."""
        cursor = await self._conn.execute(
            "SELECT * FROM grid_levels WHERE symbol = ? ORDER BY level_price",
            (symbol,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def insert_grid_level(
        self,
        symbol: str,
        level_price: float,
        side: str,
        quantity: float
    ) -> int:
        """Insert a new grid level."""
        cursor = await self._conn.execute(
            """
            INSERT INTO grid_levels (symbol, level_price, side, quantity, status)
            VALUES (?, ?, ?, ?, 'pending')
            """,
            (symbol, level_price, side, quantity)
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def update_grid_level(self, level_id: int, status: str) -> None:
        """Update grid level status."""
        filled_at = datetime.utcnow().isoformat() if status == "filled" else None
        await self._conn.execute(
            "UPDATE grid_levels SET status = ?, filled_at = ? WHERE id = ?",
            (status, filled_at, level_id)
        )
        await self._conn.commit()

    async def clear_grid_levels(self, symbol: str) -> None:
        """Clear all grid levels for a symbol."""
        await self._conn.execute("DELETE FROM grid_levels WHERE symbol = ?", (symbol,))
        await self._conn.commit()

    # Portfolio methods
    async def insert_portfolio_snapshot(
        self,
        total_value_usd: float,
        cash_usd: float,
        positions_value_usd: float,
        unrealized_pnl: float = 0,
        realized_pnl_today: float = 0
    ) -> int:
        """Insert a portfolio snapshot."""
        cursor = await self._conn.execute(
            """
            INSERT INTO portfolio_snapshots
            (timestamp, total_value_usd, cash_usd, positions_value_usd, unrealized_pnl, realized_pnl_today)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.utcnow().isoformat(),
                total_value_usd,
                cash_usd,
                positions_value_usd,
                unrealized_pnl,
                realized_pnl_today
            )
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def get_portfolio_snapshots(self, hours: int = 24) -> List[Dict[str, Any]]:
        """Get portfolio snapshots from the last N hours."""
        cursor = await self._conn.execute(
            """
            SELECT * FROM portfolio_snapshots
            WHERE datetime(timestamp) >= datetime('now', ? || ' hours')
            ORDER BY timestamp
            """,
            (-hours,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # Stats methods
    async def get_daily_pnl(self) -> float:
        """Get total realized P/L for today."""
        today = datetime.utcnow().date().isoformat()
        cursor = await self._conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) as total FROM trades WHERE timestamp >= ? AND status = 'closed'",
            (today,)
        )
        row = await cursor.fetchone()
        return row["total"] if row else 0

    async def get_trade_count_today(self, module: Optional[str] = None) -> int:
        """Get trade count for today."""
        today = datetime.utcnow().date().isoformat()
        if module:
            cursor = await self._conn.execute(
                "SELECT COUNT(*) as count FROM trades WHERE timestamp >= ? AND module = ?",
                (today, module)
            )
        else:
            cursor = await self._conn.execute(
                "SELECT COUNT(*) as count FROM trades WHERE timestamp >= ?",
                (today,)
            )
        row = await cursor.fetchone()
        return row["count"] if row else 0


# Singleton instance
_database: Optional[Database] = None


async def get_database(db_path: Optional[Path] = None) -> Database:
    """Get or create the database instance."""
    global _database
    if _database is None:
        if db_path is None:
            from config.settings import get_settings
            settings = get_settings()
            db_path = settings.data_dir / "izalith.db"
        _database = Database(db_path)
        await _database.connect()
    return _database

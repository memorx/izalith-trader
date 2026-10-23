"""
Izalith Trading System - SQLite Database

Fuente de verdad del bot: las posiciones abiertas, el P&L realizado y las órdenes
enviadas viven aquí, para que un reinicio no deje posiciones huérfanas ni repita órdenes.

Fechas: todo se guarda en UTC (ISO sin zona). El "día" de los límites diarios es el
de la zona horaria configurada (TRADING_TIMEZONE), no el de UTC.
"""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any
from zoneinfo import ZoneInfo

import aiosqlite

from core.utils.logger import get_logger

logger = get_logger("database")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def normalize_side(side: str) -> str:
    """'buy' abre un largo y 'sell' un corto: en la base sólo se guarda long/short."""
    s = (side or "").lower()
    if s in ("buy", "long"):
        return "long"
    if s in ("sell", "short"):
        return "short"
    raise ValueError(f"Lado desconocido: {side}")


class Database:
    """Async SQLite database manager."""

    # Columnas agregadas después de la primera versión: (tabla, columna, tipo)
    MIGRATIONS = [
        ("trades", "closed_at", "TEXT"),
        ("trades", "fees", "REAL DEFAULT 0"),
        ("trades", "stop_loss", "REAL"),
        ("trades", "take_profit", "REAL"),
        ("trades", "simulated", "INTEGER DEFAULT 0"),
        ("trades", "client_order_id", "TEXT"),
        ("signals", "candle_time", "TEXT"),
        ("signals", "simulated", "INTEGER DEFAULT 0"),
        ("grid_levels", "buy_price", "REAL"),
        ("grid_levels", "client_order_id", "TEXT"),
    ]

    def __init__(self, db_path: Path, tz: str = "America/Mexico_City"):
        self.db_path = db_path
        self.tz = ZoneInfo(tz)
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._create_tables()
        await self._migrate()
        logger.info(f"Database connected: {self.db_path}")

    async def disconnect(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("Database disconnected")

    @property
    def is_connected(self) -> bool:
        return self._conn is not None

    async def _create_tables(self) -> None:
        await self._conn.executescript("""
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

            CREATE TABLE IF NOT EXISTS grid_levels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                level_price REAL NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                filled_at TEXT
            );

            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                total_value_usd REAL NOT NULL,
                cash_usd REAL NOT NULL,
                positions_value_usd REAL NOT NULL,
                unrealized_pnl REAL DEFAULT 0,
                realized_pnl_today REAL DEFAULT 0
            );

            -- Cada orden se anota ANTES de mandarla. Si el proceso muere a medias, al volver
            -- se pregunta al exchange por client_order_id en lugar de mandarla otra vez.
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                module TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL,
                amount REAL,
                client_order_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                exchange_order_id TEXT,
                price REAL,
                filled_quantity REAL,
                fee REAL,
                error TEXT,
                purpose TEXT
            );

            -- Estado pequeño de cada módulo (último rebalanceo, parámetros vigentes...)
            CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            -- Valor de cada estrategia del allocator en el tiempo (para compararla con su backtest)
            CREATE TABLE IF NOT EXISTS sleeve_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                module TEXT NOT NULL,
                equity REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sleeve_module ON sleeve_snapshots(module, timestamp);

            CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
            CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
            CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
            CREATE INDEX IF NOT EXISTS idx_signals_timestamp ON signals(timestamp);
            CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
            CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
        """)
        await self._conn.commit()

    async def _migrate(self) -> None:
        for table, column, ctype in self.MIGRATIONS:
            cursor = await self._conn.execute(f"PRAGMA table_info({table})")
            cols = {row["name"] for row in await cursor.fetchall()}
            if column not in cols:
                await self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ctype}")
                logger.info(f"Migración: {table}.{column}")
        # Versiones viejas guardaban buy/sell como lado de la posición
        await self._conn.execute("UPDATE trades SET side = 'long' WHERE side = 'buy'")
        await self._conn.execute("UPDATE trades SET side = 'short' WHERE side = 'sell'")
        await self._conn.execute(
            "UPDATE trades SET closed_at = timestamp WHERE status = 'closed' AND closed_at IS NULL")
        await self._conn.commit()

    # ── Días en la zona horaria configurada ──

    def day_start_utc(self, days_ago: int = 0) -> str:
        """Inicio del día local (hoy o hace N días) expresado en UTC ISO."""
        local = datetime.now(self.tz).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
        return local.astimezone(timezone.utc).replace(tzinfo=None).isoformat()

    # ── Trades ──

    async def insert_trade(
        self,
        module: str,
        symbol: str,
        market: str,
        side: str,
        entry_price: float,
        quantity: float,
        strategy: Optional[str] = None,
        notes: Optional[str] = None,
        fees: float = 0.0,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        simulated: bool = False,
        client_order_id: Optional[str] = None,
    ) -> int:
        cursor = await self._conn.execute(
            """
            INSERT INTO trades (timestamp, module, symbol, market, side, entry_price, quantity, status,
                                strategy, notes, fees, stop_loss, take_profit, simulated, client_order_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)
            """,
            (utcnow_iso(), module, symbol, market, normalize_side(side), entry_price, quantity,
             strategy, notes, fees, stop_loss, take_profit, int(simulated), client_order_id)
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def update_open_trade(self, trade_id: int, entry_price: float, quantity: float, fees: float,
                                stop_loss: Optional[float] = None, take_profit: Optional[float] = None) -> None:
        await self._conn.execute(
            """UPDATE trades SET entry_price = ?, quantity = ?, fees = ?,
                   stop_loss = COALESCE(?, stop_loss), take_profit = COALESCE(?, take_profit)
               WHERE id = ? AND status = 'open'""",
            (entry_price, quantity, fees, stop_loss, take_profit, trade_id)
        )
        await self._conn.commit()

    async def update_trade_stops(self, trade_id: int, stop_loss: Optional[float], take_profit: Optional[float]) -> None:
        await self._conn.execute("UPDATE trades SET stop_loss = ?, take_profit = ? WHERE id = ?",
                                 (stop_loss, take_profit, trade_id))
        await self._conn.commit()

    async def close_trade(self, trade_id: int, exit_price: float, quantity: Optional[float] = None,
                          exit_fee: float = 0.0) -> Optional[float]:
        """
        Cierra un trade completo o una parte. Regresa el P&L realizado, neto de comisiones.

        En un cierre parcial, la parte cerrada queda como un trade cerrado nuevo y el trade
        original sigue abierto con la cantidad restante y su parte de la comisión de entrada.
        """
        trade = await self.get_trade(trade_id)
        if not trade or trade["status"] != "open":
            return None

        open_qty = float(trade["quantity"])
        qty = open_qty if quantity is None else min(float(quantity), open_qty)
        if qty <= 0:
            return None
        entry = float(trade["entry_price"])
        entry_fees = float(trade.get("fees") or 0.0)
        share = qty / open_qty if open_qty else 1.0
        sign = 1 if normalize_side(trade["side"]) == "long" else -1

        gross = sign * (exit_price - entry) * qty
        fee_part = entry_fees * share + exit_fee
        pnl = gross - fee_part
        pnl_pct = pnl / (entry * qty) * 100 if entry and qty else 0.0
        now = utcnow_iso()

        if qty >= open_qty * (1 - 1e-9):
            await self._conn.execute(
                """UPDATE trades SET exit_price = ?, pnl = ?, pnl_pct = ?, status = 'closed',
                       closed_at = ?, fees = ? WHERE id = ?""",
                (exit_price, pnl, pnl_pct, now, fee_part, trade_id)
            )
        else:
            await self._conn.execute(
                """
                INSERT INTO trades (timestamp, module, symbol, market, side, entry_price, exit_price, quantity,
                                    pnl, pnl_pct, status, strategy, notes, closed_at, fees, simulated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'closed', ?, ?, ?, ?, ?)
                """,
                (trade["timestamp"], trade["module"], trade["symbol"], trade["market"], trade["side"], entry,
                 exit_price, qty, pnl, pnl_pct, trade["strategy"], f"cierre parcial de #{trade_id}", now,
                 fee_part, trade.get("simulated") or 0)
            )
            await self._conn.execute(
                "UPDATE trades SET quantity = ?, fees = ? WHERE id = ?",
                (open_qty - qty, entry_fees * (1 - share), trade_id)
            )
        await self._conn.commit()
        return pnl

    async def get_trade(self, trade_id: int) -> Optional[Dict[str, Any]]:
        cursor = await self._conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_open_trades(self, module: Optional[str] = None) -> List[Dict[str, Any]]:
        if module:
            cursor = await self._conn.execute(
                "SELECT * FROM trades WHERE status = 'open' AND module = ? ORDER BY timestamp", (module,))
        else:
            cursor = await self._conn.execute("SELECT * FROM trades WHERE status = 'open' ORDER BY timestamp")
        return [dict(row) for row in await cursor.fetchall()]

    async def get_trades_today(self, module: Optional[str] = None) -> List[Dict[str, Any]]:
        start = self.day_start_utc()
        if module:
            cursor = await self._conn.execute(
                """SELECT * FROM trades WHERE (timestamp >= ? OR closed_at >= ?) AND module = ?
                   ORDER BY COALESCE(closed_at, timestamp) DESC""", (start, start, module))
        else:
            cursor = await self._conn.execute(
                """SELECT * FROM trades WHERE timestamp >= ? OR closed_at >= ?
                   ORDER BY COALESCE(closed_at, timestamp) DESC""", (start, start))
        return [dict(row) for row in await cursor.fetchall()]

    async def get_recent_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        cursor = await self._conn.execute(
            "SELECT * FROM trades ORDER BY COALESCE(closed_at, timestamp) DESC LIMIT ?", (limit,))
        return [dict(row) for row in await cursor.fetchall()]

    async def get_closed_trades(self, since_iso: Optional[str] = None, module: Optional[str] = None) -> List[Dict[str, Any]]:
        q = "SELECT * FROM trades WHERE status = 'closed'"
        args: list = []
        if since_iso:
            q += " AND closed_at >= ?"
            args.append(since_iso)
        if module:
            q += " AND module = ?"
            args.append(module)
        cursor = await self._conn.execute(q + " ORDER BY closed_at", args)
        return [dict(row) for row in await cursor.fetchall()]

    async def get_realized_pnl_total(self) -> float:
        cursor = await self._conn.execute("SELECT COALESCE(SUM(pnl), 0) AS total FROM trades WHERE status = 'closed'")
        row = await cursor.fetchone()
        return float(row["total"] or 0)

    async def get_daily_pnl(self, module: Optional[str] = None) -> float:
        """P&L realizado (neto) de los trades CERRADOS hoy, en el día local."""
        start = self.day_start_utc()
        if module:
            cursor = await self._conn.execute(
                "SELECT COALESCE(SUM(pnl), 0) AS total FROM trades WHERE status = 'closed' AND closed_at >= ? AND module = ?",
                (start, module))
        else:
            cursor = await self._conn.execute(
                "SELECT COALESCE(SUM(pnl), 0) AS total FROM trades WHERE status = 'closed' AND closed_at >= ?",
                (start,))
        row = await cursor.fetchone()
        return float(row["total"] or 0)

    async def get_trade_count_today(self, module: Optional[str] = None) -> int:
        """Entradas abiertas hoy (día local). Los cierres parciales no cuentan como trades nuevos."""
        start = self.day_start_utc()
        q = "SELECT COUNT(*) AS count FROM trades WHERE timestamp >= ? AND (notes IS NULL OR notes NOT LIKE 'cierre parcial%')"
        args: list = [start]
        if module:
            q += " AND module = ?"
            args.append(module)
        cursor = await self._conn.execute(q, args)
        row = await cursor.fetchone()
        return int(row["count"] or 0)

    # ── Órdenes (bitácora de intención) ──

    async def insert_order_intent(self, module: str, symbol: str, side: str, client_order_id: str,
                                  quantity: Optional[float] = None, amount: Optional[float] = None,
                                  purpose: Optional[str] = None) -> int:
        now = utcnow_iso()
        cursor = await self._conn.execute(
            """INSERT INTO orders (created_at, updated_at, module, symbol, side, quantity, amount,
                                   client_order_id, status, purpose)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (now, now, module, symbol, side, quantity, amount, client_order_id, purpose))
        await self._conn.commit()
        return cursor.lastrowid

    async def update_order(self, client_order_id: str, status: str, exchange_order_id: Optional[str] = None,
                           price: Optional[float] = None, filled_quantity: Optional[float] = None,
                           fee: Optional[float] = None, error: Optional[str] = None) -> None:
        await self._conn.execute(
            """UPDATE orders SET status = ?, updated_at = ?, exchange_order_id = COALESCE(?, exchange_order_id),
                   price = COALESCE(?, price), filled_quantity = COALESCE(?, filled_quantity),
                   fee = COALESCE(?, fee), error = ? WHERE client_order_id = ?""",
            (status, utcnow_iso(), exchange_order_id, price, filled_quantity, fee, error, client_order_id))
        await self._conn.commit()

    async def get_pending_orders(self, module: Optional[str] = None) -> List[Dict[str, Any]]:
        if module:
            cursor = await self._conn.execute(
                "SELECT * FROM orders WHERE status = 'pending' AND module = ? ORDER BY created_at", (module,))
        else:
            cursor = await self._conn.execute("SELECT * FROM orders WHERE status = 'pending' ORDER BY created_at")
        return [dict(row) for row in await cursor.fetchall()]

    async def get_order_by_purpose(self, purpose: str) -> Optional[Dict[str, Any]]:
        """La orden más reciente con ese propósito que no haya fallado (para no repetir un turno de DCA, etc.)."""
        cursor = await self._conn.execute(
            "SELECT * FROM orders WHERE purpose = ? AND status != 'failed' ORDER BY created_at DESC LIMIT 1",
            (purpose,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_recent_orders(self, limit: int = 50) -> List[Dict[str, Any]]:
        cursor = await self._conn.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (limit,))
        return [dict(row) for row in await cursor.fetchall()]

    # ── Estado de módulos ──

    async def kv_get(self, key: str) -> Optional[Any]:
        cursor = await self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return json.loads(row["value"]) if row else None

    async def kv_set(self, key: str, value: Any) -> None:
        await self._conn.execute(
            "INSERT INTO kv (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, json.dumps(value, default=str), utcnow_iso()))
        await self._conn.commit()

    # ── Señales ──

    async def insert_signal(self, symbol: str, signal_type: str, direction: str, strength: float, price: float,
                            candle_time: Optional[str] = None, simulated: bool = False) -> int:
        cursor = await self._conn.execute(
            """INSERT INTO signals (timestamp, symbol, signal_type, direction, strength, price_at_signal,
                                    candle_time, simulated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (utcnow_iso(), symbol, signal_type, direction, strength, price, candle_time, int(simulated)))
        await self._conn.commit()
        return cursor.lastrowid

    async def mark_signal_sent(self, signal_id: int, channel: str) -> None:
        if channel not in ("telegram", "discord"):
            raise ValueError(f"Canal desconocido: {channel}")
        await self._conn.execute(f"UPDATE signals SET sent_{channel} = 1 WHERE id = ?", (signal_id,))
        await self._conn.commit()

    async def get_recent_signals(self, symbol: str, signal_type: str, minutes: int = 15) -> List[Dict[str, Any]]:
        since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).replace(tzinfo=None).isoformat()
        cursor = await self._conn.execute(
            "SELECT * FROM signals WHERE symbol = ? AND signal_type = ? AND timestamp >= ? ORDER BY timestamp DESC",
            (symbol, signal_type, since))
        return [dict(row) for row in await cursor.fetchall()]

    async def last_signal(self, symbol: str, signal_type: str) -> Optional[Dict[str, Any]]:
        cursor = await self._conn.execute(
            "SELECT * FROM signals WHERE symbol = ? AND signal_type = ? ORDER BY timestamp DESC LIMIT 1",
            (symbol, signal_type))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_signals_today(self) -> List[Dict[str, Any]]:
        cursor = await self._conn.execute(
            "SELECT * FROM signals WHERE timestamp >= ? ORDER BY timestamp DESC", (self.day_start_utc(),))
        return [dict(row) for row in await cursor.fetchall()]

    # ── DCA ──

    async def get_dca_orders(self) -> List[Dict[str, Any]]:
        cursor = await self._conn.execute("SELECT * FROM dca_orders ORDER BY symbol")
        return [dict(row) for row in await cursor.fetchall()]

    async def upsert_dca_order(self, symbol: str, amount_usd: float, frequency: str,
                               next_execution: Optional[str] = None) -> int:
        """Crea la orden o actualiza monto y frecuencia. next_execution sólo se cambia si se pasa."""
        cursor = await self._conn.execute("SELECT id FROM dca_orders WHERE symbol = ?", (symbol,))
        row = await cursor.fetchone()
        if row:
            await self._conn.execute(
                "UPDATE dca_orders SET amount_usd = ?, frequency = ?, next_execution = COALESCE(?, next_execution) "
                "WHERE symbol = ?", (amount_usd, frequency, next_execution, symbol))
            await self._conn.commit()
            return row["id"]
        cursor = await self._conn.execute(
            "INSERT INTO dca_orders (symbol, amount_usd, frequency, next_execution) VALUES (?, ?, ?, ?)",
            (symbol, amount_usd, frequency, next_execution))
        await self._conn.commit()
        return cursor.lastrowid

    async def update_dca_execution(self, symbol: str, quantity: float, cost: float, next_execution: str) -> None:
        """Suma una compra ejecutada: `cost` es lo que realmente se pagó, comisiones incluidas."""
        await self._conn.execute(
            """UPDATE dca_orders
               SET last_execution = ?, next_execution = ?,
                   total_invested = total_invested + ?, total_quantity = total_quantity + ?,
                   avg_price = (total_invested + ?) / NULLIF(total_quantity + ?, 0)
               WHERE symbol = ?""",
            (utcnow_iso(), next_execution, cost, quantity, cost, quantity, symbol))
        await self._conn.commit()

    async def set_dca_next_execution(self, symbol: str, next_execution: str) -> None:
        await self._conn.execute("UPDATE dca_orders SET next_execution = ? WHERE symbol = ?", (next_execution, symbol))
        await self._conn.commit()

    # ── Grid ──

    async def get_grid_levels(self, symbol: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
        if status:
            cursor = await self._conn.execute(
                "SELECT * FROM grid_levels WHERE symbol = ? AND status = ? ORDER BY level_price", (symbol, status))
        else:
            cursor = await self._conn.execute(
                "SELECT * FROM grid_levels WHERE symbol = ? ORDER BY level_price", (symbol,))
        return [dict(row) for row in await cursor.fetchall()]

    async def insert_grid_level(self, symbol: str, level_price: float, side: str, quantity: float,
                                buy_price: Optional[float] = None) -> int:
        cursor = await self._conn.execute(
            "INSERT INTO grid_levels (symbol, level_price, side, quantity, status, buy_price) VALUES (?, ?, ?, ?, 'pending', ?)",
            (symbol, level_price, side, quantity, buy_price))
        await self._conn.commit()
        return cursor.lastrowid

    async def update_grid_level(self, level_id: int, status: str) -> None:
        filled_at = utcnow_iso() if status == "filled" else None
        await self._conn.execute("UPDATE grid_levels SET status = ?, filled_at = ? WHERE id = ?",
                                 (status, filled_at, level_id))
        await self._conn.commit()

    async def purge_filled_grid_levels(self, symbol: str, keep_days: int = 30) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).replace(tzinfo=None).isoformat()
        await self._conn.execute(
            "DELETE FROM grid_levels WHERE symbol = ? AND status = 'filled' AND filled_at < ?", (symbol, cutoff))
        await self._conn.commit()

    async def clear_grid_levels(self, symbol: str) -> None:
        await self._conn.execute("DELETE FROM grid_levels WHERE symbol = ?", (symbol,))
        await self._conn.commit()

    # ── Portafolio ──

    async def insert_portfolio_snapshot(self, total_value_usd: float, cash_usd: float, positions_value_usd: float,
                                        unrealized_pnl: float = 0, realized_pnl_today: float = 0) -> int:
        cursor = await self._conn.execute(
            """INSERT INTO portfolio_snapshots
               (timestamp, total_value_usd, cash_usd, positions_value_usd, unrealized_pnl, realized_pnl_today)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (utcnow_iso(), total_value_usd, cash_usd, positions_value_usd, unrealized_pnl, realized_pnl_today))
        await self._conn.commit()
        return cursor.lastrowid

    async def insert_sleeve_snapshot(self, module: str, equity: float) -> None:
        await self._conn.execute("INSERT INTO sleeve_snapshots (timestamp, module, equity) VALUES (?, ?, ?)",
                                 (utcnow_iso(), module, equity))
        await self._conn.commit()

    async def get_sleeve_snapshots(self, module: str) -> List[Dict[str, Any]]:
        cursor = await self._conn.execute(
            "SELECT timestamp, equity FROM sleeve_snapshots WHERE module = ? ORDER BY timestamp", (module,))
        return [dict(row) for row in await cursor.fetchall()]

    async def get_portfolio_snapshots(self, hours: int = 24) -> List[Dict[str, Any]]:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(tzinfo=None).isoformat()
        cursor = await self._conn.execute(
            "SELECT * FROM portfolio_snapshots WHERE timestamp >= ? ORDER BY timestamp", (since,))
        return [dict(row) for row in await cursor.fetchall()]


_database: Optional[Database] = None


async def get_database(db_path: Optional[Path] = None, tz: Optional[str] = None) -> Database:
    """Get or create the database instance."""
    global _database
    if _database is None or not _database.is_connected:
        from config.settings import get_settings
        settings = get_settings()
        if db_path is None:
            db_path = settings.data_dir / "izalith.db"
        _database = Database(db_path, tz or settings.trading_timezone)
        await _database.connect()
    return _database

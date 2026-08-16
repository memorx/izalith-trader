"""
Izalith Trading System - Rich Logger
"""
import logging
import sys
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

# Module colors
MODULE_COLORS = {
    "signals": "cyan",
    "dca": "green",
    "grid": "yellow",
    "scalper": "magenta",
    "dashboard": "blue",
    "core": "white",
    "crypto": "bright_yellow",
    "forex": "bright_green",
    "stocks": "bright_blue",
    "database": "dim white",
    "feed": "white",
    "portfolio": "bright_white",
    "main": "bold white",
    "risk": "yellow",
    "telegram": "cyan",
    "webhook": "blue",
}

# Custom theme
THEME = Theme({
    "info": "dim cyan",
    "warning": "yellow",
    "error": "bold red",
    "critical": "bold white on red",
    "success": "bold green",
    "module.signals": "cyan",
    "module.dca": "green",
    "module.grid": "yellow",
    "module.scalper": "magenta",
    "module.dashboard": "blue",
})

# Check if we're on Windows and need to handle encoding
IS_WINDOWS = sys.platform == "win32"

# Use ASCII-safe indicators for Windows compatibility
if IS_WINDOWS:
    BULLISH_INDICATOR = "[+]"
    BEARISH_INDICATOR = "[-]"
else:
    BULLISH_INDICATOR = "🟢"
    BEARISH_INDICATOR = "🔴"

# Global console with force_terminal for better Windows support
console = Console(
    theme=THEME,
    force_terminal=True,
    color_system="auto"
)


class IzalithFormatter(logging.Formatter):
    """Custom formatter with module colors."""

    def format(self, record: logging.LogRecord) -> str:
        # Extract module name from logger name
        module = record.name.split(".")[-1] if "." in record.name else record.name
        color = MODULE_COLORS.get(module.lower(), "white")

        # Format timestamp
        timestamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")

        # Format level
        level = record.levelname[:4]

        # Build message
        return f"[dim]{timestamp}[/] [{color}]{module:>10}[/] | {record.getMessage()}"


class IzalithHandler(RichHandler):
    """Custom Rich handler for Izalith."""

    def __init__(self, **kwargs):
        super().__init__(
            console=console,
            show_time=False,
            show_level=True,
            show_path=False,
            markup=True,
            rich_tracebacks=True,
            **kwargs
        )


def setup_logging(
    level: str = "INFO",
    log_dir: Optional[Path] = None,
    log_to_file: bool = True
) -> None:
    """Setup logging for the application."""
    # Get root logger
    root_logger = logging.getLogger("izalith")
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Clear existing handlers
    root_logger.handlers.clear()

    # Console handler with Rich
    console_handler = IzalithHandler()
    console_handler.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)

    # File handler
    if log_to_file and log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"izalith_{datetime.now().strftime('%Y%m%d')}.log"

        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            "%(asctime)s | %(name)s | %(levelname)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)

    # Don't propagate to root logger
    root_logger.propagate = False


def get_logger(name: str) -> logging.Logger:
    """Get a logger for a specific module."""
    return logging.getLogger(f"izalith.{name}")


def log_startup_banner():
    """Display startup banner."""
    console.print()
    console.print("=" * 60, style="bold cyan")
    console.print("        IZALITH TRADING SYSTEM", style="bold white")
    console.print("=" * 60, style="bold cyan")
    console.print()


def log_module_status(module: str, status: str, details: str = ""):
    """Log module status with color."""
    color = MODULE_COLORS.get(module.lower(), "white")
    status_color = "green" if status == "OK" else "red" if status == "ERROR" else "yellow"
    msg = f"[{color}]{module:>12}[/] [{status_color}]{status:>6}[/]"
    if details:
        msg += f" - {details}"
    console.print(msg)


def log_trade(module: str, symbol: str, side: str, price: float, qty: float, pnl: Optional[float] = None):
    """Log a trade execution."""
    color = MODULE_COLORS.get(module.lower(), "white")
    side_color = "green" if side.lower() == "buy" else "red"

    msg = f"[{color}]{module:>10}[/] | [{side_color}]{side.upper():>4}[/] {symbol} @ ${price:,.2f} x {qty:.6f}"
    if pnl is not None:
        pnl_color = "green" if pnl >= 0 else "red"
        msg += f" | P/L: [{pnl_color}]${pnl:+,.2f}[/]"

    console.print(msg)


def log_signal(symbol: str, signal_type: str, direction: str, strength: float, price: float):
    """Log a detected signal."""
    dir_color = "green" if direction == "bullish" else "red"
    indicator = BULLISH_INDICATOR if direction == "bullish" else BEARISH_INDICATOR

    # Format price based on magnitude
    if price >= 1000:
        price_str = f"${price:,.2f}"
    elif price >= 1:
        price_str = f"${price:.4f}"
    else:
        price_str = f"${price:.6f}"

    console.print(
        f"[cyan]{'signals':>10}[/] | {indicator} [{dir_color}]{direction.upper():>7}[/] "
        f"{symbol} - {signal_type} (strength: {strength:.0%}) @ {price_str}"
    )


def log_price_update(prices: dict):
    """Log price updates in a table format."""
    from rich.table import Table

    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("Asset", style="cyan")
    table.add_column("Price", justify="right")
    table.add_column("Change", justify="right")
    table.add_column("Spread", justify="right")

    for symbol, data in prices.items():
        change_color = "green" if data.get("change_24h_pct", 0) >= 0 else "red"
        table.add_row(
            symbol,
            f"${data['last']:,.2f}",
            f"[{change_color}]{data.get('change_24h_pct', 0):+.2f}%[/]",
            f"{data.get('spread_pct', 0):.3f}%"
        )

    console.print(table)

"""
Izalith Trading System - Configuration Settings
"""
import os
from dataclasses import dataclass, field
from typing import List, Optional
from pathlib import Path
from dotenv import load_dotenv


@dataclass
class Settings:
    """Main configuration settings for the trading system."""

    # General
    dry_run: bool = True
    log_level: str = "INFO"
    active_modules: List[str] = field(default_factory=lambda: ["signals", "dashboard"])

    # Crypto (Binance)
    binance_api_key: str = ""
    binance_secret: str = ""
    binance_testnet: bool = True

    # Forex (OANDA)
    oanda_account_id: str = ""
    oanda_api_key: str = ""
    oanda_practice: bool = True

    # Stocks (Alpaca)
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_paper: bool = True

    # Signals Module
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    discord_webhook_url: str = ""
    signal_types: List[str] = field(default_factory=lambda: ["rsi", "macd", "breakout", "volume_spike"])

    # DCA Module
    dca_enabled_assets: List[str] = field(default_factory=lambda: ["BTC/USDT", "ETH/USDT", "VOO"])
    dca_amount_usd: float = 50.0
    dca_frequency: str = "weekly"
    dca_day_of_week: str = "monday"
    dca_hour: int = 9

    # Grid Module
    grid_enabled_assets: List[str] = field(default_factory=lambda: ["USDT/USDC"])
    grid_total_investment: float = 1000.0
    grid_levels: int = 10
    grid_range_low: float = 0.995
    grid_range_high: float = 1.005

    # Scalper Module
    scalper_enabled_assets: List[str] = field(default_factory=lambda: ["BTC/USDT", "ETH/USDT", "EUR_USD", "GBP_USD"])
    scalper_strategies: List[str] = field(default_factory=lambda: ["momentum", "mean_reversion"])
    scalper_max_position_usd: float = 500.0
    scalper_profit_target_pct: float = 0.5
    scalper_stop_loss_pct: float = 0.3
    scalper_max_daily_trades: int = 20
    scalper_max_daily_loss_usd: float = 100.0

    # Dashboard Module
    dashboard_port: int = 8080
    dashboard_host: str = "0.0.0.0"

    # Paths
    base_dir: Path = field(default_factory=lambda: Path(__file__).parent.parent)
    data_dir: Path = field(default_factory=lambda: Path(__file__).parent.parent / "data")
    logs_dir: Path = field(default_factory=lambda: Path(__file__).parent.parent / "logs")

    @classmethod
    def from_env(cls, env_path: Optional[Path] = None) -> "Settings":
        """Load settings from environment variables."""
        if env_path:
            load_dotenv(env_path)
        else:
            # Try to load from .env in project root
            base_dir = Path(__file__).parent.parent
            env_file = base_dir / ".env"
            if env_file.exists():
                load_dotenv(env_file)

        def get_list(key: str, default: List[str]) -> List[str]:
            val = os.getenv(key, "")
            if not val:
                return default
            return [x.strip() for x in val.split(",") if x.strip()]

        def get_bool(key: str, default: bool) -> bool:
            val = os.getenv(key, "").lower()
            if val in ("true", "1", "yes"):
                return True
            if val in ("false", "0", "no"):
                return False
            return default

        def get_float(key: str, default: float) -> float:
            try:
                return float(os.getenv(key, default))
            except (ValueError, TypeError):
                return default

        def get_int(key: str, default: int) -> int:
            try:
                return int(os.getenv(key, default))
            except (ValueError, TypeError):
                return default

        return cls(
            # General
            dry_run=get_bool("DRY_RUN", True),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            active_modules=get_list("ACTIVE_MODULES", ["signals", "dashboard"]),

            # Crypto
            binance_api_key=os.getenv("BINANCE_API_KEY", ""),
            binance_secret=os.getenv("BINANCE_SECRET", ""),
            binance_testnet=get_bool("BINANCE_TESTNET", True),

            # Forex
            oanda_account_id=os.getenv("OANDA_ACCOUNT_ID", ""),
            oanda_api_key=os.getenv("OANDA_API_KEY", ""),
            oanda_practice=get_bool("OANDA_PRACTICE", True),

            # Stocks
            alpaca_api_key=os.getenv("ALPACA_API_KEY", ""),
            alpaca_secret_key=os.getenv("ALPACA_SECRET_KEY", ""),
            alpaca_paper=get_bool("ALPACA_PAPER", True),

            # Signals
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", ""),
            signal_types=get_list("SIGNAL_TYPES", ["rsi", "macd", "breakout", "volume_spike"]),

            # DCA
            dca_enabled_assets=get_list("DCA_ENABLED_ASSETS", ["BTC/USDT", "ETH/USDT", "VOO"]),
            dca_amount_usd=get_float("DCA_AMOUNT_USD", 50.0),
            dca_frequency=os.getenv("DCA_FREQUENCY", "weekly"),
            dca_day_of_week=os.getenv("DCA_DAY_OF_WEEK", "monday"),
            dca_hour=get_int("DCA_HOUR", 9),

            # Grid
            grid_enabled_assets=get_list("GRID_ENABLED_ASSETS", ["USDT/USDC"]),
            grid_total_investment=get_float("GRID_TOTAL_INVESTMENT", 1000.0),
            grid_levels=get_int("GRID_LEVELS", 10),
            grid_range_low=get_float("GRID_RANGE_LOW", 0.995),
            grid_range_high=get_float("GRID_RANGE_HIGH", 1.005),

            # Scalper
            scalper_enabled_assets=get_list("SCALPER_ENABLED_ASSETS", ["BTC/USDT", "ETH/USDT", "EUR_USD", "GBP_USD"]),
            scalper_strategies=get_list("SCALPER_STRATEGIES", ["momentum", "mean_reversion"]),
            scalper_max_position_usd=get_float("SCALPER_MAX_POSITION_USD", 500.0),
            scalper_profit_target_pct=get_float("SCALPER_PROFIT_TARGET_PCT", 0.5),
            scalper_stop_loss_pct=get_float("SCALPER_STOP_LOSS_PCT", 0.3),
            scalper_max_daily_trades=get_int("SCALPER_MAX_DAILY_TRADES", 20),
            scalper_max_daily_loss_usd=get_float("SCALPER_MAX_DAILY_LOSS_USD", 100.0),

            # Dashboard
            dashboard_port=get_int("DASHBOARD_PORT", 8080),
            dashboard_host=os.getenv("DASHBOARD_HOST", "0.0.0.0"),
        )

    def is_module_active(self, module: str) -> bool:
        """Check if a module is active."""
        return module.lower() in [m.lower() for m in self.active_modules]


# Singleton instance
_settings: Optional[Settings] = None


def get_settings(reload: bool = False) -> Settings:
    """Get the global settings instance."""
    global _settings
    if _settings is None or reload:
        _settings = Settings.from_env()
    return _settings

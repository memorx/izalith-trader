"""
Izalith Trading System - Webhook Notifier
"""
import aiohttp
from typing import Optional, Dict
from datetime import datetime

from .detector import Signal
from core.utils.logger import get_logger

logger = get_logger("webhook")


class WebhookNotifier:
    """Sends trading signals to a webhook endpoint (Discord, Slack, etc.)."""

    def __init__(self, url: str):
        self.url = url
        self._session: Optional[aiohttp.ClientSession] = None
        self.enabled = bool(url)

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    def _format_discord_embed(self, signal: Signal) -> Dict:
        """Format signal as Discord embed."""
        color = 0x00FF00 if signal.direction == "bullish" else 0xFF0000

        # Format price
        if signal.price >= 1000:
            price_str = f"${signal.price:,.2f}"
        elif signal.price >= 1:
            price_str = f"${signal.price:.4f}"
        else:
            price_str = f"${signal.price:.6f}"

        embed = {
            "title": f"{'🟢' if signal.direction == 'bullish' else '🔴'} {signal.direction.upper()} - {signal.symbol}",
            "description": signal.message,
            "color": color,
            "fields": [
                {"name": "Signal Type", "value": signal.signal_type.replace("_", " ").title(), "inline": True},
                {"name": "Price", "value": price_str, "inline": True},
                {"name": "Strength", "value": f"{signal.strength:.0%}", "inline": True}
            ],
            "footer": {"text": "Izalith Trading System"},
            "timestamp": signal.timestamp.isoformat()
        }

        if signal.indicator_value is not None:
            embed["fields"].append({
                "name": "Indicator Value",
                "value": f"{signal.indicator_value:.4f}",
                "inline": True
            })

        return embed

    async def send(self, signal: Signal) -> bool:
        """Send a signal to the webhook."""
        if not self.enabled:
            logger.debug("Webhook not configured, skipping signal")
            return False

        try:
            session = await self._get_session()

            # Detect webhook type and format accordingly
            if "discord" in self.url.lower():
                payload = {
                    "embeds": [self._format_discord_embed(signal)]
                }
            else:
                # Generic JSON payload
                payload = {
                    "signal": signal.to_dict(),
                    "source": "izalith-trading-system"
                }

            async with session.post(self.url, json=payload) as resp:
                if resp.status in [200, 204]:
                    logger.info(f"Signal sent to webhook: {signal.symbol} {signal.signal_type}")
                    return True
                else:
                    error = await resp.text()
                    logger.error(f"Webhook error {resp.status}: {error}")
                    return False

        except Exception as e:
            logger.error(f"Failed to send webhook: {e}")
            return False

    async def send_raw(self, data: Dict) -> bool:
        """Send raw data to the webhook."""
        if not self.enabled:
            return False

        try:
            session = await self._get_session()

            async with session.post(self.url, json=data) as resp:
                return resp.status in [200, 204]

        except Exception as e:
            logger.error(f"Failed to send raw webhook: {e}")
            return False

    async def test_connection(self) -> bool:
        """Test the webhook connection."""
        if not self.enabled:
            logger.warning("Webhook not configured")
            return False

        try:
            session = await self._get_session()

            # Send a test message
            if "discord" in self.url.lower():
                payload = {
                    "embeds": [{
                        "title": "🔌 Connection Test",
                        "description": "Izalith Trading System webhook is configured correctly!",
                        "color": 0x00FF00,
                        "timestamp": datetime.utcnow().isoformat()
                    }]
                }
            else:
                payload = {
                    "type": "test",
                    "message": "Izalith Trading System webhook connection test",
                    "timestamp": datetime.utcnow().isoformat()
                }

            async with session.post(self.url, json=payload) as resp:
                if resp.status in [200, 204]:
                    logger.info("Webhook connection test successful")
                    return True
                else:
                    logger.error(f"Webhook connection test failed: {resp.status}")
                    return False

        except Exception as e:
            logger.error(f"Webhook connection test failed: {e}")
            return False

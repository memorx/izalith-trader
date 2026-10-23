"""
Izalith Trading System - Reportes y alarmas

- Resumen diario por Telegram a la hora configurada (REPORT_HOUR, en TRADING_TIMEZONE):
  valor del portafolio, cambio del día, P&L, posiciones, estado de cada estrategia y avisos.
- Alertas inmediatas por Telegram cuando pasa algo que hay que atender: un módulo se detuvo,
  una orden se llenó sin registrarse, una fuente de precios se cayó o se alcanzó el límite de
  pérdida diaria. Cada alerta se manda una sola vez hasta que se resuelve.
- Latido para healthchecks.io (HEALTHCHECK_URL): cada 5 minutos avisa "sigo vivo". Si el bot se
  muere o la computadora se apaga, healthchecks.io deja de recibirlo y te manda un correo.
"""
import asyncio
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Set
from zoneinfo import ZoneInfo

from core.utils.logger import get_logger

logger = get_logger("reports")

HEARTBEAT_SECONDS = 300


def _fmt_money(x: float) -> str:
    return ("-$" if x < 0 else "$") + f"{abs(x):,.2f}"


class ReportsBot:
    def __init__(self, system, config: dict):
        self.system = system
        self.config = config
        self.tz = ZoneInfo(config.get("trading_timezone", "America/Mexico_City"))
        self.report_hour = int(config.get("report_hour", 21))
        self.healthcheck_url = (config.get("healthcheck_url") or "").strip()
        from modules.signals.telegram import TelegramNotifier
        self.telegram = TelegramNotifier(config.get("telegram_bot_token", ""), config.get("telegram_chat_id", ""))
        self._alerted: Set[str] = set()
        self._running = False
        self.last_report: Optional[str] = None

    # ── Contenido ──

    async def build_summary(self) -> str:
        s = self.system
        p = s.portfolio
        await p.update_prices()
        summary = await p.get_summary()
        snaps = await s.db.get_portfolio_snapshots(hours=26)
        day_ago = None
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).replace(tzinfo=None).isoformat()
        for snap in snaps:
            if snap["timestamp"] <= cutoff:
                day_ago = snap["total_value_usd"]
        total = summary["total_value_usd"]
        modo = "PAPEL" if s.settings.dry_run else "DINERO REAL"
        lines = [f"Izalith ({modo}) - {datetime.now(self.tz):%d/%m/%Y %H:%M}",
                 f"Valor: {_fmt_money(total)}"]
        if day_ago:
            ch = total - day_ago
            lines.append(f"En 24 h: {'+' if ch >= 0 else ''}{_fmt_money(ch)} ({(total / day_ago - 1) * 100:+.2f}%)")
        start = summary.get("initial_cash_usd") or 0
        if start:
            lines.append(f"Desde el inicio: {(total / start - 1) * 100:+.2f}% (empezó con {_fmt_money(start)})")
        lines.append(f"P&L realizado hoy: {_fmt_money(summary['realized_pnl_today'])}, abierto: {_fmt_money(summary['unrealized_pnl'])}")

        alloc = s.instances.get("allocator")
        if alloc is not None:
            for st in alloc.get_status().get("strategies", {}).values():
                eq = st.get("equity")
                res = f" {(eq / st['capital'] - 1) * 100:+.1f}%" if eq else ""
                lines.append(f"- {st.get('nombre')}: {st.get('status')}{res}")
        if summary["positions"]:
            lines.append("Posiciones: " + ", ".join(
                f"{x['symbol']} {x['unrealized_pnl_pct']:+.1f}%" for x in summary["positions"]))
        problems = self.current_problems()
        if problems:
            lines.append("Avisos:")
            lines += [f"- {msg}" for msg in problems.values()]
        return "\n".join(lines)

    def current_problems(self) -> Dict[str, str]:
        """Lo que hay que atender ahora. La llave sirve para no repetir la misma alerta."""
        s = self.system
        out: Dict[str, str] = {}
        guard = getattr(s, "guard", None)
        if guard is not None and guard.active:
            out["freno"] = f"Freno de emergencia activo: {guard.state.get('motivo')}"
        for name, err in s.module_errors.items():
            out[f"modulo:{name}"] = f"El módulo {name} se detuvo: {err}"
        for o in s.unrecorded_orders:
            out[f"orden:{o['client_order_id']}"] = (f"La orden {o['side']} {o['symbol']} se llenó en el exchange y no quedó "
                                                    f"registrada. Revísala a mano.")
        for market, st in (s.feed.get_market_status() if s.feed else {}).items():
            err = st.get("last_error") or ""
            if err.startswith("sin llaves"):
                continue
            if st.get("simulating") and not s.settings.dry_run:
                out[f"sim:{market}"] = f"{st.get('name', market)}: precios simulados con dinero real"
            elif not st.get("connected") and not st.get("simulating"):
                out[f"caida:{market}"] = f"{st.get('name', market)}: sin conexión ({err[:80]})"
        return out

    # ── Envío ──

    async def send(self, text: str) -> bool:
        if not self.telegram.enabled:
            logger.info("Telegram no configurado; el reporte queda en el log:\n" + text)
            return False
        return await self.telegram.send_alert("Izalith", text, "info")

    async def check_alerts(self) -> None:
        problems = self.current_problems()
        # Límite de pérdida diaria del scalper
        try:
            limit = float(self.config.get("scalper_max_daily_loss_usd", 0) or 0)
            if limit and "scalper" in self.system.instances and await self.system.db.get_daily_pnl("scalper") <= -limit:
                problems["limite:scalper"] = f"El scalper llegó a su pérdida máxima del día ({_fmt_money(limit)}) y dejó de abrir operaciones"
        except Exception:
            pass
        new = {k: v for k, v in problems.items() if k not in self._alerted}
        for key, msg in new.items():
            if await self.send("Atención: " + msg) or not self.telegram.enabled:
                self._alerted.add(key)
        # Lo que ya se resolvió se puede volver a avisar si regresa
        self._alerted &= set(problems)

    def _ping(self, fail: bool = False) -> None:
        if not self.healthcheck_url:
            return
        url = self.healthcheck_url.rstrip("/") + ("/fail" if fail else "")
        try:
            urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "izalith"}), timeout=10).read()
        except Exception as e:
            logger.warning(f"Healthcheck no respondió: {e}")

    # ── Ciclo ──

    async def run(self) -> None:
        self._running = True
        logger.info(f"Reportes: resumen diario a las {self.report_hour}:00 ({self.tz.key}); "
                    f"Telegram {'sí' if self.telegram.enabled else 'no configurado'}; "
                    f"healthcheck {'sí' if self.healthcheck_url else 'no configurado'}")
        while self._running:
            try:
                await self.check_alerts()
                now = datetime.now(self.tz)
                today = now.strftime("%Y-%m-%d")
                sent_key = "reports:last_daily"
                last = await self.system.db.kv_get(sent_key)
                if now.hour >= self.report_hour and last != today:
                    if await self.send(await self.build_summary()) or not self.telegram.enabled:
                        await self.system.db.kv_set(sent_key, today)
                        self.last_report = now.isoformat()
                await asyncio.to_thread(self._ping, bool(self.system.module_errors))
            except Exception as e:
                logger.error(f"Reportes: {e}")
            await asyncio.sleep(HEARTBEAT_SECONDS)

    async def stop(self) -> None:
        self._running = False
        await self.telegram.close()

    def get_status(self) -> Dict:
        return {"running": self._running, "telegram": self.telegram.enabled,
                "healthcheck": bool(self.healthcheck_url), "report_hour": self.report_hour,
                "last_report": self.last_report}

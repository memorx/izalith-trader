"""
Izalith Trading System - Calendario de la bolsa de Nueva York (NYSE)

Feriados y cierres temprano (13:00) publicados por NYSE. Hay que agregar cada año nuevo:
fuera de los años de la tabla se usa sólo la regla de lunes a viernes y se avisa en el log.
"""
from datetime import date, datetime
from typing import Optional

import pytz

from core.utils.logger import get_logger

logger = get_logger("calendario")

NY = pytz.timezone("America/New_York")

NYSE_HOLIDAYS = {
    2025: ["01-01", "01-09", "01-20", "02-17", "04-18", "05-26", "06-19", "07-04", "09-01", "11-27", "12-25"],
    2026: ["01-01", "01-19", "02-16", "04-03", "05-25", "06-19", "07-03", "09-07", "11-26", "12-25"],
    2027: ["01-01", "01-18", "02-15", "03-26", "05-31", "06-18", "07-05", "09-06", "11-25", "12-24"],
}
NYSE_EARLY_CLOSE = {
    2025: ["07-03", "11-28", "12-24"],
    2026: ["11-27", "12-24"],
    2027: ["11-26"],
}
_warned = set()


def _known(year: int) -> bool:
    if year in NYSE_HOLIDAYS:
        return True
    if year not in _warned:
        _warned.add(year)
        logger.warning(f"El calendario de NYSE no tiene los feriados de {year}: agrégalos en core/market_calendar.py")
    return False


def is_nyse_holiday(d: date) -> bool:
    return _known(d.year) and d.strftime("%m-%d") in NYSE_HOLIDAYS[d.year]


def nyse_is_open(now: Optional[datetime] = None) -> bool:
    """¿Está abierta la sesión regular de NYSE (9:30 a 16:00, o a 13:00 los días de cierre temprano)?"""
    now = (now or datetime.now(NY)).astimezone(NY)
    if now.weekday() >= 5 or is_nyse_holiday(now.date()):
        return False
    close_h = 13 if _known(now.year) and now.strftime("%m-%d") in NYSE_EARLY_CLOSE.get(now.year, []) else 16
    start = now.replace(hour=9, minute=30, second=0, microsecond=0)
    end = now.replace(hour=close_h, minute=0, second=0, microsecond=0)
    return start <= now < end

"""
Izalith Trading System - Collectors

Snapshots of markets few people watch: funding across exchanges, Bitso MXN premium,
stock perpetuals, prediction markets. Data first; strategies only after enough history.
"""
from .bot import CollectorsBot

__all__ = ["CollectorsBot"]

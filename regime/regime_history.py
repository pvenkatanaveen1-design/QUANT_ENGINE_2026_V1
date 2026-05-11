from __future__ import annotations

from repositories.regime_repository import RegimeRepository
from services.storage_service import storage


def load_regime_history(symbol: str, timeframe: str, limit: int = 500) -> list[dict]:
    repo = RegimeRepository(storage)
    return repo.get_history(symbol=symbol, timeframe=timeframe, limit=limit)


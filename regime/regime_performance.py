from __future__ import annotations

from repositories.regime_repository import RegimeRepository
from services.storage_service import storage


def load_regime_performance(years: float) -> list[dict]:
    repo = RegimeRepository(storage)
    return repo.get_regime_performance(years=years)


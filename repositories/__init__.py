"""
repositories/ — Clean CRUD abstraction over the database layer.

No system outside this folder should write raw SQL.
All data access goes through typed repository classes.

Usage:
    from repositories.trade_repository import TradeRepository
    from services.storage_service import storage

    repo = TradeRepository(storage)
    repo.insert(trade_event)
    open_trades = repo.get_open()
"""

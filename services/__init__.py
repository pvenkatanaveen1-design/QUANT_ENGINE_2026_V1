"""
services/ — Reusable infrastructure utilities.

Services are shared building blocks used by multiple systems.
They have NO business logic.  They only handle infrastructure concerns:
  - Storage (StorageService)
  - Notifications (future: NotificationService)
  - Broker connectivity (future: wrapped in execution/ instead)

Usage:
    from services.storage_service import storage
    storage.execute_sqlite("INSERT INTO trades ...", params)
"""

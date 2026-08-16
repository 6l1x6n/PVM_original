# -*- coding: utf-8 -*-
"""
PVM.core v2.7.0 - Market Logic
===============================
Re-exports SQL managers from db_sqlite for backward compatibility.
All data management is done via SQLite.
"""

# Re-export SQL managers as the canonical managers
from db_sqlite import (
    GoodsManagerSQL as GoodsManager,
    PartnersManagerSQL as PartnersManager,
    ReceiptsManagerSQL as ReceiptsManager,
    PurchasesManagerSQL as PurchasesManager,
    QuickItemsManagerSQL as QuickItemsManager,
    WriteoffsManagerSQL as WriteoffManager,
    InventoryAuditManagerSQL as InventoryAuditManager,
    InventoryOpsManagerSQL as InventoryOpsManager,
    DatabaseManager,
)

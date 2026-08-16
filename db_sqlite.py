# -*- coding: utf-8 -*-
"""
PVM.core v2.7.0 - SQLite Database Module
=========================================
All local data storage: goods, partners, receipts, purchases, quick items.
Includes JSON→SQLite migration for first-time upgrade.
"""

import os
import sqlite3
import json
import hashlib
import threading
from datetime import datetime, date
from contextlib import contextmanager


# =============================================================================
# DATABASE MANAGER
# =============================================================================
class DatabaseManager:
    """Centralized SQLite database manager with thread-local connection pool.
    
    Each thread gets ONE reusable connection instead of a new connect/close
    on every operation. WAL mode and PRAGMAs are set once per connection.
    This reduces overhead from ~70 connect/disconnect cycles per sync to ~1.
    """
    
    def __init__(self, db_path):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._local = threading.local()
        self._backup_path = None
        try:
            self._init_database()
        except Exception as e:
            # C1: never delete the DB file on failure. Back it up and exit
            # with a clear error so the operator can recover the data.
            self._backup_corrupt_db()
            if isinstance(e, sqlite3.OperationalError):
                hint = (" База данных повреждена." if 'malformed' in str(e).lower()
                        else " Не удалось инициализировать базу данных.")
            else:
                hint = " Не удалось инициализировать базу данных."
            backup_note = (f"\nРезервная копия: {self._backup_path}"
                           if self._backup_path else "")
            raise RuntimeError(
                f"{hint}{backup_note}\nПричина: {e}") from e

    def _backup_corrupt_db(self):
        """Copy the (possibly corrupt) DB file before giving up, so data can
        be recovered instead of silently destroyed."""
        import shutil
        try:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup = f"{self.db_path}.corrupt_{ts}.bak"
            if os.path.exists(self.db_path):
                shutil.copy2(self.db_path, backup)
                for suffix in ('-wal', '-shm'):
                    src = self.db_path + suffix
                    if os.path.exists(src):
                        shutil.copy2(src, backup + suffix)
                self._backup_path = backup
        except Exception:
            pass
    
    def _get_thread_conn(self):
        """Get or create a connection for the current thread."""
        conn = getattr(self._local, 'conn', None)
        # Check if connection is still alive
        if conn is not None:
            try:
                conn.execute("SELECT 1")
                return conn
            except (sqlite3.ProgrammingError, sqlite3.OperationalError):
                # Connection was closed or broken — recreate
                self._local.conn = None
                conn = None
        
        # Create new connection for this thread
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.create_function("LOWER", 1, lambda x: x.lower() if x else x)
        # Thread-local sync-log suppression flag (seen by SQL triggers via
        # this UDF). Only the current thread's own connection is affected,
        # so concurrent writers never get their outbox entries suppressed.
        def _sync_suppressed():
            return 1 if getattr(self._local, 'suppress_sync', False) else 0
        conn.create_function("__pvm_sync_suppressed", 0, _sync_suppressed)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        self._local.conn = conn
        return conn

    @contextmanager
    def suppress_sync_log(self):
        """Suppress sync_log trigger writes for THIS thread's connection.

        Used by sync apply handlers: applying a remote change must never
        re-enqueue the change into the local outbox (echo loop prevention).
        """
        self._local.suppress_sync = True
        try:
            yield
        finally:
            self._local.suppress_sync = False
    
    @contextmanager
    def get_connection(self):
        """Context manager for database connections (thread-local reuse)."""
        conn = self._get_thread_conn()
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
    
    @property
    def sync_log(self):
        """Lazy SyncLogManager instance for this DB."""
        if not hasattr(self, '_sync_log'):
            self._sync_log = SyncLogManager(self)
        return self._sync_log
    
    def _init_database(self):
        """Create all necessary tables."""
        # === PHASE 1: Schema migration on a RAW connection (FK OFF) ===
        # get_connection() forces PRAGMA foreign_keys=ON which prevents
        # table recreation. We use a raw connection here instead.
        self._run_schema_migration()
        
        # === PHASE 2: Normal init (FK ON) ===
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS goods (
                    id TEXT PRIMARY KEY,
                    code TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    pv REAL NOT NULL DEFAULT 0,
                    barcode TEXT DEFAULT '',
                    purchase_price REAL DEFAULT 0,
                    sale_price REAL DEFAULT 0,
                    quantity REAL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    synced INTEGER DEFAULT 0,
                    is_deleted INTEGER DEFAULT 0
                )
            ''')
            
            # Migrations for goods table
            try:
                cursor.execute('ALTER TABLE goods ADD COLUMN synced INTEGER DEFAULT 0')
            except: pass
            try:
                cursor.execute('ALTER TABLE goods ADD COLUMN is_deleted INTEGER DEFAULT 0')
            except: pass

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS partners (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    full_name TEXT,
                    phone TEXT DEFAULT '',
                    email TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    discount REAL DEFAULT 0.5,
                    total_purchases INTEGER DEFAULT 0,
                    total_spent REAL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    synced INTEGER DEFAULT 0,
                    dob TEXT,
        is_blocked INTEGER DEFAULT 0,
        block_reason TEXT DEFAULT '',
        blocked_by TEXT DEFAULT '',
        blocked_at TEXT DEFAULT '',
        last_purchase_at TEXT
                )
            ''')

            # Migrations for partners table
            for col, spec in [('dob', 'TEXT'), ('is_blocked', 'INTEGER DEFAULT 0'), 
                             ('last_purchase_at', 'TEXT'), ('synced', 'INTEGER DEFAULT 0')]:
                try:
                    cursor.execute(f'ALTER TABLE partners ADD COLUMN {col} {spec}')
                except: pass
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS receipts (
                    id TEXT PRIMARY KEY,
                    number INTEGER NOT NULL,
                    datetime TEXT NOT NULL,
                    partner_id TEXT,
                    subtotal REAL NOT NULL,
                    discount REAL DEFAULT 0,
                    total REAL NOT NULL,
                    payment_cash REAL DEFAULT 0,
                    payment_card REAL DEFAULT 0,
                    payment_internal REAL DEFAULT 0,
                    change_given REAL DEFAULT 0,
                    status TEXT DEFAULT 'completed',
                    synced INTEGER DEFAULT 0,
                    refund_datetime TEXT,
                    refund_reason TEXT,
                    refunded_by TEXT DEFAULT '',
                    cashier_user TEXT DEFAULT '',
                    updated_at TEXT,
                    live_sent INTEGER DEFAULT 0,
                    live_error TEXT,
                    live_status INTEGER DEFAULT 0,
                    live_processed_at TEXT,
                    pv_bot_status INTEGER DEFAULT 0,
                    pv_bot_date TEXT,
                    pv_bot_error TEXT,
                    refund_total REAL DEFAULT 0,
                    refund_method TEXT DEFAULT ''
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS receipt_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    receipt_id TEXT NOT NULL,
                    good_code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    price REAL NOT NULL,
                    pv REAL DEFAULT 0,
                    sum REAL NOT NULL,
                    refunded_qty REAL DEFAULT 0,
                    live_status INTEGER DEFAULT 0,
                    FOREIGN KEY (receipt_id) REFERENCES receipts(id)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS receipt_refund_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    receipt_id TEXT NOT NULL,
                    datetime TEXT NOT NULL,
                    reason TEXT,
                    user_name TEXT,
                    device_name TEXT,
                    items_json TEXT,
                    FOREIGN KEY (receipt_id) REFERENCES receipts(id)
                )
            ''')
            
            # Migrations for user_name and device_name if table already existed
            try:
                cursor.execute('ALTER TABLE receipt_refund_logs ADD COLUMN user_name TEXT')
            except: pass
            try:
                cursor.execute('ALTER TABLE receipt_refund_logs ADD COLUMN device_name TEXT')
            except: pass
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS purchases (
                    id TEXT PRIMARY KEY,
                    invoice_number TEXT NOT NULL,
                    supplier TEXT NOT NULL,
                    datetime TEXT NOT NULL,
                    total_amount REAL NOT NULL,
                    items_count INTEGER NOT NULL,
                    number INTEGER DEFAULT 0,
                    notes TEXT DEFAULT '',
                    cashier_user TEXT DEFAULT '',
                    status TEXT DEFAULT 'completed',
                    synced INTEGER DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS purchase_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    purchase_id TEXT NOT NULL,
                    code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    purchase_price REAL NOT NULL,
                    sale_price REAL NOT NULL,
                    pv REAL DEFAULT 0,
                    barcode TEXT DEFAULT '',
                    FOREIGN KEY (purchase_id) REFERENCES purchases(id)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS writeoffs (
                    id TEXT PRIMARY KEY,
                    number INTEGER NOT NULL,
                    datetime TEXT NOT NULL,
                    reason TEXT DEFAULT '',
                    items_count INTEGER NOT NULL,
                    cashier_user TEXT DEFAULT '',
                    synced INTEGER DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS writeoff_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    writeoff_id TEXT NOT NULL,
                    good_code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    barcode TEXT DEFAULT '',
                    FOREIGN KEY (writeoff_id) REFERENCES writeoffs(id)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS quick_items (
                    slot_index INTEGER PRIMARY KEY CHECK (slot_index >= 0 AND slot_index < 20),
                    good_code TEXT,
                    item_data TEXT,
                    updated_at TEXT
                )
            ''')
            
            # Migration for updated_at if table already existed
            try:
                cursor.execute('ALTER TABLE quick_items ADD COLUMN updated_at TEXT')
            except: pass

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sync_markers (
                    marker_key TEXT PRIMARY KEY,
                    marker_value TEXT
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sync_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    data TEXT NOT NULL,
                    device_key TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    synced INTEGER NOT NULL DEFAULT 0
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_sync_log_synced ON sync_log(synced)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_sync_log_type ON sync_log(entity_type)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_sync_log_entity ON sync_log(entity_type, entity_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_sync_log_created ON sync_log(created_at)')

            # Idempotency: track which sync_log entries have been applied remotely
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sync_log_applied (
                    sync_log_id INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL,
                    device_id TEXT NOT NULL
                )
            ''')

            # Folder-sync: registry of remote files already applied (idempotent consume)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sync_applied_files (
                    file_name TEXT PRIMARY KEY,
                    file_hash TEXT,
                    applied_at TEXT NOT NULL
                )
            ''')

            # Triggers to auto-populate sync_log on data changes.
            # v2 protocol: every row gets event_id (dedup key), ts_utc and a
            # FULL payload (deletes carry tombstones so remote devices can
            # soft-delete without losing catalog data). The suppress guard
            # prevents echo loops when sync applies remote changes.
            # NOTE: purchases/writeoffs/audits are pushed via their own
            # `synced`-flag / marker paths (they need child rows: items), so
            # their header-only triggers are dropped below.

            # sync_log schema upgrade (v2 columns)
            for col, spec in [('event_id', "TEXT DEFAULT ''"),
                              ('store_id', "TEXT DEFAULT ''"),
                              ('ts_utc', "TEXT DEFAULT ''")]:
                try:
                    cursor.execute(f'ALTER TABLE sync_log ADD COLUMN {col} {spec}')
                except: pass
            try:
                cursor.execute("UPDATE sync_log SET event_id = lower(hex(randomblob(16))) WHERE event_id = ''")
                cursor.execute("UPDATE sync_log SET ts_utc = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE ts_utc = ''")
            except Exception: pass

            # Inbox: transactional staging + per-event idempotent apply
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sync_inbox (
                    event_id TEXT PRIMARY KEY,
                    source_device TEXT DEFAULT '',
                    file_name TEXT DEFAULT '',
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    data TEXT DEFAULT '{}',
                    updated_at TEXT DEFAULT '',
                    received_at TEXT NOT NULL,
                    attempts INTEGER DEFAULT 0,
                    last_error TEXT DEFAULT '',
                    applied INTEGER DEFAULT 0
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_sync_inbox_pending ON sync_inbox(applied, received_at)')

            # Known devices: used by the janitor to delete outbox files only
            # after every other device acknowledged them (no lost changes).
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sync_device_registry (
                    device_key TEXT PRIMARY KEY,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL
                )
            ''')

            # Empty table; a row (id=0) would mark the legacy suppression
            # mode. Kept for schema stability.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sync_suppress (id INTEGER PRIMARY KEY CHECK (id = 0))
            ''')

            # --- goods triggers ---
            cursor.execute('DROP TRIGGER IF EXISTS trg_goods_insert')
            cursor.execute('''
                CREATE TRIGGER IF NOT EXISTS trg_goods_insert AFTER INSERT ON goods
                BEGIN
                    INSERT INTO sync_log (entity_type, entity_id, operation, data, device_key, version, created_at, event_id, ts_utc, synced)
                    SELECT 'goods', NEW.code, 'INSERT', json_object('code', NEW.code, 'name', NEW.name, 'barcode', NEW.barcode, 'purchase_price', NEW.purchase_price, 'sale_price', NEW.sale_price, 'quantity', NEW.quantity, 'pv', NEW.pv, 'is_deleted', NEW.is_deleted, 'updated_at', NEW.updated_at), '', 1, datetime('now'), lower(hex(randomblob(16))), strftime('%Y-%m-%dT%H:%M:%SZ','now'), 0
                    WHERE __pvm_sync_suppressed() = 0;
                END;
            ''')
            cursor.execute('DROP TRIGGER IF EXISTS trg_goods_update')
            cursor.execute('''
                CREATE TRIGGER IF NOT EXISTS trg_goods_update AFTER UPDATE OF name, pv, barcode, purchase_price, sale_price, is_deleted ON goods
                BEGIN
                    INSERT INTO sync_log (entity_type, entity_id, operation, data, device_key, version, created_at, event_id, ts_utc, synced)
                    SELECT 'goods', NEW.code, 'UPDATE', json_object('code', NEW.code, 'name', NEW.name, 'barcode', NEW.barcode, 'purchase_price', NEW.purchase_price, 'sale_price', NEW.sale_price, 'quantity', NEW.quantity, 'pv', NEW.pv, 'is_deleted', NEW.is_deleted, 'updated_at', NEW.updated_at), '', 1, datetime('now'), lower(hex(randomblob(16))), strftime('%Y-%m-%dT%H:%M:%SZ','now'), 0
                    WHERE __pvm_sync_suppressed() = 0;
                END;
            ''')
            cursor.execute('DROP TRIGGER IF EXISTS trg_goods_stock_delta')
            cursor.execute('''
                CREATE TRIGGER IF NOT EXISTS trg_goods_stock_delta AFTER UPDATE OF quantity ON goods
                BEGIN
                    INSERT INTO sync_log (entity_type, entity_id, operation, data, device_key, version, created_at, event_id, ts_utc, synced)
                    SELECT 'stock_delta', NEW.code, 'UPDATE', json_object('code', NEW.code, 'delta', NEW.quantity - OLD.quantity, 'updated_at', NEW.updated_at), '', 1, datetime('now'), lower(hex(randomblob(16))), strftime('%Y-%m-%dT%H:%M:%SZ','now'), 0
                    WHERE __pvm_sync_suppressed() = 0 AND NEW.quantity != OLD.quantity;
                END;
            ''')
            cursor.execute('DROP TRIGGER IF EXISTS trg_goods_delete')
            cursor.execute('''
                CREATE TRIGGER IF NOT EXISTS trg_goods_delete AFTER DELETE ON goods
                BEGIN
                    INSERT INTO sync_log (entity_type, entity_id, operation, data, device_key, version, created_at, event_id, ts_utc, synced)
                    SELECT 'goods', OLD.code, 'DELETE', json_object('code', OLD.code, 'name', OLD.name, 'barcode', OLD.barcode, 'purchase_price', OLD.purchase_price, 'sale_price', OLD.sale_price, 'quantity', OLD.quantity, 'pv', OLD.pv, 'is_deleted', 1, 'updated_at', OLD.updated_at), '', 1, datetime('now'), lower(hex(randomblob(16))), strftime('%Y-%m-%dT%H:%M:%SZ','now'), 0
                    WHERE __pvm_sync_suppressed() = 0;
                END;
            ''')

            # --- partners triggers ---
            cursor.execute('DROP TRIGGER IF EXISTS trg_partners_insert')
            cursor.execute('''
                CREATE TRIGGER IF NOT EXISTS trg_partners_insert AFTER INSERT ON partners
                BEGIN
                    INSERT INTO sync_log (entity_type, entity_id, operation, data, device_key, version, created_at, event_id, ts_utc, synced)
                    SELECT 'partners', NEW.id, 'INSERT', json_object('id', NEW.id, 'name', NEW.name, 'full_name', NEW.full_name, 'phone', NEW.phone, 'email', NEW.email, 'discount', NEW.discount, 'dob', NEW.dob, 'is_blocked', NEW.is_blocked, 'block_reason', NEW.block_reason, 'blocked_by', NEW.blocked_by, 'blocked_at', NEW.blocked_at, 'last_purchase_at', NEW.last_purchase_at, 'updated_at', NEW.updated_at), '', 1, datetime('now'), lower(hex(randomblob(16))), strftime('%Y-%m-%dT%H:%M:%SZ','now'), 0
                    WHERE __pvm_sync_suppressed() = 0;
                END;
            ''')
            cursor.execute('DROP TRIGGER IF EXISTS trg_partners_update')
            cursor.execute('''
                CREATE TRIGGER IF NOT EXISTS trg_partners_update AFTER UPDATE ON partners
                BEGIN
                    INSERT INTO sync_log (entity_type, entity_id, operation, data, device_key, version, created_at, event_id, ts_utc, synced)
                    SELECT 'partners', NEW.id, 'UPDATE', json_object('id', NEW.id, 'name', NEW.name, 'full_name', NEW.full_name, 'phone', NEW.phone, 'email', NEW.email, 'discount', NEW.discount, 'dob', NEW.dob, 'is_blocked', NEW.is_blocked, 'block_reason', NEW.block_reason, 'blocked_by', NEW.blocked_by, 'blocked_at', NEW.blocked_at, 'last_purchase_at', NEW.last_purchase_at, 'updated_at', NEW.updated_at), '', 1, datetime('now'), lower(hex(randomblob(16))), strftime('%Y-%m-%dT%H:%M:%SZ','now'), 0
                    WHERE __pvm_sync_suppressed() = 0;
                END;
            ''')
            cursor.execute('DROP TRIGGER IF EXISTS trg_partners_delete')
            cursor.execute('''
                CREATE TRIGGER IF NOT EXISTS trg_partners_delete AFTER DELETE ON partners
                BEGIN
                    INSERT INTO sync_log (entity_type, entity_id, operation, data, device_key, version, created_at, event_id, ts_utc, synced)
                    SELECT 'partners', OLD.id, 'DELETE', json_object('id', OLD.id, 'name', OLD.name, 'full_name', OLD.full_name, 'phone', OLD.phone, 'email', OLD.email, 'discount', OLD.discount, 'dob', OLD.dob, 'is_blocked', OLD.is_blocked, 'block_reason', OLD.block_reason, 'blocked_by', OLD.blocked_by, 'blocked_at', OLD.blocked_at, 'last_purchase_at', OLD.last_purchase_at, 'updated_at', OLD.updated_at), '', 1, datetime('now'), lower(hex(randomblob(16))), strftime('%Y-%m-%dT%H:%M:%SZ','now'), 0
                    WHERE __pvm_sync_suppressed() = 0;
                END;
            ''')

            # purchases/writeoffs/audits: header-only trigger payloads cannot
            # carry child rows (items), so these entities are synced via
            # dedicated flag/marker paths with full data. Drop legacy triggers.
            for t in ('trg_purchases_insert', 'trg_purchases_update', 'trg_purchases_delete',
                      'trg_writeoffs_insert', 'trg_writeoffs_update', 'trg_writeoffs_delete',
                      'trg_audits_insert', 'trg_audits_update', 'trg_audits_delete'):
                try:
                    cursor.execute(f'DROP TRIGGER IF EXISTS {t}')
                except Exception:
                    pass

            # Users table for local authentication & permissions
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS app_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'cashier',
                    pin_hash TEXT NOT NULL,
                    pin_hint TEXT DEFAULT '',
                    permissions TEXT DEFAULT '{}',
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            ''')
            
            # Partners history table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS partners_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    partner_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details TEXT,
                    user_name TEXT,
                    timestamp TEXT NOT NULL
                )
            ''')
            
            # Cancelled items table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS cancelled_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    good_code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    action TEXT NOT NULL,
                    cashier TEXT DEFAULT '',
                    timestamp TEXT NOT NULL
                )
            ''')
            
            # Inventory audit tables
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS inventory_audits (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    audit_type TEXT NOT NULL DEFAULT 'full',
                    filter_criteria TEXT DEFAULT '{}',
                    created_by TEXT DEFAULT '',
                    completed_by TEXT DEFAULT '',
                    snapshot_total_items INTEGER DEFAULT 0,
                    counted_items INTEGER DEFAULT 0,
                    total_surplus REAL DEFAULT 0,
                    total_shortage REAL DEFAULT 0,
                    total_difference_money REAL DEFAULT 0,
                    applied INTEGER DEFAULT 0,
                    applied_at TEXT,
                    applied_by TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    zero_negatives INTEGER DEFAULT 0,
                    created_device TEXT DEFAULT '',
                    completed_device TEXT DEFAULT '',
                    applied_device TEXT DEFAULT '',
                    updated_at TEXT
                )
            ''')
            
            # Migrations for new device tracking columns
            for col in ['created_device', 'completed_device', 'applied_device']:
                try:
                    cursor.execute(f'ALTER TABLE inventory_audits ADD COLUMN {col} TEXT DEFAULT ""')
                except: pass
            
            try:
                cursor.execute('ALTER TABLE inventory_audits ADD COLUMN zero_negatives INTEGER DEFAULT 0')
            except: pass
            
            # Audit triggers are intentionally NOT recreated: header-only
            # payloads cannot carry audit items. Audits sync via the dedicated
            # marker path (see sync_engine._flush_audits).
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS inventory_audit_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    audit_id TEXT NOT NULL,
                    good_id TEXT NOT NULL,
                    good_code TEXT NOT NULL,
                    good_name TEXT NOT NULL,
                    good_barcode TEXT DEFAULT '',
                    sale_price REAL DEFAULT 0,
                    purchase_price REAL DEFAULT 0,
                    expected_qty REAL NOT NULL DEFAULT 0,
                    actual_qty REAL,
                    sold_during_audit REAL DEFAULT 0,
                    adjusted_expected REAL DEFAULT 0,
                    difference REAL DEFAULT 0,
                    difference_money REAL DEFAULT 0,
                    counted_at TEXT,
                    counted_by TEXT DEFAULT '',
                    FOREIGN KEY (audit_id) REFERENCES inventory_audits(id)
                )
            ''')
            
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_audit_items_audit ON inventory_audit_items(audit_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_audit_items_good ON inventory_audit_items(good_code)')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS autoreview_sessions (
                    id TEXT PRIMARY KEY,
                    started_at TEXT,
                    finished_at TEXT,
                    duration_sec INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'running',
                    items_total INTEGER DEFAULT 0,
                    items_parsed INTEGER DEFAULT 0,
                    items_created INTEGER DEFAULT 0,
                    items_updated INTEGER DEFAULT 0,
                    items_skipped INTEGER DEFAULT 0,
                    error_message TEXT DEFAULT '',
                    log_text TEXT DEFAULT '',
                    skipped_codes TEXT DEFAULT '[]',
                    stats TEXT DEFAULT '{}'
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_ar_sessions_started ON autoreview_sessions(started_at)')
            
            # Initialize 20 quick item slots
            for i in range(20):
                cursor.execute(
                    'INSERT OR IGNORE INTO quick_items (slot_index, good_code, item_data) VALUES (?, NULL, NULL)',
                    (i,))
            
            # Migration: add missing columns if upgrading existing DB
            try:
                cursor.execute("PRAGMA table_info(receipts)")
                receipts_cols = [col['name'] for col in cursor.fetchall()]
                if 'status' not in receipts_cols:
                    cursor.execute("ALTER TABLE receipts ADD COLUMN status TEXT DEFAULT 'completed'")
                if 'refund_datetime' not in receipts_cols:
                    cursor.execute("ALTER TABLE receipts ADD COLUMN refund_datetime TEXT")
                if 'refund_reason' not in receipts_cols:
                    cursor.execute("ALTER TABLE receipts ADD COLUMN refund_reason TEXT")
                if 'cashier_user' not in receipts_cols:
                    cursor.execute("ALTER TABLE receipts ADD COLUMN cashier_user TEXT DEFAULT ''")
                if 'synced' not in receipts_cols:
                    cursor.execute("ALTER TABLE receipts ADD COLUMN synced INTEGER DEFAULT 0")
                if 'refunded_by' not in receipts_cols:
                    cursor.execute("ALTER TABLE receipts ADD COLUMN refunded_by TEXT DEFAULT ''")
                if 'updated_at' not in receipts_cols:
                    cursor.execute("ALTER TABLE receipts ADD COLUMN updated_at TEXT")
                    cursor.execute("UPDATE receipts SET updated_at = datetime WHERE updated_at IS NULL")
                if 'refund_total' not in receipts_cols:
                    cursor.execute("ALTER TABLE receipts ADD COLUMN refund_total REAL DEFAULT 0")
                if 'refund_method' not in receipts_cols:
                    cursor.execute("ALTER TABLE receipts ADD COLUMN refund_method TEXT DEFAULT ''")
                
                cursor.execute("PRAGMA table_info(receipt_items)")
                items_cols = [col['name'] for col in cursor.fetchall()]
                if 'refunded_qty' not in items_cols:
                    cursor.execute("ALTER TABLE receipt_items ADD COLUMN refunded_qty REAL DEFAULT 0")
                if 'pv' not in items_cols:
                    cursor.execute("ALTER TABLE receipt_items ADD COLUMN pv REAL DEFAULT 0")
                
                cursor.execute("PRAGMA table_info(purchases)")
                purchases_cols = [col['name'] for col in cursor.fetchall()]
                if 'cashier_user' not in purchases_cols:
                    cursor.execute("ALTER TABLE purchases ADD COLUMN cashier_user TEXT DEFAULT ''")
                if 'synced' not in purchases_cols:
                    cursor.execute("ALTER TABLE purchases ADD COLUMN synced INTEGER DEFAULT 0")
                if 'number' not in purchases_cols:
                    cursor.execute("ALTER TABLE purchases ADD COLUMN number INTEGER DEFAULT 0")
                
                cursor.execute("PRAGMA table_info(partners)")
                partners_cols = [col['name'] for col in cursor.fetchall()]
                if 'updated_at' not in partners_cols:
                    cursor.execute("ALTER TABLE partners ADD COLUMN updated_at TEXT")
                    cursor.execute("UPDATE partners SET updated_at = created_at WHERE updated_at IS NULL")
                if 'synced' not in partners_cols:
                    cursor.execute("ALTER TABLE partners ADD COLUMN synced INTEGER DEFAULT 0")
                if 'dob' not in partners_cols:
                    cursor.execute("ALTER TABLE partners ADD COLUMN dob TEXT")
                if 'is_blocked' not in partners_cols:
                    cursor.execute("ALTER TABLE partners ADD COLUMN is_blocked INTEGER DEFAULT 0")
                if 'block_reason' not in partners_cols:
                    cursor.execute("ALTER TABLE partners ADD COLUMN block_reason TEXT DEFAULT ''")
                if 'blocked_by' not in partners_cols:
                    cursor.execute("ALTER TABLE partners ADD COLUMN blocked_by TEXT DEFAULT ''")
                if 'blocked_at' not in partners_cols:
                    cursor.execute("ALTER TABLE partners ADD COLUMN blocked_at TEXT DEFAULT ''")

                cursor.execute("PRAGMA table_info(goods)")
                goods_cols = [col['name'] for col in cursor.fetchall()]
                if 'updated_at' not in goods_cols:
                    cursor.execute("ALTER TABLE goods ADD COLUMN updated_at TEXT")
                    cursor.execute("UPDATE goods SET updated_at = created_at WHERE updated_at IS NULL")
                if 'is_deleted' not in goods_cols:
                    cursor.execute("ALTER TABLE goods ADD COLUMN is_deleted INTEGER DEFAULT 0")
                
                # Migration: add updated_at to inventory_audits for proper sync filtering
                cursor.execute("PRAGMA table_info(inventory_audits)")
                audit_cols = [col['name'] for col in cursor.fetchall()]
                if 'updated_at' not in audit_cols:
                    cursor.execute("ALTER TABLE inventory_audits ADD COLUMN updated_at TEXT")
                    cursor.execute("UPDATE inventory_audits SET updated_at = COALESCE(completed_at, created_at) WHERE updated_at IS NULL")
            except Exception as e:
                print(f"Migration error (ignorable if columns exist): {e}")
            
            # Indexes
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_goods_code ON goods(code)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_goods_barcode ON goods(barcode)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_receipts_datetime ON receipts(datetime)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_receipts_number ON receipts(number)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_receipt_items_receipt ON receipt_items(receipt_id)')

    def update_device_name_in_history(self, old_name, new_name):
        """Update all local history records where this device's name is stored (Device/User format)."""
        if not old_name or not new_name or old_name == new_name:
            return False
        
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # 1. Update tables using combined "Device/User" format
                tables_cols = [
                    ('receipts', 'cashier_user'),
                    ('purchases', 'cashier_user'),
                    ('writeoffs', 'cashier_user'),
                    ('cancelled_items', 'cashier'),
                    ('partners_history', 'user_name')
                ]
                
                for table, col in tables_cols:
                    # Finds rows starting with "OldName/" and replaces the prefix
                    cursor.execute(f'''
                        UPDATE {table} 
                        SET {col} = ? || SUBSTR({col}, INSTR({col}, '/'))
                        WHERE {col} LIKE ? || '/%'
                    ''', (new_name, old_name))
                
                # 2. Update tables with dedicated device_name column
                try:
                    cursor.execute('''
                        UPDATE receipt_refund_logs SET device_name = ? WHERE device_name = ?
                    ''', (new_name, old_name))
                except: pass
                
                # 3. Update inventory_audits (created_by, completed_by, applied_by)
                for col in ['created_by', 'completed_by', 'applied_by']:
                    cursor.execute(f'''
                        UPDATE inventory_audits 
                        SET {col} = ? || SUBSTR({col}, INSTR({col}, '/'))
                        WHERE {col} LIKE ? || '/%'
                    ''', (new_name, old_name))
                
                # 4. Update inventory_audit_items (counted_by)
                cursor.execute('''
                    UPDATE inventory_audit_items 
                    SET counted_by = ? || SUBSTR(counted_by, INSTR(counted_by, '/'))
                    WHERE counted_by LIKE ? || '/%'
                ''', (new_name, old_name))
                
                return True
        except Exception as e:
            print(f"Error updating device name in history: {e}")
            return False

    def _run_schema_migration(self):
        """Run schema migration on a RAW connection with FK checks disabled.
        
        This is separate from get_connection() because that method always
        enables PRAGMA foreign_keys=ON, which prevents table recreation.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        # Sync-log suppression stub: mass migration updates must not produce
        # outbox entries, and triggers must not fail on a missing function.
        conn.create_function("__pvm_sync_suppressed", 0, lambda: 1)
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            cursor = conn.cursor()

            # Remove the retired goods-history table and its indexes from
            # existing installations. The operation is idempotent.
            cursor.execute("DROP TABLE IF EXISTS goods_history")
            
            # Step 1: Clean up orphan tables from previous failed migrations
            for old_table in ['receipt_items_old', 'receipts_old', 'purchase_items_old', 'purchases_old']:
                cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{old_table}'")
                if cursor.fetchone():
                    # Check if the MAIN table also exists (migration was partial)
                    main_table = old_table.replace('_old', '')
                    cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{main_table}'")
                    if cursor.fetchone():
                        # Both exist — drop the orphan _old
                        cursor.execute(f"DROP TABLE {old_table}")
                        print(f"🧹 Cleaned up orphan table: {old_table}")
                    else:
                        # Only _old exists — rename it back
                        cursor.execute(f"ALTER TABLE {old_table} RENAME TO {main_table}")
                        print(f"🔧 Restored table from orphan: {old_table} → {main_table}")
            
            conn.commit()
            
            # Step 2: Fix receipts (remove UNIQUE on number, remove FK to partners, fix broken FKs)
            needs_receipts_fix = False
            
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='receipts'")
            if cursor.fetchone():
                cursor.execute("PRAGMA index_list('receipts')")
                for idx in cursor.fetchall():
                    cursor.execute(f"PRAGMA index_info('{idx['name']}')")
                    info = cursor.fetchall()
                    if not idx['name'].startswith('sqlite_autoindex_receipts_1') and idx['unique'] and any(col['name'] == 'number' for col in info):
                        needs_receipts_fix = True
                        break
                
                cursor.execute("PRAGMA foreign_key_list('receipts')")
                if any(fk[2] == 'partners' for fk in cursor.fetchall()):
                    needs_receipts_fix = True
            
            # Check if receipt_items FK is broken (pointing to _old or wrong table)
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='receipt_items'")
            if cursor.fetchone():
                cursor.execute("PRAGMA foreign_key_list('receipt_items')")
                for fk in cursor.fetchall():
                    if fk[2] != 'receipts':
                        needs_receipts_fix = True
                        break
            
            if needs_receipts_fix:
                print("🔄 Stabilizing receipts schema...")
                cursor.execute("PRAGMA table_info(receipts)")
                r_cols = [c['name'] for c in cursor.fetchall()]
                cursor.execute("PRAGMA table_info(receipt_items)")
                item_cols = [c['name'] for c in cursor.fetchall()]
                
                cursor.execute("ALTER TABLE receipts RENAME TO receipts_old")
                cursor.execute("ALTER TABLE receipt_items RENAME TO receipt_items_old")
                
                cursor.execute('''
                    CREATE TABLE receipts (
                        id TEXT PRIMARY KEY,
                        number INTEGER NOT NULL,
                        datetime TEXT NOT NULL,
                        partner_id TEXT,
                        subtotal REAL NOT NULL,
                        discount REAL DEFAULT 0,
                        total REAL NOT NULL,
                        payment_cash REAL DEFAULT 0,
                        payment_card REAL DEFAULT 0,
                        payment_internal REAL DEFAULT 0,
                        change_given REAL DEFAULT 0,
                        status TEXT DEFAULT 'completed',
                        synced INTEGER DEFAULT 0,
                        refund_datetime TEXT,
                        refund_reason TEXT,
                        cashier_user TEXT DEFAULT ''
                    )
                ''')
                
                cursor.execute('''
                    CREATE TABLE receipt_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        receipt_id TEXT NOT NULL,
                        good_code TEXT NOT NULL,
                        name TEXT NOT NULL,
                        quantity REAL NOT NULL,
                        price REAL NOT NULL,
                        pv REAL DEFAULT 0,
                        sum REAL NOT NULL,
                        refunded_qty REAL DEFAULT 0,
                        FOREIGN KEY (receipt_id) REFERENCES receipts(id)
                    )
                ''')
                
                cursor.execute(f"INSERT INTO receipts ({', '.join(r_cols)}) SELECT {', '.join(r_cols)} FROM receipts_old")
                cursor.execute(f"INSERT INTO receipt_items ({', '.join(item_cols)}) SELECT {', '.join(item_cols)} FROM receipt_items_old")
                
                cursor.execute("DROP TABLE receipts_old")
                cursor.execute("DROP TABLE receipt_items_old")
                conn.commit()
                print("✅ Receipts schema stabilized.")
            
            # Step 3: Fix purchases (check if purchase_items FK is broken/pointing to _old)
            needs_purchases_fix = False
            
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='purchase_items'")
            if cursor.fetchone():
                cursor.execute("PRAGMA foreign_key_list('purchase_items')")
                for fk in cursor.fetchall():
                    if fk[2] != 'purchases':
                        needs_purchases_fix = True
                        break
            
            if needs_purchases_fix:
                print("🔄 Stabilizing purchases schema...")
                cursor.execute("PRAGMA table_info(purchases)")
                p_cols = [c['name'] for c in cursor.fetchall()]
                cursor.execute("PRAGMA table_info(purchase_items)")
                pi_cols = [c['name'] for c in cursor.fetchall()]
                
                cursor.execute("ALTER TABLE purchases RENAME TO purchases_old")
                cursor.execute("ALTER TABLE purchase_items RENAME TO purchase_items_old")
                
                cursor.execute('''
                    CREATE TABLE purchases (
                        id TEXT PRIMARY KEY,
                        invoice_number TEXT NOT NULL,
                        supplier TEXT NOT NULL,
                        datetime TEXT NOT NULL,
                        total_amount REAL NOT NULL,
                        items_count INTEGER NOT NULL,
                        notes TEXT DEFAULT '',
                        cashier_user TEXT DEFAULT '',
                        status TEXT DEFAULT 'completed',
                        synced INTEGER DEFAULT 0,
                        updated_at TEXT NOT NULL
                    )
                ''')
                cursor.execute('''
                    CREATE TABLE purchase_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        purchase_id TEXT NOT NULL,
                        code TEXT NOT NULL,
                        name TEXT NOT NULL,
                        quantity REAL NOT NULL,
                        purchase_price REAL NOT NULL,
                        sale_price REAL NOT NULL,
                        pv REAL DEFAULT 0,
                        FOREIGN KEY (purchase_id) REFERENCES purchases(id)
                    )
                ''')
                
                cursor.execute(f"INSERT INTO purchases ({', '.join(p_cols)}) SELECT {', '.join(p_cols)} FROM purchases_old")
                cursor.execute(f"INSERT INTO purchase_items ({', '.join(pi_cols)}) SELECT {', '.join(pi_cols)} FROM purchase_items_old")
                
                cursor.execute("DROP TABLE purchases_old")
                cursor.execute("DROP TABLE purchase_items_old")
                conn.commit()
                print("✅ Purchases schema stabilized.")

            # --- SPRINT EXTRA MIGRATIONS (Incremental) ---
            # 1. Barcode in purchase_items
            try:
                cursor.execute("ALTER TABLE purchase_items ADD COLUMN barcode TEXT DEFAULT ''")
            except Exception: pass
            
            # 2. Barcode in writeoff_items
            try:
                cursor.execute("ALTER TABLE writeoff_items ADD COLUMN barcode TEXT DEFAULT ''")
            except Exception: pass

            # 3. Live Bot status in receipts
            try:
                cursor.execute("ALTER TABLE receipts ADD COLUMN live_sent INTEGER DEFAULT 0")
                cursor.execute("ALTER TABLE receipts ADD COLUMN live_error TEXT")
            except Exception: pass
            
            try:
                cursor.execute("ALTER TABLE receipts ADD COLUMN live_status INTEGER DEFAULT 0")
                cursor.execute("ALTER TABLE receipts ADD COLUMN live_processed_at TEXT")
            except Exception: pass

            try:
                cursor.execute("ALTER TABLE receipt_items ADD COLUMN live_status INTEGER DEFAULT 0")
            except Exception: pass

            # 4. Status and updated_at in purchases
            try:
                cursor.execute("ALTER TABLE purchases ADD COLUMN status TEXT DEFAULT 'completed'")
            except Exception: pass
            
            try:
                cursor.execute("ALTER TABLE purchases ADD COLUMN updated_at TEXT")
                cursor.execute("UPDATE purchases SET updated_at = datetime WHERE updated_at IS NULL")
            except Exception: pass

            # 5. PV Bot columns in receipts
            try:
                cursor.execute("ALTER TABLE receipts ADD COLUMN pv_bot_status INTEGER DEFAULT 0")
            except Exception: pass
            try:
                cursor.execute("ALTER TABLE receipts ADD COLUMN pv_bot_date TEXT")
            except Exception: pass
            try:
                cursor.execute("ALTER TABLE receipts ADD COLUMN pv_bot_error TEXT")
            except Exception: pass

            # === DEEP SYNC MIGRATION (v3.9.35) ===
            # Force all existing data to be re-synced once to ensure mutual catalog is complete
            try:
                cursor.execute("SELECT marker_value FROM sync_markers WHERE marker_key = 'deep_sync_v35'")
                if not cursor.fetchone():
                    print("🔄 Running Deep Sync Migration (v3.9.35)...")
                    # 1. Reset 'synced' flags to force PUSH
                    cursor.execute("UPDATE goods SET synced = 0")
                    cursor.execute("UPDATE partners SET synced = 0")
                    cursor.execute("UPDATE receipts SET synced = 0")
                    
                    # 2. Reset pull markers to force PULL
                    cursor.execute("DELETE FROM sync_markers WHERE marker_key LIKE 'last_%_sync'")
                    
                    # 3. Mark as done
                    cursor.execute("INSERT OR REPLACE INTO sync_markers (marker_key, marker_value) VALUES ('deep_sync_v35', 'done')")
                    print("✅ Deep Sync Migration complete. Data will merge on next restart.")
            except Exception as e:
                print(f"⚠️ Deep Sync Migration failed: {e}")

            conn.commit()
            
        except Exception as e:
            print(f"Schema migration error: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
            # C1: do not swallow migration failures. The caller backs the DB
            # up and exits with a clear error instead of destroying data.
            raise
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def mark_receipt_live_sent(self, receipt_id, status=1, processed_items=None, error=None):
        """
        Mark a receipt as processed by Live PV Bot.
        status: 1=success, 2=partial, -1=failed
        processed_items: list of good_codes that were successfully processed
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Update receipt main status and timestamp
            if error:
                cursor.execute('''
                    UPDATE receipts SET live_sent = -1, live_error = ?, 
                    live_status = -1, live_processed_at = ? WHERE id = ?
                ''', (error, now, receipt_id))
            else:
                cursor.execute('''
                    UPDATE receipts SET live_sent = 1, live_status = ?, 
                    live_processed_at = ? WHERE id = ?
                ''', (status, now, receipt_id))
            
            # Update individual items if provided
            if processed_items:
                for code in processed_items:
                    cursor.execute('''
                        UPDATE receipt_items SET live_status = 1 
                        WHERE receipt_id = ? AND good_code = ?
                    ''', (receipt_id, code))

    def log_cancelled_item(self, good_code, name, quantity, action, cashier=""):
        """Log a cancelled item to the database."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO cancelled_items (good_code, name, quantity, action, cashier, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (good_code, name, quantity, action, cashier, datetime.now().isoformat()))
    
    def get_cancelled_items(self, limit=100, offset=0, search_query="", action_filter="", date_from=None, date_to=None):
        """Get cancelled items from the database with optional search, action type, and date filters."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Base query
            query = 'SELECT * FROM cancelled_items'
            params = []
            conditions = []
            
            if search_query:
                q = f'%{search_query.lower()}%'
                conditions.append('(LOWER(good_code) LIKE ? OR LOWER(name) LIKE ? OR LOWER(cashier) LIKE ?)')
                params.extend([q, q, q])
            
            if action_filter:
                conditions.append('action = ?')
                params.append(action_filter)
            
            if date_from:
                conditions.append('timestamp >= ?')
                params.append(date_from)
            
            if date_to:
                conditions.append('timestamp <= ?')
                params.append(date_to)
            
            if conditions:
                query += ' WHERE ' + ' AND '.join(conditions)
                
            query += ' ORDER BY timestamp DESC LIMIT ? OFFSET ?'
            params.extend([limit, offset])
            
            cursor.execute(query, params)
            items = [dict(row) for row in cursor.fetchall()]
            
            # Count total for pagination
            count_query = 'SELECT COUNT(*) as total FROM cancelled_items'
            count_params = []
            count_conditions = []
            
            if search_query:
                q = f'%{search_query.lower()}%'
                count_conditions.append('(LOWER(good_code) LIKE ? OR LOWER(name) LIKE ? OR LOWER(cashier) LIKE ?)')
                count_params.extend([q, q, q])
            
            if action_filter:
                count_conditions.append('action = ?')
                count_params.append(action_filter)
            
            if date_from:
                count_conditions.append('timestamp >= ?')
                count_params.append(date_from)
            
            if date_to:
                count_conditions.append('timestamp <= ?')
                count_params.append(date_to)
            
            if count_conditions:
                count_query += ' WHERE ' + ' AND '.join(count_conditions)
                
            cursor.execute(count_query, count_params)
            total = cursor.fetchone()['total']
            
            return items, total

    # =========================================================================
    # DATABASE MANAGEMENT: Statistics, Export, Import, Backup
    # =========================================================================

    # Tables and their user-facing labels + related sub-tables
    EXPORTABLE_SECTIONS = {
        'goods':       {'label': 'Товары',             'tables': ['goods']},
        'partners':    {'label': 'Партнёры',           'tables': ['partners', 'partners_history']},
        'receipts':    {'label': 'Чеки',               'tables': ['receipts', 'receipt_items', 'receipt_refund_logs']},
        'purchases':   {'label': 'Накладные',           'tables': ['purchases', 'purchase_items']},
        'writeoffs':   {'label': 'Списания',            'tables': ['writeoffs', 'writeoff_items']},
        'audits':      {'label': 'Ревизии',             'tables': ['inventory_audits', 'inventory_audit_items']},
        'cancelled':   {'label': 'Отмены',              'tables': ['cancelled_items']},
        'quick_items': {'label': 'Быстрые товары',      'tables': ['quick_items']},
        'users':       {'label': 'Пользователи',        'tables': ['app_users']},
        'sync':        {'label': 'Маркеры синхронизации','tables': ['sync_markers']},
    }

    def get_database_statistics(self):
        """Return a dict with counts for every table and the DB file size."""
        stats = {}
        tables = [
            ('goods',                'Товары'),
            ('partners',             'Партнёры'),
            ('receipts',             'Чеки'),
            ('receipt_items',        'Позиции чеков'),
            ('purchases',            'Накладные'),
            ('purchase_items',       'Позиции накладных'),
            ('writeoffs',            'Списания'),
            ('writeoff_items',       'Позиции списаний'),
            ('inventory_audits',     'Ревизии'),
            ('inventory_audit_items','Позиции ревизий'),
            ('cancelled_items',      'Отмены'),
            ('partners_history',     'История партнёров'),
            ('receipt_refund_logs',  'Возвраты'),
            ('quick_items',          'Быстрые товары'),
            ('app_users',            'Пользователи'),
            ('sync_markers',         'Маркеры синхр.'),
            ('autoreview_sessions',  'Автоскладирование'),
        ]
        with self.get_connection() as conn:
            cursor = conn.cursor()
            for table_name, label in tables:
                try:
                    cursor.execute(f"SELECT COUNT(*) as cnt FROM {table_name}")
                    count = cursor.fetchone()['cnt']
                except Exception:
                    count = 0
                stats[table_name] = {'label': label, 'count': count}

        # DB file size
        try:
            size_bytes = os.path.getsize(self.db_path)
        except Exception:
            size_bytes = 0
        stats['_file_size'] = size_bytes
        return stats

    def create_backup(self, backup_path):
        """Create a full copy of the current database file.
        
        Uses SQLite's built-in backup API for a safe, consistent copy
        even while the database is in use.
        Returns: (success: bool, message: str)
        """
        import shutil
        try:
            # Use a fresh independent connection instead of thread-local pool
            src_conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=60)
            src_conn.execute("PRAGMA busy_timeout=60000")
            dst_conn = sqlite3.connect(backup_path)
            src_conn.backup(dst_conn)
            # Ensure the backup file is not in WAL mode
            dst_conn.execute("PRAGMA journal_mode=DELETE")
            dst_conn.close()
            src_conn.close()
            return True, backup_path
        except Exception as e:
            # Fallback to file copy
            try:
                shutil.copy2(self.db_path, backup_path)
                return True, backup_path
            except Exception as e2:
                return False, f"Backup failed: {e2}"

    def export_database(self, export_path, sections, settings_data=None, progress_callback=None):
        """Export selected sections to a .pvmbackup file (gzip-compressed SQLite).
        
        Args:
            export_path: Path to save the .pvmbackup file
            sections: List of section keys (e.g. ['goods', 'receipts', ...])
            settings_data: Optional dict of application settings to embed
            progress_callback: Optional callable(current_step, total_steps, message)
        
        Returns: (success: bool, message: str)
        """
        import gzip
        import tempfile
        
        # Determine which tables to export
        tables_to_export = set()
        for section in sections:
            info = self.EXPORTABLE_SECTIONS.get(section, {})
            for t in info.get('tables', []):
                tables_to_export.add(t)
        
        if not tables_to_export:
            return False, "Нет данных для экспорта"
        
        total_steps = len(tables_to_export) + 3  # tables + metadata + compress + finalize
        step = [0]
        
        def _progress(msg):
            step[0] += 1
            if progress_callback:
                progress_callback(step[0], total_steps, msg)
        
        try:
            # 1. Create a snapshot of the live DB using a fresh independent connection
            #    This avoids "database is locked" from the app's WAL connections
            snap_fd, snap_path = tempfile.mkstemp(suffix='_snap.db')
            os.close(snap_fd)
            
            _progress("Создание снимка базы...")
            # Open a NEW read-only connection independent of the thread-local pool
            src_conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=60)
            src_conn.execute("PRAGMA busy_timeout=60000")
            snap_conn = sqlite3.connect(snap_path)
            src_conn.backup(snap_conn)
            # Force snapshot out of WAL mode to prevent any locking when attached
            snap_conn.execute("PRAGMA journal_mode=DELETE")
            snap_conn.close()
            src_conn.close()
            
            # 2. Create temporary SQLite database for selective export
            tmp_fd, tmp_path = tempfile.mkstemp(suffix='.db')
            os.close(tmp_fd)
            
            tmp_conn = sqlite3.connect(tmp_path)
            # DO NOT USE WAL here! The gzip at the end only reads the main .db file,
            # so data in -wal would be lost. Plus, WAL can cause ATTACH locking bugs.
            tmp_conn.execute("PRAGMA journal_mode=DELETE")
            tmp_conn.execute("PRAGMA synchronous=OFF")
            tmp_cursor = tmp_conn.cursor()
            
            # 3. Copy tables from snapshot manually (no ATTACH to avoid any SQLite locking issues)
            snap_conn_read = sqlite3.connect(snap_path)
            snap_cursor = snap_conn_read.cursor()
            
            for table in tables_to_export:
                _progress(f"Экспорт: {table}...")
                try:
                    # Get CREATE TABLE statement from snapshot
                    snap_cursor.execute(
                        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                        (table,))
                    row = snap_cursor.fetchone()
                    if row and row[0]:
                        # Create table in export DB
                        tmp_cursor.execute(row[0])
                        # Copy all data iteratively in batches
                        snap_cursor.execute(f"SELECT * FROM {table}")
                        while True:
                            rows = snap_cursor.fetchmany(10000)
                            if not rows:
                                break
                            placeholders = ",".join(["?"] * len(rows[0]))
                            tmp_cursor.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
                except Exception as e:
                    print(f"⚠️ Export skipped table {table}: {e}")
            
            snap_conn_read.close()
            
            # 3. Store metadata + settings in a special table
            _progress("Сохранение метаданных...")
            tmp_cursor.execute('''
                CREATE TABLE IF NOT EXISTS _pvmbackup_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            
            meta = {
                'version': '1.0',
                'created_at': datetime.now().isoformat(),
                'device_key': '',
                'device_name': '',
                'sections': json.dumps(sections),
                'tables': json.dumps(list(tables_to_export)),
            }
            
            # Include device info
            try:
                import settings as s
                meta['device_key'] = s.get_or_create_device_key()
                meta['device_name'] = s.get_sync_settings().get('sync_name', '')
            except Exception:
                pass
            
            # Store app settings if provided
            if settings_data:
                meta['app_settings'] = json.dumps(settings_data, ensure_ascii=False)
            
            for k, v in meta.items():
                tmp_cursor.execute(
                    "INSERT OR REPLACE INTO _pvmbackup_meta (key, value) VALUES (?, ?)",
                    (k, str(v)))
            
            tmp_conn.commit()
            tmp_conn.close()
            
            # 4. Compress the temporary DB to .pvmbackup (gzip)
            _progress("Сжатие файла...")
            with open(tmp_path, 'rb') as f_in:
                with gzip.open(export_path, 'wb', compresslevel=6) as f_out:
                    while True:
                        chunk = f_in.read(1024 * 1024)  # 1MB chunks
                        if not chunk:
                            break
                        f_out.write(chunk)
            
            # Cleanup temp files
            for p in (tmp_path, snap_path):
                try:
                    os.remove(p)
                except Exception:
                    pass
            
            # Final size
            export_size = os.path.getsize(export_path)
            size_str = self._format_size(export_size)
            
            return True, f"Экспорт завершён! Размер: {size_str}"
        
        except Exception as e:
            # Cleanup on error
            for p in (tmp_path, snap_path):
                try:
                    os.remove(p)
                except Exception:
                    pass
            return False, f"Ошибка экспорта: {e}"

    def read_backup_info(self, backup_path):
        """Read metadata and statistics from a .pvmbackup file without importing.
        
        Returns: (success: bool, info: dict or error_message: str)
        """
        import gzip
        import tempfile
        
        try:
            # 1. Decompress to temp file
            tmp_fd, tmp_path = tempfile.mkstemp(suffix='.db')
            os.close(tmp_fd)
            
            with gzip.open(backup_path, 'rb') as f_in:
                with open(tmp_path, 'wb') as f_out:
                    while True:
                        chunk = f_in.read(1024 * 1024)
                        if not chunk:
                            break
                        f_out.write(chunk)
            
            # 2. Read metadata
            tmp_conn = sqlite3.connect(tmp_path)
            tmp_conn.row_factory = sqlite3.Row
            cursor = tmp_conn.cursor()
            
            info = {
                'meta': {},
                'stats': {},
                'sections': [],
                'tables': [],
                'file_size': os.path.getsize(backup_path),
                'uncompressed_size': os.path.getsize(tmp_path),
            }
            
            # Read metadata table
            try:
                cursor.execute("SELECT key, value FROM _pvmbackup_meta")
                for row in cursor.fetchall():
                    info['meta'][row['key']] = row['value']
                
                info['sections'] = json.loads(info['meta'].get('sections', '[]'))
                info['tables'] = json.loads(info['meta'].get('tables', '[]'))
            except Exception:
                # Old format or no metadata — scan tables directly
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != '_pvmbackup_meta'")
                info['tables'] = [row['name'] for row in cursor.fetchall()]
                # Guess sections from tables
                for section, sinfo in self.EXPORTABLE_SECTIONS.items():
                    if any(t in info['tables'] for t in sinfo['tables']):
                        info['sections'].append(section)
            
            # Count records in each table
            for table in info['tables']:
                try:
                    cursor.execute(f"SELECT COUNT(*) as cnt FROM {table}")
                    info['stats'][table] = cursor.fetchone()['cnt']
                except Exception:
                    info['stats'][table] = 0
            
            tmp_conn.close()
            
            # Cleanup
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            
            return True, info
        
        except Exception as e:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            return False, f"Ошибка чтения файла: {e}"

    def import_database(self, backup_path, sections, progress_callback=None):
        """Import selected sections from a .pvmbackup file.
        
        DESTRUCTIVE: Deletes existing data in the selected sections first!
        
        Args:
            backup_path: Path to .pvmbackup file
            sections: List of section keys to import
            progress_callback: Optional callable(current_step, total_steps, message)
        
        Returns: (success: bool, message: str, imported_settings: dict or None)
        """
        import gzip
        import tempfile
        
        # Determine which tables to import
        tables_to_import = []
        for section in sections:
            info = self.EXPORTABLE_SECTIONS.get(section, {})
            for t in info.get('tables', []):
                tables_to_import.append(t)
        
        if not tables_to_import:
            return False, "Нет данных для импорта", None
        
        total_steps = len(tables_to_import) * 2 + 4  # decompress + delete + insert each + finalize
        step = [0]
        
        def _progress(msg):
            step[0] += 1
            if progress_callback:
                progress_callback(step[0], total_steps, msg)
        
        try:
            # 1. Decompress backup
            _progress("Распаковка файла...")
            tmp_fd, tmp_path = tempfile.mkstemp(suffix='.db')
            os.close(tmp_fd)
            
            with gzip.open(backup_path, 'rb') as f_in:
                with open(tmp_path, 'wb') as f_out:
                    while True:
                        chunk = f_in.read(1024 * 1024)
                        if not chunk:
                            break
                        f_out.write(chunk)
            
            # 2. Validate backup
            _progress("Валидация файла...")
            tmp_conn = sqlite3.connect(tmp_path)
            tmp_conn.row_factory = sqlite3.Row
            tmp_cursor = tmp_conn.cursor()
            
            # Check which tables exist in backup
            tmp_cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            backup_tables = {row['name'] for row in tmp_cursor.fetchall()}
            
            # Read settings from meta if present
            imported_settings = None
            try:
                tmp_cursor.execute("SELECT value FROM _pvmbackup_meta WHERE key='app_settings'")
                row = tmp_cursor.fetchone()
                if row and row['value']:
                    imported_settings = json.loads(row['value'])
            except Exception:
                pass
            
            tmp_conn.close()
            
            # 3. Import data manually (no ATTACH to avoid locking issues)
            #    Use a RAW connection with FK OFF to allow DELETE/INSERT without constraint issues
            _progress("Подготовка к импорту...")
            conn = sqlite3.connect(self.db_path, timeout=60)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("PRAGMA journal_mode = WAL")
            cursor = conn.cursor()
            
            backup_conn = sqlite3.connect(tmp_path)
            backup_conn.row_factory = sqlite3.Row
            backup_cursor = backup_conn.cursor()
            
            imported_counts = {}
            
            # Process tables in dependency order (delete children first, insert parents first)
            # Reverse for delete, forward for insert
            delete_order = list(reversed(tables_to_import))
            insert_order = list(tables_to_import)
            
            # DELETE existing data
            for table in delete_order:
                _progress(f"Очистка: {table}...")
                try:
                    cursor.execute(f"DELETE FROM main.{table}")
                except Exception as e:
                    print(f"⚠️ Could not clear {table}: {e}")
            
            # INSERT from backup
            for table in insert_order:
                _progress(f"Импорт: {table}...")
                if table not in backup_tables:
                    print(f"⚠️ Table {table} not found in backup, skipping")
                    continue
                try:
                    # Get columns from backup table
                    backup_cursor.execute(f"PRAGMA table_info({table})")
                    backup_cols = [row['name'] for row in backup_cursor.fetchall()]
                    
                    # Get columns from main table
                    tmp_cursor2 = conn.cursor()
                    tmp_cursor2.execute(f"PRAGMA table_info({table})")
                    main_cols = [row['name'] for row in tmp_cursor2.fetchall()]
                    
                    # Use intersection of columns (handles schema differences)
                    common_cols = [c for c in backup_cols if c in main_cols]
                    if not common_cols:
                        print(f"⚠️ No matching columns for {table}, skipping")
                        continue
                    
                    cols_str = ', '.join(common_cols)
                    placeholders = ', '.join(['?'] * len(common_cols))
                    
                    # Read rows from backup and insert/replace
                    backup_cursor.execute(f"SELECT {cols_str} FROM {table}")
                    
                    row_count = 0
                    while True:
                        rows = backup_cursor.fetchmany(10000)
                        if not rows:
                            break
                        # Convert Row objects to tuples for parameterized insertion
                        values = [tuple(r) for r in rows]
                        cursor.executemany(f"INSERT OR REPLACE INTO {table} ({cols_str}) VALUES ({placeholders})", values)
                        row_count += len(values)
                        
                    imported_counts[table] = row_count
                except Exception as e:
                    print(f"⚠️ Import error for {table}: {e}")
                    imported_counts[table] = -1
            
            backup_conn.close()
            conn.commit()
            conn.execute("PRAGMA foreign_keys = ON")
            conn.close()
            
            # Cleanup temp file
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            
            # Build summary
            summary_parts = []
            for table, count in imported_counts.items():
                if count > 0:
                    summary_parts.append(f"{table}: {count}")
            
            summary = f"Импортировано: {', '.join(summary_parts)}" if summary_parts else "Импорт завершён"
            return True, summary, imported_settings
        
        except Exception as e:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            return False, f"Ошибка импорта: {e}", None

    def clear_database(self, sections):
        """Clear data in specified sections."""
        tables_to_clear = []
        for s in sections:
            info = self.EXPORTABLE_SECTIONS.get(s)
            if info:
                tables_to_clear.extend(info['tables'])
        
        if not tables_to_clear:
            return False, "Не выбраны данные для удаления"
            
        try:
            conn = self._get_thread_conn()
            conn.execute("PRAGMA foreign_keys = OFF")
            cursor = conn.cursor()
            
            cleared_counts = {}
            for table in tables_to_clear:
                cursor.execute(f"DELETE FROM {table}")
                cleared_counts[table] = cursor.rowcount
                
            conn.commit()
            conn.execute("PRAGMA foreign_keys = ON")
            
            # --- RESET SYNC MARKERS FOR WIPED SECTIONS ---
            # This ensures that after a local wipe, the node re-pulls the
            # wiped sections from the sync folder on the next cycle.
            try:
                marker_map = {
                    'goods':     ['last_goods_sync'],
                    'partners':  ['last_partners_sync'],
                    'receipts':  ['last_receipts_sync'],
                    'purchases': ['last_purchases_sync'],
                    'writeoffs': ['last_writeoffs_sync'],
                    'audits':    ['last_audits_sync', 'last_audits_push'],
                    'sync':      ['last_goods_sync', 'last_partners_sync', 'last_receipts_sync', 
                                  'last_purchases_sync', 'last_writeoffs_sync', 'last_audits_sync', 
                                  'last_audits_push']
                }
                markers_to_reset = []
                for s in sections:
                    markers_to_reset.extend(marker_map.get(s, []))
                
                if markers_to_reset:
                    cursor.execute(f"DELETE FROM sync_markers WHERE marker_key IN ({','.join(['?']*len(markers_to_reset))})", markers_to_reset)
                    conn.commit()
            except Exception as me:
                print(f"⚠️ Failed to reset sync markers after wipe: {me}")
            # ---------------------------------------------
            
            summary = ", ".join([f"{t}: {c}" for t, c in cleared_counts.items() if c > 0])
            if not summary:
                summary = "0 строк удалено"
                
            return True, f"Успешно удалено:\n{summary}"
        except Exception as e:
            return False, f"Ошибка при очистке: {e}"
    
    @staticmethod
    def _format_size(size_bytes):
        """Format bytes into a human-readable string."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


    # =========================================================================
    # SYNC ENGINE HELPERS
    # =========================================================================
    def get_pending_changes(self, limit: int = 500) -> list[dict]:
        """Get unsynced changes from sync_log, ordered by creation time."""
        with self.get_connection() as conn:
            rows = conn.execute('''
                SELECT id, entity_type, entity_id, operation, data, created_at
                FROM sync_log
                WHERE synced = 0
                ORDER BY id ASC
                LIMIT ?
            ''', (limit,)).fetchall()
            return [dict(r) for r in rows]

    def mark_changes_synced(self, change_ids: list[int]) -> None:
        """Mark sync_log entries as synced."""
        if not change_ids:
            return
        with self.get_connection() as conn:
            placeholders = ','.join('?' * len(change_ids))
            conn.execute(f'UPDATE sync_log SET synced = 1 WHERE id IN ({placeholders})', change_ids)

    def get_sync_marker(self, key: str) -> str | None:
        with self.get_connection() as conn:
            row = conn.execute('SELECT marker_value FROM sync_markers WHERE marker_key = ?', (key,)).fetchone()
            return row['marker_value'] if row else None

    def set_sync_marker(self, key: str, value: str) -> None:
        with self.get_connection() as conn:
            conn.execute('''
                INSERT INTO sync_markers (marker_key, marker_value)
                VALUES (?, ?)
                ON CONFLICT(marker_key) DO UPDATE SET marker_value = excluded.marker_value
            ''', (key, value))


# =============================================================================
# GOODS MANAGER
# =============================================================================
class GoodsManagerSQL:
    """Manage goods using SQLite database."""
    
    def __init__(self, db_manager):
        self.db = db_manager

    def get_good(self, code):
        """Find good by code. Returns (id, dict) or (None, None)."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM goods WHERE code = ?', (code,))
            row = cursor.fetchone()
            if row:
                return row['id'], dict(row)
            return None, None

    def get_unsynced_goods(self):
        """Get goods that haven't been pushed to master yet."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM goods WHERE synced = 0')
            return [dict(row) for row in cursor.fetchall()]

    def mark_goods_synced(self, codes):
        """Mark a list of goods as synced."""
        if not codes: return
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            # Batch update
            placeholders = ','.join(['?'] * len(codes))
            cursor.execute(f'UPDATE goods SET synced = 1 WHERE code IN ({placeholders})', tuple(codes))
            conn.commit()

    def add_good_from_dict(self, data, merge_stock=False, preserve_quantity=False):
        """UPSERT good from a dictionary (typically from sync).

        preserve_quantity=True → only update name/prices/barcode/pv/is_deleted;
        the local quantity column is left untouched. This is the correct mode
        for applying warehouse-originated goods EDITS received through the cloud
        relay: the warehouse does not own goods.quantity (the cashier PC does),
        so an edit arriving from the warehouse must NOT clobber the cashier's
        current stock. See sync plan Phase 5.3.
        """
        return self.add_good(
            code=data.get('code'),
            name=data.get('name'),
            pv=data.get('pv', 0),
            purchase_price=data.get('purchase_price', 0),
            sale_price=data.get('sale_price', 0),
            quantity=data.get('quantity', 0),
            barcode=data.get('barcode', ''),
            set_quantity=not merge_stock, # If merge_stock, use additive update
            preserve_quantity=preserve_quantity,
            updated_at=data.get('updated_at'),
            synced=1,
            is_deleted=data.get('is_deleted'),
        )

    def add_good(self, code, name, pv, purchase_price, sale_price, quantity=0, barcode="", set_quantity=False, reason="", **kwargs):
        """Add or update good. If set_quantity=True, overwrite instead of adding."""
        quantity = quantity if quantity is not None else 0
        pv = pv if pv is not None else 0
        purchase_price = purchase_price if purchase_price is not None else 0
        sale_price = sale_price if sale_price is not None else 0
        # Prices are whole numbers only (единая логика целых цен)
        try:
            purchase_price = int(round(float(purchase_price)))
        except (ValueError, TypeError):
            purchase_price = 0
        try:
            sale_price = int(round(float(sale_price)))
        except (ValueError, TypeError):
            sale_price = 0
        old_code = kwargs.get('old_code')
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            now = kwargs.get('updated_at', datetime.now().isoformat())
            preserve_qty = bool(kwargs.get('preserve_quantity', False))
            is_deleted_in = kwargs.get('is_deleted')

            if old_code and old_code != code:
                # Rename (edit dialog changed the code): operate on the row
                # identified by old_code instead of inserting a duplicate.
                cursor.execute('SELECT * FROM goods WHERE code = ?', (old_code,))
                existing = cursor.fetchone()
                if existing:
                    cursor.execute('SELECT code FROM goods WHERE code = ?', (code,))
                    if cursor.fetchone():
                        # Target code is taken by another good - abort without
                        # touching any data.
                        return False
                    if preserve_qty and is_deleted_in is not None:
                        cursor.execute('''
                            UPDATE goods
                            SET code = ?, name = ?, pv = ?, barcode = ?, purchase_price = ?,
                                sale_price = ?, is_deleted = ?, updated_at = ?
                            WHERE code = ?
                        ''', (code, name, pv, barcode, purchase_price, sale_price,
                              int(is_deleted_in), now, old_code))
                    elif preserve_qty:
                        cursor.execute('''
                            UPDATE goods
                            SET code = ?, name = ?, pv = ?, barcode = ?, purchase_price = ?,
                                sale_price = ?, updated_at = ?
                            WHERE code = ?
                        ''', (code, name, pv, barcode, purchase_price, sale_price, now, old_code))
                    elif set_quantity:
                        cursor.execute('''
                            UPDATE goods
                            SET code = ?, name = ?, pv = ?, barcode = ?, purchase_price = ?,
                                sale_price = ?, quantity = ?, updated_at = ?
                            WHERE code = ?
                        ''', (code, name, pv, barcode, purchase_price, sale_price,
                              quantity, now, old_code))
                    else:
                        cursor.execute('''
                            UPDATE goods
                            SET code = ?, name = ?, pv = ?, barcode = ?, purchase_price = ?,
                                sale_price = ?, quantity = quantity + ?, updated_at = ?
                            WHERE code = ?
                        ''', (code, name, pv, barcode, purchase_price, sale_price,
                              quantity, now, old_code))
                    for table, column in (('receipt_items', 'good_code'),
                                          ('cancelled_items', 'good_code'),
                                          ('writeoff_items', 'good_code'),
                                          ('quick_items', 'good_code'),
                                          ('inventory_audit_items', 'good_code'),
                                          ('purchase_items', 'code')):
                        try:
                            cursor.execute(f'UPDATE {table} SET {column} = ? WHERE {column} = ?',
                                           (code, old_code))
                        except Exception:
                            pass
                else:
                    good_id = hashlib.md5(f"{code}{name}{barcode}".encode()).hexdigest()[:12]
                    cursor.execute('''
                        INSERT INTO goods (id, code, name, pv, barcode, purchase_price, sale_price,
                                          quantity, created_at, updated_at, is_deleted)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    ''', (good_id, code, name, pv, barcode, purchase_price, sale_price,
                          quantity, now, now))
                return True

            cursor.execute('SELECT * FROM goods WHERE code = ?', (code,))
            existing = cursor.fetchone()
            
            if existing:
                try:
                    _cat_changed = (
                        (existing['name'] or '') != (name or '') or
                        (existing['barcode'] or '') != (barcode or '') or
                        float(existing['pv'] or 0) != float(pv or 0) or
                        float(existing['purchase_price'] or 0) != float(purchase_price or 0) or
                        float(existing['sale_price'] or 0) != float(sale_price or 0)
                    )
                except (ValueError, TypeError):
                    _cat_changed = True
                _upd_ts = now if _cat_changed else (existing['updated_at'] or now)
                if set_quantity and not preserve_qty:
                    # Overwrite (used during sync from master)
                    cursor.execute('''
                        UPDATE goods
                        SET name = ?, pv = ?, barcode = ?, purchase_price = ?, sale_price = ?,
                            quantity = ?, updated_at = ?
                        WHERE code = ?
                    ''', (name, pv, barcode, purchase_price, sale_price, quantity, _upd_ts, code))
                elif preserve_qty:
                    # Warehouse-edit Mode (Phase 5.3): preserve cashier's authoritative
                    # quantity, only update catalog fields + soft-delete state.
                    if is_deleted_in is not None:
                        cursor.execute('''
                            UPDATE goods
                            SET name = ?, pv = ?, barcode = ?, purchase_price = ?, sale_price = ?,
                                is_deleted = ?, updated_at = ?
                            WHERE code = ?
                        ''', (name, pv, barcode, purchase_price, sale_price, int(is_deleted_in), now, code))
                    else:
                        cursor.execute('''
                            UPDATE goods
                            SET name = ?, pv = ?, barcode = ?, purchase_price = ?, sale_price = ?,
                                updated_at = ?
                            WHERE code = ?
                        ''', (name, pv, barcode, purchase_price, sale_price, now, code))
                else:
                    # Additive (used during arrival/purchase)
                    cursor.execute('''
                        UPDATE goods
                        SET name = ?, pv = ?, barcode = ?, purchase_price = ?, sale_price = ?,
                            quantity = quantity + ?, updated_at = ?
                        WHERE code = ?
                    ''', (name, pv, barcode, purchase_price, sale_price, quantity, _upd_ts, code))
            else:
                good_id = hashlib.md5(f"{code}{name}{barcode}".encode()).hexdigest()[:12]
                cursor.execute('''
                    INSERT INTO goods (id, code, name, pv, barcode, purchase_price, sale_price,
                                      quantity, created_at, updated_at, is_deleted)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                ''', (good_id, code, name, pv, barcode, purchase_price, sale_price,
                      quantity, now, now))
        
        return True
    
    def update_quantity(self, code, qty_change, user_name="System", reason="", action_name="Обновление кол-ва"):
        """Update good quantity by delta."""
        if qty_change is None:
            return False
            
        success = False
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE goods SET quantity = quantity + ? WHERE code = ?
            ''', (qty_change, code))
            
            if cursor.rowcount > 0:
                return True
            
        return False
            
        return False
    
    def get_good_by_barcode(self, barcode):
        """Find good by barcode."""
        if not barcode:
            return None, None
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM goods WHERE barcode = ?', (barcode,))
            row = cursor.fetchone()
            if row:
                return row['id'], dict(row)
            return None, None
    
    def search_goods(self, query):
        """Search goods by code, name, or barcode with improved Cyrillic/space handling."""
        # Normalize spaces and lower case
        clean_query = ' '.join(query.split()).lower()
        q = f'%{clean_query}%'
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM goods
                WHERE (LOWER(code) LIKE ? OR LOWER(name) LIKE ? OR LOWER(barcode) LIKE ?)
                AND is_deleted = 0
            ''', (q, q, q))
            return [dict(row) for row in cursor.fetchall()]
    
    def get_all_goods(self):
        """Get all goods sorted by name."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM goods WHERE is_deleted = 0 ORDER BY name')
            return [dict(row) for row in cursor.fetchall()]

    def delete_good(self, code):
        """Delete good by code (soft delete, syncs to other devices)."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE goods SET is_deleted = 1, synced = 0, updated_at = ? WHERE code = ?', 
                           (datetime.now().isoformat(), code))
            return cursor.rowcount > 0

    def clear_goods_cache(self):
        """Wipe local goods data and sync markers to trigger a fresh full sync from Master."""
        with self.db.get_connection() as conn:
            # Disable FK to allow wiping goods even if linked to receipts
            conn.execute("PRAGMA foreign_keys = OFF")
            cursor = conn.cursor()
            # Clear goods data
            cursor.execute('DELETE FROM goods')
            cursor.execute('DELETE FROM quick_items')
            # Clear sync markers related to goods
            cursor.execute("DELETE FROM sync_markers WHERE marker_key LIKE '%pull_goods%'")
            # Initialize 20 quick item slots
            for i in range(20):
                cursor.execute(
                    'INSERT OR IGNORE INTO quick_items (slot_index, good_code, item_data) VALUES (?, NULL, NULL)',
                    (i,))
            conn.commit()
            conn.execute("PRAGMA foreign_keys = ON")
        return True


# =============================================================================
# PARTNERS MANAGER
# =============================================================================
class PartnersManagerSQL:
    """Manage partners using SQLite database."""
    
    PARTNER_DISCOUNT = 0.5
    
    def __init__(self, db_manager):
        self.db = db_manager
    
    def parse_partner_name(self, name):
        """Extract ID and clean name from string like 'kz12345678 John Doe'."""
        if not name:
            return None, ""
        
        import re
        # Match 2-letter prefix followed by exactly 8 digits
        match = re.search(r'([a-zA-Z]{2}\d{8})\b', name)
        if match:
            extracted_id = match.group(1).lower()
            # Clean name is everything except the ID
            clean_name = name.replace(match.group(1), "").strip()
            # Also handle if name was "kz12345678 - Name"
            clean_name = re.sub(r'^[-\s:]+', '', clean_name)
            return extracted_id, clean_name
        
        return None, name.strip()
    
    def add_partner(self, name=None, phone="", email="", notes="", partner_id=None, user_name="System", skip_sync_log=False, **kwargs):
        """Add new partner with optional or auto-generated ID.
        
        Если партнер с таким ID уже существует:
        - synced=0 (локальное создание) -> raise ValueError
        - synced=1 (синхронизация) -> UPDATE (UPSERT)
        """
        import uuid
        
        if name is None:
            name = kwargs.get('name_with_id', '')
        
        display_name = (name or '').strip()
        if not display_name and not partner_id:
            return False
            
        extracted_id, clean_name = self.parse_partner_name(name)
        
        final_id = partner_id or extracted_id or str(uuid.uuid4())
        display_name = f"{extracted_id} {clean_name}" if extracted_id else name.strip()
        
        synced_status = kwargs.get('synced', 1 if partner_id else 0)
        is_blocked = kwargs.get('is_blocked', 0)
        dob = kwargs.get('dob', None)
        discount = kwargs.get('discount', self.PARTNER_DISCOUNT)
        full_name = kwargs.get('full_name', display_name)
        now_ts = kwargs.get('updated_at') or datetime.now().isoformat()
        
        old_data = self.get_partner(final_id)
        
        if old_data:
            if synced_status == 0:
                raise ValueError(f"Партнер с ID '{final_id}' уже существует")
            # Sync mode: UPDATE существующего
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE partners SET name=?, full_name=?, phone=?, email=?, notes=?,
                    discount=?, is_blocked=?, dob=?, block_reason=?, updated_at=?, synced=1
                    WHERE id=?
                ''', (display_name, full_name, phone, email, notes,
                      discount, is_blocked, dob, kwargs.get('block_reason', ''), now_ts, final_id))
            if not skip_sync_log:
                self.db.sync_log.log('partner', final_id, 'update', {
                    'id': final_id, 'name': display_name, 'phone': phone,
                    'email': email, 'notes': notes, 'discount': discount,
                    'is_blocked': is_blocked
                })
            self.add_history_record(final_id, "Updated", {
                'name': {'old': old_data.get('name',''), 'new': display_name},
                'is_blocked': {'old': old_data.get('is_blocked',0), 'new': is_blocked}
            }, user_name)
            return final_id
        
        # NEW partner: INSERT
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO partners
                (id, name, full_name, phone, email, notes, discount, created_at, updated_at, synced, dob, is_blocked, block_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (final_id, display_name, full_name, phone, email, notes,
                  discount, now_ts, now_ts, synced_status, dob, is_blocked, kwargs.get('block_reason', '')))
            
        self.add_history_record(final_id, "Created", {"name": display_name, "phone": phone}, user_name)

        if not skip_sync_log:
            self.db.sync_log.log('partner', final_id, 'create', {
                'id': final_id, 'name': display_name, 'phone': phone,
                'email': email, 'notes': notes, 'discount': discount,
                'is_blocked': is_blocked
            })
            
        return final_id

    def update_partner(self, partner_id, name, phone, email, notes, user_name="System", **kwargs):
        """Update existing partner and log changes. Supports PK change."""
        old_data = self.get_partner(partner_id)
        if not old_data:
            return False
            
        new_id = kwargs.get('new_id', partner_id)
        is_blocked = kwargs.get('is_blocked', old_data.get('is_blocked', 0))
        block_reason = kwargs.get('block_reason', old_data.get('block_reason', ''))
        blocked_by = kwargs.get('blocked_by', old_data.get('blocked_by', ''))
        blocked_at = kwargs.get('blocked_at', old_data.get('blocked_at', ''))
        dob = kwargs.get('dob', old_data.get('dob'))

        new_data = {
            'id': new_id,
            'name': name,
            'full_name': kwargs.get('full_name', name),
            'phone': phone,
            'email': email,
            'notes': notes,
            'discount': kwargs.get('discount', old_data.get('discount', 0.5)),
            'is_blocked': is_blocked,
            'block_reason': block_reason,
            'blocked_by': blocked_by,
            'blocked_at': blocked_at,
            'dob': dob
        }
        
        diff = {}
        for k, v in new_data.items():
            old_val = str(old_data.get(k)) if old_data.get(k) is not None else ''
            new_val = str(v) if v is not None else ''
            if old_val != new_val:
                diff[k] = {'old': old_val, 'new': new_val}
                
        if diff:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                if new_id != partner_id:
                    cursor.execute('''
                        UPDATE partners 
                        SET id=?, name=?, full_name=?, phone=?, email=?, notes=?,
                            is_blocked=?, block_reason=?, blocked_by=?, blocked_at=?,
                            dob=?, discount=?, updated_at=?, synced=0
                        WHERE id=?
                    ''', (new_id, name, new_data['full_name'], phone, email, notes,
                          is_blocked, block_reason, blocked_by, blocked_at,
                          dob, new_data['discount'], datetime.now().isoformat(), partner_id))
                    cursor.execute('UPDATE receipts SET partner_id=? WHERE partner_id=?', (new_id, partner_id))
                    cursor.execute('UPDATE partners_history SET partner_id=? WHERE partner_id=?', (new_id, partner_id))
                else:
                    cursor.execute('''
                        UPDATE partners 
                        SET name=?, full_name=?, phone=?, email=?, notes=?,
                            is_blocked=?, block_reason=?, blocked_by=?, blocked_at=?,
                            dob=?, discount=?, updated_at=?, synced=0
                        WHERE id=?
                    ''', (name, new_data['full_name'], phone, email, notes,
                          is_blocked, block_reason, blocked_by, blocked_at,
                          dob, new_data['discount'], datetime.now().isoformat(), partner_id))
            
            # Use the most descriptive ID for history
            self.add_history_record(new_id, "Updated", diff, user_name)
            return True
        return False

    def add_history_record(self, partner_id, action, details, user_name):
        """Add a record to the partners history."""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO partners_history (partner_id, action, details, user_name, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                ''', (partner_id, action, json.dumps(details, ensure_ascii=False), user_name, datetime.now().isoformat()))
        except Exception as e:
            print(f"Failed to add partner history: {e}")

    def get_partner_history(self, search_query=None):
        """Retrieve partners history with optional filtering and joined creation date."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            base_query = '''
                SELECT ph.*, p.created_at as partner_created_at 
                FROM partners_history ph
                LEFT JOIN partners p ON ph.partner_id = p.id
            '''
            if search_query:
                q = f'%{search_query.lower()}%'
                cursor.execute(f'''
                    {base_query}
                    WHERE LOWER(ph.partner_id) LIKE ? OR LOWER(ph.action) LIKE ? 
                    ORDER BY ph.timestamp DESC
                ''', (q, q))
            else:
                cursor.execute(f'{base_query} ORDER BY ph.timestamp DESC')
            return [dict(row) for row in cursor.fetchall()]
    
    def get_partner_history_record(self, record_id):
        """Retrieve a single partner history record by its id."""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM partners_history WHERE id = ?', (record_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            print(f"Failed to get partner history record: {e}")
            return None
    
    def get_partner(self, partner_id):
        """Get partner by ID."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM partners WHERE id = ?', (partner_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_partner_by_id(self, partner_id):
        """Alias for compatibility with ui.py."""
        return self.get_partner(partner_id)
    
    def update_partner_stats(self, partner_id, amount):
        """Update partner purchase statistics."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            cursor.execute('''
                UPDATE partners
                SET total_purchases = total_purchases + 1, 
                    total_spent = total_spent + ?,
                    last_purchase_at = ?
                WHERE id = ?
            ''', (amount, now, partner_id))
            return cursor.rowcount > 0
    
    def get_all_partners(self):
        """Get all partners sorted by name."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM partners ORDER BY name')
            return [dict(row) for row in cursor.fetchall()]

    def add_partner_from_dict(self, data):
        """UPSERT partner from a dictionary (typically from cloud sync)."""
        return self.add_partner(
            name=data.get('name'),
            phone=data.get('phone', ''),
            email=data.get('email', ''),
            notes=data.get('notes', ''),
            partner_id=data.get('id'),
            full_name=data.get('full_name'),
            discount=data.get('discount', 0.5),
            dob=data.get('dob'),
            is_blocked=data.get('is_blocked', 0),
            block_reason=data.get('block_reason', ''),
            blocked_by=data.get('blocked_by', ''),
            blocked_at=data.get('blocked_at', ''),
            updated_at=data.get('updated_at'),
            synced=1
        )

    def get_all_partners_after(self, timestamp):
        """Get all partners updated after a specific timestamp."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM partners WHERE updated_at > ? ORDER BY updated_at ASC', (timestamp,))
            return [dict(row) for row in cursor.fetchall()]
    
    def get_unsynced_partners(self):
        """Get partners not yet synced to master."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM partners WHERE synced = 0')
            return [dict(row) for row in cursor.fetchall()]

    def mark_partner_synced(self, partner_id):
        """Mark partner as synced with master."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE partners SET synced = 1 WHERE id = ?', (partner_id,))
            return cursor.rowcount > 0
    
    def delete_partner(self, partner_id, user_name='System'):
        """Delete partner with error handling for linked receipts."""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                # Explicit check for receipts
                cursor.execute('SELECT COUNT(*) FROM receipts WHERE partner_id = ?', (partner_id,))
                if cursor.fetchone()[0] > 0:
                    raise Exception("Cannot delete partner: they have existing sales history. Delete receipts first.")
                
                # Record deletion in history before deleting the partner
                cursor.execute('SELECT name FROM partners WHERE id = ?', (partner_id,))
                p_data = cursor.fetchone()
                if p_data:
                    self.add_history_record(partner_id, 'Deleted', {'name': p_data['name']}, user_name)

                cursor.execute('DELETE FROM partners WHERE id = ?', (partner_id,))
                return cursor.rowcount > 0
        except sqlite3.Error as e:
            raise Exception(f"Database error during partner deletion: {e}")


# =============================================================================
# RECEIPTS MANAGER
# =============================================================================
class ReceiptsManagerSQL:
    """Manage receipts using SQLite database."""
    
    def __init__(self, db_manager, device_prefix=''):
        self.db = db_manager
        self.device_prefix = device_prefix
        self.counter = self._get_next_counter()
    
    def refresh_counter(self):
        """Update internal counter to the current highest number in DB."""
        self.counter = self._get_next_counter()

    def _get_next_counter(self):
        import re
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            # Find all numbers to handle legacy prefixed strings safely
            cursor.execute('SELECT number FROM receipts')
            rows = cursor.fetchall()
            max_val = 0
            for row in rows:
                num = row['number']
                if num is None: continue
                if isinstance(num, int):
                    if num > max_val: max_val = num
                else:
                    # Robust extraction: find any numeric part
                    match = re.search(r'(\d+)', str(num))
                    if match:
                        val = int(match.group(1))
                        if val > max_val: max_val = val
            return max_val + 1
    
    def create_receipt(self, items, total, discount=0, partner_id=None,
                      payment_cash=0, payment_card=0, payment_internal=0, change_given=0,
                      cashier_user=''):
        """Create new receipt with items."""
        # Refresh counter one last time before creating to minimize collisions
        self.refresh_counter()
        
        receipt_number = self.counter
        # TECHNICAL ID: Complex string with prefix for sync stability
        from datetime import date
        prefix = str(self.device_prefix) if self.device_prefix else ""
        receipt_id = f"{prefix}-{date.today().isoformat()}-{receipt_number:05d}"
        
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            cursor.execute('''
                INSERT INTO receipts
                (id, number, datetime, partner_id, subtotal, discount, total,
                 payment_cash, payment_card, payment_internal, change_given, status, 
                 cashier_user, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?, ?)
            ''', (receipt_id, receipt_number, now, partner_id,
                  total + discount, discount, total, payment_cash, payment_card,
                  payment_internal, change_given, cashier_user, now))
            
            for item in items:
                cursor.execute('''
                    INSERT INTO receipt_items (receipt_id, good_code, name, quantity, price, pv, sum)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (receipt_id, item['code'], item['name'], item['quantity'],
                      item['price'], item.get('pv', 0), item['sum']))
        
        self.counter += 1
        return self.get_receipt_by_id(receipt_id)

    def mark_receipt_synced(self, receipt_id):
        """Mark a receipt as synced with master."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE receipts SET synced = 1 WHERE id = ?', (receipt_id,))
            return cursor.rowcount > 0

    def get_unsynced_receipts(self):
        """Get all receipts that haven't been synced to master yet."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM receipts WHERE synced = 0 ORDER BY datetime ASC')
            ids = [row['id'] for row in cursor.fetchall()]
        return [self.get_receipt_by_id(rid) for rid in ids]

    def add_receipt(self, data, skip_inventory=False):
        """Add or UPDATE a receipt from a secondary register.
        
        Args:
            data: Receipt data dict
            skip_inventory: If True, don't deduct inventory (used when pulling from Master,
                          since pull_goods already synced correct quantities)
        """
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            
            # 1. Existing check for status merge
            cursor.execute('SELECT status FROM receipts WHERE id = ?', (data['id'],))
            row = cursor.fetchone()
            
            if row:
                cursor.execute('''
                    UPDATE receipts
                    SET status = ?, number = ?,
                        refund_datetime = ?, refund_reason = ?, refunded_by = ?,
                        refund_total = COALESCE(?, refund_total),
                        refund_method = ?,
                        pv_bot_status = ?, pv_bot_date = ?, pv_bot_error = ?,
                        updated_at = ?, synced = 1
                    WHERE id = ?
                ''', (
                    data.get('status', 'completed'),
                    data['number'],
                    data.get('refund', {}).get('datetime') if data.get('refund') else None,
                    data.get('refund', {}).get('reason', '') if data.get('refund') else '',
                    data.get('refunded_by', ''),
                    data.get('refund_total') or 0,
                    data.get('refund_method', ''),
                    data.get('pv_bot_status', 0),
                    data.get('pv_bot_date', ''),
                    data.get('pv_bot_error', ''),
                    data.get('updated_at') or data['datetime'],
                    data['id'],
                ))
                return True

            # 2. Fully new receipt
            final_number = data['number']
            
            # Conflict resolution: if this number is already taken by another ID
            cursor.execute('SELECT id FROM receipts WHERE number = ?', (final_number,))
            collision = cursor.fetchone()
            if collision and collision['id'] != data['id']:
                # Reassign to the end of the global sequence
                cursor.execute('SELECT number FROM receipts')
                existing_nums = [row['number'] for row in cursor.fetchall()]
                
                max_n = 0
                import re
                for n in existing_nums:
                    try:
                        if isinstance(n, int):
                            curr = n
                        else:
                            # Extract numeric part from legacy string like 'EDAB-125'
                            num_match = re.search(r'(\d+)', str(n))
                            curr = int(num_match.group(1)) if num_match else 0
                        if curr > max_n: max_n = curr
                    except:
                        pass
                final_number = max_n + 1
                
            cursor.execute('''
                INSERT INTO receipts
                (id, number, datetime, partner_id, subtotal, discount, total,
                 payment_cash, payment_card, payment_internal, change_given, status, 
                 refunded_by, cashier_user, updated_at, refund_total, refund_method)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (data['id'], final_number, data['datetime'], data['partner_id'],
                  data['subtotal'], data['discount'], data['total'],
                  data['payment']['cash'], data['payment']['card'],
                  data['payment']['internal'], data['payment']['change'],
                  data['status'], data.get('refunded_by', ''), data.get('cashier_user', ''),
                  data.get('updated_at') or data['datetime'],
                  data.get('refund_total') or 0, data.get('refund_method', '')))
            
            if data.get('refund'):
                cursor.execute('''
                    UPDATE receipts SET refund_datetime = ?, refund_reason = ? WHERE id = ?
                ''', (data['refund']['datetime'], data['refund']['reason'], data['id']))

            for item in data['items']:
                cursor.execute('''
                    INSERT INTO receipt_items (receipt_id, good_code, name, quantity, price, pv, sum)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (data['id'], item['good_code'], item['name'], item['quantity'],
                      item['price'], item.get('pv', 0), item['sum']))
                
                # Update inventory ONLY when Master receives from Secondary.
                # When Secondary pulls from Master, skip_inventory=True because
                # pull_goods() already synced the correct quantities.
                if not skip_inventory and data['status'] != 'refunded':
                    cursor.execute('''
                        UPDATE goods
                        SET quantity = quantity - ?
                        WHERE code = ?
                    ''', (item['quantity'], item['good_code']))
            
            # CRITICAL: Always update counter after adding a receipt from sync
            self.refresh_counter()
        return True
    
    def refund_receipt(self, receipt_id, reason=""):
        """Mark receipt as refunded."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            cursor.execute('''
                UPDATE receipts
                SET status = 'refunded', refund_datetime = ?, refund_reason = ?, 
                    refunded_by = ?, synced = 0, updated_at = ?
                WHERE id = ?
            ''', (now, reason, self.device_prefix, now, receipt_id))
            if cursor.rowcount > 0:
                return self.get_receipt_by_id(receipt_id)
        return None
    
    def get_receipt_by_id(self, receipt_id):
        """Get receipt with all items."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM receipts WHERE id = ?', (receipt_id,))
            receipt_row = cursor.fetchone()
            if not receipt_row:
                return None
            
            receipt = dict(receipt_row)
            cursor.execute('SELECT * FROM receipt_items WHERE receipt_id = ?', (receipt_id,))
            items = [dict(row) for row in cursor.fetchall()]

            cursor.execute('SELECT * FROM receipt_refund_logs WHERE receipt_id = ? ORDER BY datetime DESC', (receipt_id,))
            refund_logs = [dict(row) for row in cursor.fetchall()]
            for log in refund_logs:
                try:
                    log['items'] = json.loads(log['items_json'])
                except:
                    log['items'] = []
            
            return {
                'id': receipt['id'],
                'number': receipt['number'],
                'datetime': receipt['datetime'],
                'partner_id': receipt['partner_id'],
                'subtotal': receipt['subtotal'],
                'discount': receipt['discount'],
                'total': receipt['total'],
                'refunded_by': receipt.get('refunded_by', ''),
                'cashier_user': receipt.get('cashier_user', ''),
                'updated_at': receipt.get('updated_at', receipt['datetime']),
                'refund_total': receipt.get('refund_total', 0) or 0,
                'refund_method': receipt.get('refund_method', '') or '',
                'items': items,
                'payment': {
                    'cash': receipt['payment_cash'],
                    'card': receipt['payment_card'],
                    'internal': receipt['payment_internal'],
                    'change': receipt['change_given']
                },
                'status': receipt['status'],
                'live_sent': receipt.get('live_sent', 0),
                'live_status': receipt.get('live_status', 0),
                'live_processed_at': receipt.get('live_processed_at', ''),
                'live_error': receipt.get('live_error', ''),
                'refund': {
                    'datetime': receipt['refund_datetime'],
                    'reason': receipt['refund_reason']
                } if receipt['refund_datetime'] else None,
                'refund_logs': refund_logs
            }
    
    def get_all_receipts(self):
        """Get all receipts sorted by date desc."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM receipts ORDER BY datetime DESC')
            receipt_ids = [row['id'] for row in cursor.fetchall()]
        return [self.get_receipt_by_id(rid) for rid in receipt_ids]
    
    def get_daily_summary(self, target_date=None):
        """Get summary for specific date."""
        if target_date is None:
            target_date = date.today()
        date_str = target_date.isoformat()
        
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT
                    COUNT(*) as receipts_count,
                    COALESCE(SUM(total), 0) as total_sales,
                    COALESCE(SUM(payment_cash), 0) as payment_cash,
                    COALESCE(SUM(payment_card), 0) as payment_card,
                    COALESCE(SUM(payment_internal), 0) as payment_internal
                FROM receipts
                WHERE DATE(datetime) = ? AND status != 'refunded'
            ''', (date_str,))
            result = cursor.fetchone()
            
            # 2. Top 5 Items
            cursor.execute('''
                SELECT ri.name, SUM(ri.quantity) as qty
                FROM receipt_items ri
                JOIN receipts r ON ri.receipt_id = r.id
                WHERE DATE(r.datetime) = ? AND r.status != 'refunded'
                GROUP BY ri.name
                ORDER BY qty DESC
                LIMIT 5
            ''', (date_str,))
            top_items = [{'name': r['name'], 'qty': r['qty']} for r in cursor.fetchall()]

            return {
                'date': date_str,
                'receipts_count': result['receipts_count'] or 0,
                'total_sales': result['total_sales'] or 0,
                'payment_cash': result['payment_cash'] or 0,
                'payment_card': result['payment_card'] or 0,
                'payment_internal': result['payment_internal'] or 0,
                'top_items': top_items
            }

    def get_all_receipts_after(self, timestamp):
        """Get all receipts updated after a specific timestamp."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM receipts WHERE updated_at > ? ORDER BY updated_at ASC', (timestamp,))
            ids = [row['id'] for row in cursor.fetchall()]
        return [r for r in (self.get_receipt_by_id(rid) for rid in ids) if r]


# =============================================================================
# WRITEOFFS MANAGER
# =============================================================================
class WriteoffsManagerSQL:
    """Manage product write-offs using SQLite database."""
    
    def __init__(self, db_manager, device_prefix=''):
        self.db = db_manager
        self.device_prefix = device_prefix
        self.counter = self._get_next_counter()
    
    def _get_next_counter(self):
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT MAX(number) as max_num FROM writeoffs')
            result = cursor.fetchone()
            return (result['max_num'] or 0) + 1
            
    def create_writeoff(self, reason, items, cashier_user=''):
        """Create new write-off record."""
        writeoff_id = (f"{self.device_prefix}-{self.counter}" if self.device_prefix
                       else f"{self.counter}")
        items_count = sum(i['quantity'] for i in items)
        now = datetime.now().isoformat()
        
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO writeoffs (id, number, datetime, reason, items_count, cashier_user, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (writeoff_id, self.counter, now, reason, items_count, cashier_user, now))
            
            for item in items:
                cursor.execute('''
                    INSERT INTO writeoff_items (writeoff_id, good_code, name, quantity, barcode)
                    VALUES (?, ?, ?, ?, ?)
                ''', (writeoff_id, item['code'], item['name'], item['quantity'], item.get('barcode', '')))
        
        self.counter += 1
        return self.get_writeoff_by_id(writeoff_id)

    def get_writeoff_by_id(self, writeoff_id):
        """Get write-off with items."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM writeoffs WHERE id = ?', (writeoff_id,))
            row = cursor.fetchone()
            if not row: return None
            
            writeoff = dict(row)
            cursor.execute('SELECT * FROM writeoff_items WHERE writeoff_id = ?', (writeoff_id,))
            writeoff['items'] = [dict(r) for r in cursor.fetchall()]
            return writeoff

    def get_all_writeoffs(self, search_query=None, date_from=None, date_to=None):
        """Get all write-offs desc with optional filtering."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM writeoffs WHERE 1=1"
            params = []
            
            if date_from:
                query += " AND datetime >= ?"
                params.append(date_from)
            if date_to:
                query += " AND datetime <= ?"
                params.append(date_to)
            
            if search_query:
                query += " AND (reason LIKE ? OR cashier_user LIKE ? OR id LIKE ?)"
                params.extend([f"%{search_query}%", f"%{search_query}%", f"%{search_query}%"])
                
            query += " ORDER BY number DESC"
            cursor.execute(query, params)
            return [dict(r) for r in cursor.fetchall()]

    def get_all_writeoffs_after(self, timestamp):
        """Get all write-offs created or updated after timestamp."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM writeoffs WHERE updated_at > ? ORDER BY number ASC', (timestamp,))
            writeoffs = [dict(r) for r in cursor.fetchall()]
            for w in writeoffs:
                cursor.execute('SELECT * FROM writeoff_items WHERE writeoff_id = ?', (w['id'],))
                w['items'] = [dict(r) for r in cursor.fetchall()]
            return writeoffs

    def add_writeoff(self, data):
        """Insert a write-off directly from sync payload."""
        writeoff_id = data.get('id')
        if not writeoff_id:
            return False

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            # Check if exists
            cursor.execute('SELECT id FROM writeoffs WHERE id = ?', (writeoff_id,))
            if cursor.fetchone():
                return True # Already exists

            now = data.get('updated_at', datetime.now().isoformat())
            cursor.execute('''
                INSERT INTO writeoffs (id, number, datetime, reason, items_count, cashier_user, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (writeoff_id, data.get('number', self.counter), data.get('datetime', now),
                  data.get('reason', ''), data.get('items_count', 0), data.get('cashier_user', ''), now))

            # If number exceeded local counter
            num = data.get('number', 0)
            if isinstance(num, int) and num >= self.counter:
                self.counter = num + 1

            for item in data.get('items', []):
                cursor.execute('''
                    INSERT INTO writeoff_items (writeoff_id, good_code, name, quantity, barcode)
                    VALUES (?, ?, ?, ?, ?)
                ''', (writeoff_id, item.get('code', item.get('good_code', '')),
                      item.get('name', ''), item.get('quantity', 0), item.get('barcode', '')))
        
        return True

    def get_unsynced_writeoffs(self):
        """Get all write-offs that haven't been synced."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM writeoffs WHERE synced = 0')
            writeoffs = [dict(r) for r in cursor.fetchall()]
            for w in writeoffs:
                cursor.execute('SELECT * FROM writeoff_items WHERE writeoff_id = ?', (w['id'],))
                w['items'] = [dict(r) for r in cursor.fetchall()]
            return writeoffs

    def mark_writeoff_synced(self, writeoff_id):
        """Mark writeoff as synced."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE writeoffs SET synced = 1 WHERE id = ?', (writeoff_id,))


# =============================================================================
# PURCHASES MANAGER
# =============================================================================
class PurchasesManagerSQL:
    """Manage purchases using SQLite database."""
    
    def __init__(self, db_manager, device_prefix=''):
        self.db = db_manager
        self.device_prefix = device_prefix
        self.counter = self._get_next_counter()
    
    def _get_next_counter(self):
        """Get a safe next purchase counter, always reading from DB to avoid stale in-memory state."""
        import re
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            # Look at both 'number' column and the numeric part of 'id' values
            # to find the true maximum (ids are device-prefixed: {PREFIX}-{counter}).
            cursor.execute("SELECT id, number FROM purchases")
            rows = cursor.fetchall()
        max_val = 44
        for row in rows:
            for val in (row['id'], row['number']):
                if val is None:
                    continue
                try:
                    if isinstance(val, int):
                        curr = val
                    else:
                        match = re.search(r'(\d+)\s*$', str(val))
                        curr = int(match.group(1)) if match else 0
                except (ValueError, TypeError):
                    curr = 0
                if curr > max_val:
                    max_val = curr
        return max_val + 1

    def create_purchase(self, invoice_number, supplier, items, total_amount, notes="", cashier_user=''):
        """Create new purchase invoice."""
        items_count = sum((i.get('quantity') or i.get('qty') or 0) for i in items)
        now = datetime.now().isoformat()
        
        # Always get a fresh counter to avoid stale in-memory collisions
        fresh_counter = self._get_next_counter()
        # Use whichever is larger: in-memory counter or fresh DB counter
        counter = max(self.counter, fresh_counter)
        purchase_id = (f"{self.device_prefix}-{counter}" if self.device_prefix
                       else f"{counter}")
        
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute('''
                    INSERT INTO purchases (id, invoice_number, supplier, datetime, total_amount, items_count, notes, cashier_user, status, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (purchase_id, invoice_number, supplier, now, total_amount, items_count, notes, cashier_user, 'completed', now))
            except Exception:
                # If collision still occurs, increment and retry once
                counter += 1
                purchase_id = (f"{self.device_prefix}-{counter}" if self.device_prefix
                               else f"{counter}")
                cursor.execute('''
                    INSERT INTO purchases (id, invoice_number, supplier, datetime, total_amount, items_count, notes, cashier_user, status, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (purchase_id, invoice_number, supplier, now, total_amount, items_count, notes, cashier_user, 'completed', now))
            
            for item in items:
                cursor.execute('''
                    INSERT INTO purchase_items (purchase_id, code, name, barcode, quantity, purchase_price, sale_price, pv)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (purchase_id, item['code'], item['name'], item.get('barcode', ''), item['quantity'],
                      item['purchase_price'], item['sale_price'], item.get('pv', 0)))
        
        self.counter = counter + 1  # keep in-memory counter ahead
        return {
            'id': purchase_id, 'invoice_number': invoice_number, 'supplier': supplier,
            'datetime': now, 'items': items, 'total_amount': total_amount,
            'items_count': items_count, 'notes': notes, 'synced': 0
        }

    def mark_purchase_synced(self, purchase_id):
        """Mark a purchase as synced with master."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE purchases SET synced = 1 WHERE id = ?', (purchase_id,))
            return cursor.rowcount > 0

    def cancel_purchase(self, purchase_id):
        """Cancel a purchase invoice and revert inventory."""
        now = datetime.now().isoformat()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Get purchase status first
            cursor.execute('SELECT status FROM purchases WHERE id = ?', (purchase_id,))
            res = cursor.fetchone()
            if not res or res['status'] == 'cancelled':
                return False
            
            # Update status
            cursor.execute('''
                UPDATE purchases 
                SET status = 'cancelled', updated_at = ?, synced = 0 
                WHERE id = ?
            ''', (now, purchase_id))
            
            # Revert inventory
            cursor.execute('SELECT code, quantity FROM purchase_items WHERE purchase_id = ?', (purchase_id,))
            items = cursor.fetchall()
            for item in items:
                cursor.execute('''
                    UPDATE goods 
                    SET quantity = quantity - ? 
                    WHERE code = ?
                ''', (item['quantity'], item['code']))
            
            return True

    def get_unsynced_purchases(self):
        """Get all purchases that haven't been synced to master yet."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM purchases WHERE synced = 0 ORDER BY datetime ASC')
            ids = [row['id'] for row in cursor.fetchall()]
        return [p for p in (self.get_purchase(pid) for pid in ids) if p]

    def get_all_purchases_after(self, timestamp):
        """Get all purchases updated after a specific timestamp."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            # Use updated_at for accurate sync of cancellations
            cursor.execute('SELECT id FROM purchases WHERE updated_at > ? ORDER BY updated_at ASC', (timestamp,))
            ids = [row['id'] for row in cursor.fetchall()]
        return [p for p in (self.get_purchase(pid) for pid in ids) if p]

    def add_purchase_from_sync(self, data, skip_inventory=False):
        """Add or update a purchase invoice from sync (UPSERT).
        
        Args:
            data: Purchase data dict
            skip_inventory: If True, don't add inventory (used when pulling from Master,
                          since pull_goods already synced correct quantities)
        """
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            # Check if exists
            cursor.execute('SELECT id, status, updated_at FROM purchases WHERE id = ?', (data['id'],))
            existing = cursor.fetchone()
            
            now = datetime.now().isoformat()
            data_updated_at = data.get('updated_at') or data.get('datetime') or now
            data_status = data.get('status', 'completed')

            if existing:
                # Update if incoming data is newer
                if data_updated_at > (existing['updated_at'] or ''):
                    # Handle inventory reversal if status changed to cancelled
                    if not skip_inventory and existing['status'] != 'cancelled' and data_status == 'cancelled':
                        cursor.execute('SELECT code, quantity FROM purchase_items WHERE purchase_id = ?', (data['id'],))
                        for item in cursor.fetchall():
                            cursor.execute('UPDATE goods SET quantity = quantity - ? WHERE code = ?', 
                                         (item['quantity'], item['code']))
                    
                    cursor.execute('''
                        UPDATE purchases 
                        SET status = ?, updated_at = ?, synced = 1 
                        WHERE id = ?
                    ''', (data_status, data_updated_at, data['id']))
                return True
            
            # New purchase
            cursor.execute('''
                INSERT INTO purchases (id, invoice_number, supplier, datetime, total_amount, items_count, notes, cashier_user, status, updated_at, synced)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ''', (data['id'], data.get('invoice_number', ''), data.get('supplier', ''), data['datetime'], 
                  data.get('total_amount', 0) or 0, data.get('items_count', 0) or 0, data.get('notes', ''), 
                  data.get('cashier_user', ''), data_status, data_updated_at))
            
            for item in data.get('items', []):
                code = item.get('code', '')
                qty = item.get('quantity', 0) or 0
                if not skip_inventory and code and qty > 0 and data_status != 'cancelled':
                    # Update inventory ONLY when Master receives from Secondary.
                    # When Secondary pulls from Master, skip_inventory=True because
                    # pull_goods() already synced the correct quantities.
                    cursor.execute('''
                        UPDATE goods 
                        SET quantity = quantity + ? 
                        WHERE code = ?
                    ''', (qty, code))

                cursor.execute('''
                    INSERT INTO purchase_items (purchase_id, code, name, quantity, purchase_price, sale_price, pv, barcode)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (data['id'], code, item.get('name', ''), qty,
                      item.get('purchase_price', 0) or 0, item.get('sale_price', 0) or 0, 
                      item.get('pv', 0) or 0, item.get('barcode', '')))
        return True
    
    def get_all_purchases(self):
        """Get all purchases sorted by date desc."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM purchases ORDER BY datetime DESC')
            purchase_ids = [row['id'] for row in cursor.fetchall()]
        return [p for p in (self.get_purchase(pid) for pid in purchase_ids) if p]
    
    def get_purchase(self, purchase_id):
        """Get purchase with items."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM purchases WHERE id = ?', (purchase_id,))
            row = cursor.fetchone()
            if not row:
                return None
            purchase = dict(row)
            cursor.execute('SELECT * FROM purchase_items WHERE purchase_id = ?', (purchase_id,))
            purchase['items'] = [dict(r) for r in cursor.fetchall()]
            return purchase


# =============================================================================
# INVENTORY OPERATIONS (ATOMIC)
# =============================================================================
def _fmt_qty(value):
    """Format a quantity without trailing '.0' (integer-friendly display)."""
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        v = 0
    return str(int(v)) if v == int(v) else str(v)


class InventoryOpsManagerSQL:
    """Atomic business operations: sale, purchase, writeoff, refund.

    Each operation runs inside ONE transaction (a single get_connection
    block): the document row, its items and the goods quantity changes
    either all commit together or all roll back. Stock is validated
    before any decrement and negative balances are rejected.

    Any failure raises ValueError (friendly message) or sqlite3.Error —
    in both cases nothing is persisted.
    """

    def __init__(self, db_manager, device_prefix=''):
        self.db = db_manager
        self.device_prefix = device_prefix
        self._receipts = None
        self._purchases = None
        self._writeoffs = None

    def _receipts_mgr(self):
        if self._receipts is None:
            self._receipts = ReceiptsManagerSQL(self.db, device_prefix=self.device_prefix)
        return self._receipts

    def _purchases_mgr(self):
        if self._purchases is None:
            self._purchases = PurchasesManagerSQL(self.db, device_prefix=self.device_prefix)
        return self._purchases

    def _writeoffs_mgr(self):
        if self._writeoffs is None:
            self._writeoffs = WriteoffsManagerSQL(self.db, device_prefix=self.device_prefix)
        return self._writeoffs

    def _check_stock(self, cursor, items, action):
        """Validate that every item exists and has enough stock."""
        for item in items:
            qty = float(item.get('quantity') or 0)
            name = item.get('name') or item.get('code') or ''
            code = item.get('code') or ''
            if qty <= 0:
                raise ValueError(f"Некорректное количество: {name} ({code})")
            cursor.execute('SELECT name, quantity FROM goods WHERE code = ? AND is_deleted = 0', (code,))
            row = cursor.fetchone()
            if row is None:
                raise ValueError(f"Товар не найден: {name} ({code})")
            available = float(row['quantity'] or 0)
            if available < qty:
                raise ValueError(
                    f"Недостаточно товара для {action}: {row['name']} ({code}) — "
                    f"доступно {_fmt_qty(available)}, требуется {_fmt_qty(qty)}")

    def sale(self, items, total, discount=0, partner_id=None,
             payment_cash=0, payment_card=0, payment_internal=0, change_given=0,
             cashier_user=''):
        """Create a receipt and deduct stock atomically.

        Returns the receipt dict (with items) after commit. Sales are never
        blocked by stock level — remaining quantity may go negative. Raises
        ValueError on empty checkout or unknown item — nothing is committed.
        """
        items = list(items or [])
        if not items:
            raise ValueError("Корзина пуста")
        mgr = self._receipts_mgr()
        mgr.refresh_counter()
        receipt_number = mgr.counter
        from datetime import date as _date
        prefix = str(self.device_prefix) if self.device_prefix else ""
        receipt_id = f"{prefix}-{_date.today().isoformat()}-{receipt_number:05d}"
        now = datetime.now().isoformat()

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO receipts
                (id, number, datetime, partner_id, subtotal, discount, total,
                 payment_cash, payment_card, payment_internal, change_given, status,
                 cashier_user, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?, ?)
            ''', (receipt_id, receipt_number, now, partner_id,
                  total + discount, discount, total, payment_cash, payment_card,
                  payment_internal, change_given, cashier_user, now))
            for item in items:
                cursor.execute('''
                    INSERT INTO receipt_items (receipt_id, good_code, name, quantity, price, pv, sum)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (receipt_id, item['code'], item['name'], item['quantity'],
                      item['price'], item.get('pv', 0), item['sum']))
                cursor.execute('''
                    UPDATE goods SET quantity = quantity - ?
                    WHERE code = ?
                ''', (item['quantity'], item['code']))
                if cursor.rowcount == 0:
                    raise ValueError(
                        f"Товар не найден при продаже: {item['name']} ({item['code']})")
            if partner_id:
                cursor.execute('''
                    UPDATE partners
                    SET total_purchases = total_purchases + 1,
                        total_spent = total_spent + ?, last_purchase_at = ?
                    WHERE id = ?
                ''', (total, now, partner_id))

        mgr.counter = receipt_number + 1
        return mgr.get_receipt_by_id(receipt_id)

    def purchase(self, invoice_number, supplier, items, total_amount,
                 notes="", cashier_user=''):
        """Create a purchase invoice and add stock atomically.

        Also upserts goods metadata (name/prices/barcode) from the invoice
        lines in the same transaction. Returns the purchase dict.
        """
        items = list(items or [])
        if not items:
            raise ValueError("Накладная не содержит товаров")
        pm = self._purchases_mgr()
        counter = max(pm.counter, pm._get_next_counter())
        purchase_id = (f"{self.device_prefix}-{counter}" if self.device_prefix
                       else str(counter))
        items_count = sum(float(i.get('quantity') or i.get('qty') or 0) for i in items)
        now = datetime.now().isoformat()

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute('''
                    INSERT INTO purchases (id, invoice_number, supplier, datetime, total_amount,
                        items_count, notes, cashier_user, status, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?)
                ''', (purchase_id, invoice_number, supplier, now, total_amount,
                      items_count, notes, cashier_user, now))
            except sqlite3.IntegrityError:
                counter += 1
                purchase_id = (f"{self.device_prefix}-{counter}" if self.device_prefix
                               else str(counter))
                cursor.execute('''
                    INSERT INTO purchases (id, invoice_number, supplier, datetime, total_amount,
                        items_count, notes, cashier_user, status, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?)
                ''', (purchase_id, invoice_number, supplier, now, total_amount,
                      items_count, notes, cashier_user, now))
            for item in items:
                code = item.get('code') or ''
                if not code:
                    raise ValueError("Позиция накладной без кода товара")
                qty = float(item.get('quantity') or item.get('qty') or 0)
                if qty <= 0:
                    raise ValueError(f"Некорректное количество: {item.get('name', code)} ({code})")
                name = item.get('name', '')
                barcode = item.get('barcode', '')
                pv = item.get('pv', 0)
                try:
                    pp = int(round(float(item.get('purchase_price') or 0)))
                except (TypeError, ValueError):
                    pp = 0
                try:
                    sp = int(round(float(item.get('sale_price') or 0)))
                except (TypeError, ValueError):
                    sp = 0
                cursor.execute('''
                    INSERT INTO purchase_items (purchase_id, code, name, barcode, quantity,
                        purchase_price, sale_price, pv)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (purchase_id, code, name, barcode, qty, pp, sp, pv))
                cursor.execute('SELECT * FROM goods WHERE code = ?', (code,))
                existing = cursor.fetchone()
                if existing:
                    try:
                        cat_changed = (
                            (existing['name'] or '') != (name or '') or
                            (existing['barcode'] or '') != (barcode or '') or
                            float(existing['pv'] or 0) != float(pv or 0) or
                            float(existing['purchase_price'] or 0) != float(pp or 0) or
                            float(existing['sale_price'] or 0) != float(sp or 0)
                        )
                    except (ValueError, TypeError):
                        cat_changed = True
                    if cat_changed:
                        cursor.execute('''
                            UPDATE goods
                            SET name = ?, pv = ?, barcode = ?, purchase_price = ?, sale_price = ?,
                                updated_at = ?
                            WHERE code = ?
                        ''', (name, pv, barcode, pp, sp, now, code))
                    cursor.execute('''
                        UPDATE goods
                        SET quantity = quantity + ?
                        WHERE code = ?
                    ''', (qty, code))
                else:
                    good_id = hashlib.md5(f"{code}{name}{barcode}".encode()).hexdigest()[:12]
                    cursor.execute('''
                        INSERT INTO goods (id, code, name, pv, barcode, purchase_price, sale_price,
                            quantity, created_at, updated_at, is_deleted)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    ''', (good_id, code, name, pv, barcode, pp, sp, qty, now, now))

        pm.counter = counter + 1
        return pm.get_purchase(purchase_id)

    def writeoff(self, reason, items, cashier_user=''):
        """Create a write-off document and deduct stock atomically.

        Validates available stock per item; raises ValueError on shortage
        (nothing is committed).
        """
        items = list(items or [])
        if not items:
            raise ValueError("Список товаров для списания пуст")
        wm = self._writeoffs_mgr()
        writeoff_id = (f"{self.device_prefix}-{wm.counter}" if self.device_prefix
                       else str(wm.counter))
        items_count = sum(float(i.get('quantity') or 0) for i in items)
        now = datetime.now().isoformat()

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self._check_stock(cursor, items, "списания")
            cursor.execute('''
                INSERT INTO writeoffs (id, number, datetime, reason, items_count, cashier_user, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (writeoff_id, wm.counter, now, reason, items_count, cashier_user, now))
            for item in items:
                cursor.execute('''
                    INSERT INTO writeoff_items (writeoff_id, good_code, name, quantity, barcode)
                    VALUES (?, ?, ?, ?, ?)
                ''', (writeoff_id, item['code'], item['name'], item['quantity'],
                      item.get('barcode', '')))
                cursor.execute('''
                    UPDATE goods SET quantity = quantity - ?
                    WHERE code = ? AND quantity >= ?
                ''', (item['quantity'], item['code'], item['quantity']))
                if cursor.rowcount == 0:
                    raise ValueError(
                        f"Недостаточно товара при списании: {item['name']} ({item['code']})")

        wm.counter += 1
        return wm.get_writeoff_by_id(writeoff_id)

    def refund(self, receipt, items_to_refund, reason, refunded_by='',
               user_name='', device_name='', refund_method=''):
        """Process (partial) refund atomically.

        items_to_refund: list of (item_index_in_receipt, quantity).
        Money is recorded: `refund_total` accumulates the returned amount per
        line (line sum × refunded share), `refund_method` stores the method
        of the last refund (cash/card/internal).
        Returns the new receipt status ('completed'/'partial_refund'/'refunded').
        """
        receipt_id = receipt['id']
        now = datetime.now().isoformat()
        refunded_log = []
        refund_total = 0.0

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            for item_idx, refund_qty in items_to_refund:
                refund_qty = float(refund_qty or 0)
                if refund_qty <= 0:
                    continue
                ri = receipt['items'][item_idx]
                item_id = ri.get('id')
                item_code = ri.get('good_code') or ri.get('code')
                line_qty = float(ri.get('quantity') or 0)
                line_sum = float(ri.get('sum') or 0)
                line_refund = round(line_sum * (refund_qty / line_qty), 2) if line_qty > 0 else 0.0
                refund_total += line_refund
                if item_id:
                    cursor.execute('''
                        UPDATE receipt_items SET refunded_qty = COALESCE(refunded_qty, 0) + ?
                        WHERE id = ?
                    ''', (refund_qty, item_id))
                else:
                    cursor.execute('''
                        UPDATE receipt_items SET refunded_qty = COALESCE(refunded_qty, 0) + ?
                        WHERE receipt_id = ? AND name = ?
                    ''', (refund_qty, receipt_id, ri['name']))
                if item_code:
                    cursor.execute('''
                        UPDATE goods SET quantity = quantity + ?
                        WHERE code = ?
                    ''', (refund_qty, item_code))
                refunded_log.append({'name': ri['name'], 'qty': refund_qty, 'amount': line_refund})

            cursor.execute('''
                SELECT SUM(quantity) as total_qty, SUM(COALESCE(refunded_qty, 0)) as total_refunded
                FROM receipt_items WHERE receipt_id = ?
            ''', (receipt_id,))
            row = cursor.fetchone()
            total_qty = (row['total_qty'] or 0) if row else 0
            total_refunded = (row['total_refunded'] or 0) if row else 0
            if total_refunded >= total_qty:
                new_status = 'refunded'
            elif total_refunded > 0:
                new_status = 'partial_refund'
            else:
                new_status = 'completed'

            cursor.execute('''
                UPDATE receipts
                SET status = ?, refund_datetime = ?, refund_reason = ?,
                    refunded_by = ?, refund_total = COALESCE(refund_total, 0) + ?,
                    refund_method = ?, synced = 0, updated_at = ?
                WHERE id = ?
            ''', (new_status, now, reason, refunded_by, refund_total,
                  refund_method, now, receipt_id))

            cursor.execute('''
                INSERT INTO receipt_refund_logs (receipt_id, datetime, reason, user_name, device_name, items_json)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (receipt_id, now, reason, user_name, device_name,
                  json.dumps(refunded_log, ensure_ascii=False)))

        return new_status


# =============================================================================
# QUICK ITEMS MANAGER
# =============================================================================
class QuickItemsManagerSQL:
    """Manage quick items using SQLite database."""
    
    def __init__(self, db_manager):
        self.db = db_manager
    
    def set_item(self, slot_index, item_data):
        """Set quick item at slot (0-19)."""
        if not (0 <= slot_index < 20):
            return False
        
        if isinstance(item_data, str):
            good_code = item_data
            item_json = json.dumps({'code': item_data})
        elif isinstance(item_data, dict):
            good_code = item_data.get('code', '')
            item_json = json.dumps(item_data)
        else:
            return False
        
        now = datetime.now().isoformat()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE quick_items 
                SET good_code = ?, item_data = ?, updated_at = ? 
                WHERE slot_index = ?
            ''', (good_code, item_json, now, slot_index))
        return True
    
    def clear_item(self, slot_index):
        """Clear quick item at slot and shift remaining items left to close gaps."""
        if not (0 <= slot_index < 20):
            return False
            
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            # Mark the slot for removal
            cursor.execute(
                'UPDATE quick_items SET good_code = NULL, item_data = NULL WHERE slot_index = ?',
                (slot_index,))
            
            # Get all remaining active items sorted by original slot
            cursor.execute('SELECT good_code, item_data FROM quick_items WHERE good_code IS NOT NULL ORDER BY slot_index')
            active_items = cursor.fetchall()
            
            # Clear all slots first
            cursor.execute('UPDATE quick_items SET good_code = NULL, item_data = NULL')
            
            # Re-fill from index 0
            now = datetime.now().isoformat()
            for i, row in enumerate(active_items):
                if i < 20:
                    cursor.execute('''
                        UPDATE quick_items 
                        SET good_code = ?, item_data = ?, updated_at = ? 
                        WHERE slot_index = ?
                    ''', (row['good_code'], row['item_data'], now, i))
        return True
    
    def get_item(self, slot_index):
        """Get quick item at slot."""
        if not (0 <= slot_index < 20):
            return None
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT item_data FROM quick_items WHERE slot_index = ?', (slot_index,))
            row = cursor.fetchone()
            if row and row['item_data']:
                return json.loads(row['item_data'])
        return None


# =============================================================================
# USERS MANAGER (Local Authentication & Permissions)
# =============================================================================
class UsersManagerSQL:
    """Manage local app users with PIN authentication and permissions."""
    
    # Default permissions for role templates
    ROLE_TEMPLATES = {
        'admin': {
            'arrival_view': True, 'arrival_create': True, 'arrival_edit': True, 'arrival_delete': True,
            'writeoff_view': True, 'writeoff_create': True,
            'inventory_view': True, 'inventory_conduct': True,
            'sales_view': True, 'sales_refund_partial': True, 'sales_refund_full': True,
            'partner_view': True, 'partner_create': True, 'partner_edit': True, 'partner_delete': True, 'partner_block': True,
            'pvbot_use': True,
            'analytics_view': True,
            'bizanalytics_view': True,
            'autoreview_view': True, 'autoreview_start': True,
            'pos_view': True,
            'cancellations_view': True,
            'purchase_cancel': True,
            'goods_code_edit': False,
            'partner_history': False,
            'settings_visible': True, 'settings_appearance': True, 'settings_printer': True, 
            'settings_sync': True, 'settings_performance': True, 'settings_automation': True, 'settings_autorun': True,
            'settings_integrations': True, 'settings_database': True,
            'user_management': True,
            'can_edit_ids': False,
        },
        'superadmin': {
            'arrival_view': True, 'arrival_create': True, 'arrival_edit': True, 'arrival_delete': True,
            'writeoff_view': True, 'writeoff_create': True,
            'inventory_view': True, 'inventory_conduct': True,
            'sales_view': True, 'sales_refund_partial': True, 'sales_refund_full': True,
            'partner_view': True, 'partner_create': True, 'partner_edit': True, 'partner_delete': True, 'partner_block': True,
            'partner_history': True,
            'pvbot_use': True,
            'analytics_view': True,
            'bizanalytics_view': True,
            'autoreview_view': True, 'autoreview_start': True,
            'pos_view': True,
            'cancellations_view': True,
            'purchase_cancel': True,
            'goods_code_edit': True,
            'settings_visible': True, 'settings_appearance': True, 'settings_printer': True, 
            'settings_sync': True, 'settings_performance': True, 'settings_automation': True, 'settings_autorun': True,
            'settings_integrations': True, 'settings_database': True,
            'user_management': True,
            'can_edit_ids': True,
        },
        'cashier': {
            'arrival_view': True, 'arrival_create': False, 'arrival_edit': False, 'arrival_delete': False,
            'writeoff_view': True, 'writeoff_create': True,
            'inventory_view': True, 'inventory_conduct': False,
            'sales_view': True, 'sales_refund_partial': False, 'sales_refund_full': False,
            'partner_view': True, 'partner_create': True, 'partner_edit': False, 'partner_delete': False, 'partner_block': False,
            'pvbot_use': False,
            'analytics_view': False,
            'bizanalytics_view': False,
            'autoreview_view': False, 'autoreview_start': False,
            'pos_view': True,
            'cancellations_view': False,
            'purchase_cancel': False,
            'goods_code_edit': False,
            'partner_history': False,
            'settings_visible': True, 'settings_appearance': True, 'settings_printer': False, 
            'settings_sync': False, 'settings_performance': False, 'settings_automation': False, 'settings_autorun': False,
            'settings_integrations': False, 'settings_database': False,
            'user_management': False,
            'can_edit_ids': False,
        },
        'viewer': {
            'arrival_view': True, 'arrival_create': False, 'arrival_edit': False, 'arrival_delete': False,
            'writeoff_view': True, 'writeoff_create': False,
            'inventory_view': True, 'inventory_conduct': False,
            'sales_view': True, 'sales_refund_partial': False, 'sales_refund_full': False,
            'partner_view': True, 'partner_create': False, 'partner_edit': False, 'partner_delete': False, 'partner_block': False,
            'pvbot_use': False,
            'analytics_view': True,
            'bizanalytics_view': True,
            'autoreview_view': True, 'autoreview_start': False,
            'pos_view': False,
            'cancellations_view': False,
            'purchase_cancel': False,
            'goods_code_edit': False,
            'partner_history': False,
            'settings_visible': False, 'settings_appearance': True, 'settings_printer': False, 
            'settings_sync': False, 'settings_performance': False, 'settings_automation': False, 'settings_autorun': False,
            'settings_integrations': False, 'settings_database': False,
            'user_management': False,
            'can_edit_ids': False,
        },
    }
    
    # All permission keys with Russian labels
    PERMISSION_LABELS = {
        'arrival_view':         'Поступления: Просмотр',
        'arrival_create':       'Поступления: Создание',
        'arrival_edit':         'Поступления: Редактирование',
        'arrival_delete':       'Поступления: Удаление',
        'writeoff_view':        'Списания: Просмотр',
        'writeoff_create':       'Списания: Создание',
        'inventory_view':       'Ревизия: Просмотр',
        'inventory_conduct':    'Ревизия: Проведение',
        'sales_view':           'Продажи: Просмотр',
        'sales_refund_full':    'Продажи: Полный возврат',
        'sales_refund_partial': 'Продажи: Частичный возврат',
        'pos_view':             'Касса: Доступ',
        'cancellations_view':   'История: Отмены (касса)',
        'purchase_cancel':      'Поступления: Отмена поставки',
        'goods_code_edit':      'Товары: Код/штрихкод (ред.)',
        'partner_view':         'Партнеры: Просмотр',
        'partner_create':       'Партнеры: Создание',
        'partner_edit':         'Партнеры: Редактирование',
        'partner_delete':       'Партнеры: Удаление',
        'partner_block':        'Партнеры: Блокировка',
        'partner_history':      'Партнеры: История изменений',
        'pvbot_use':            'PV Бот: Запуск',
        'analytics_view':       'Аналитика: Просмотр',
        'bizanalytics_view':    'Бизнес-аналитика: Просмотр',
        'autoreview_view':      'Автоскладирование: Просмотр',
        'autoreview_start':     'Автоскладирование: Запуск',
        'settings_visible':     'Настройки: Доступ',
        'settings_appearance':  'Настройки: Внешний вид',
        'settings_printer':     'Настройки: Принтер и Чек',
        'settings_sync':        'Настройки: Система',
        'settings_performance': 'Настройки: Производительность',
        'settings_automation':  'Настройки: Автоматизация',
        'settings_autorun':     'Настройки: Автозапуск',
        'settings_integrations': 'Настройки: Интеграции',
        'settings_database':    'Настройки: База данных',
        'user_management':      'Настройки: Пользователи и права',
        'can_edit_ids':         'Админ: Редактирование ID/Кодов',
    }
    
    def __init__(self, db_manager):
        self.db = db_manager
    
    @staticmethod
    def hash_pin(pin):
        """Hash a 4-digit PIN."""
        return hashlib.sha256(pin.encode()).hexdigest()
    
    def has_any_users(self):
        """Check if any users exist."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) as cnt FROM app_users')
            return cursor.fetchone()['cnt'] > 0
    
    def ensure_superadmin(self):
        """Ensure exactly one superadmin exists.
        
        The superadmin is the very first user ever created. If no active
        superadmin exists (e.g. databases created before this role existed),
        the oldest active 'admin' user is promoted to superadmin with the
        full superadmin permission template.
        """
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as cnt FROM app_users WHERE role = 'superadmin' AND is_active = 1")
            if cursor.fetchone()['cnt'] > 0:
                return False
            cursor.execute("SELECT username FROM app_users WHERE role = 'admin' AND is_active = 1 ORDER BY id ASC LIMIT 1")
            row = cursor.fetchone()
            if row:
                template = self.ROLE_TEMPLATES['superadmin']
                cursor.execute('''
                    UPDATE app_users SET role = ?, permissions = ?, updated_at = ?
                    WHERE username = ?
                ''', ('superadmin', json.dumps(template), datetime.now().isoformat(), row['username']))
                return True
        return False
    
    def create_user(self, username, display_name, role, pin, pin_hint='', permissions=None):
        """Create a new user. Returns user dict or None on failure."""
        if permissions is None:
            permissions = self.ROLE_TEMPLATES.get(role, self.ROLE_TEMPLATES['cashier']).copy()
        
        username_clean = username.lower().strip()
        now = datetime.now().isoformat()
        
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                # Check if user already exists
                cursor.execute('SELECT id, is_active FROM app_users WHERE username = ?', (username_clean,))
                existing = cursor.fetchone()
                
                if existing:
                    if existing['is_active']:
                        return None  # Active user already exists
                    else:
                        # Reactivate inactive user
                        cursor.execute('''
                            UPDATE app_users 
                            SET display_name = ?, role = ?, pin_hash = ?, pin_hint = ?, 
                                permissions = ?, is_active = 1, updated_at = ?
                            WHERE username = ?
                        ''', (display_name.strip(), role, self.hash_pin(pin),
                              pin_hint, json.dumps(permissions), now, username_clean))
                else:
                    # Create new user
                    cursor.execute('''
                        INSERT INTO app_users (username, display_name, role, pin_hash, pin_hint, permissions, is_active, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                    ''', (username_clean, display_name.strip(), role, self.hash_pin(pin),
                          pin_hint, json.dumps(permissions), now, now))
            
            # Transaction committed, now get the user data
            return self.get_user_by_username(username_clean)
            
        except sqlite3.IntegrityError:
            return None  # Unexpected unique constraint violation
    
    def verify_pin(self, username, pin):
        """Verify PIN for user. Returns user dict or None."""
        user = self.get_user_by_username(username)
        if user and user['is_active'] and user['pin_hash'] == self.hash_pin(pin):
            return user
        return None
    
    @staticmethod
    def _merge_permissions(role, stored_perms):
        """Merge stored permissions with the role template so new permission
        keys introduced in updates apply to existing users automatically
        (explicitly set values are always kept)."""
        template = UsersManagerSQL.ROLE_TEMPLATES.get(role, {})
        merged = dict(template)
        merged.update(stored_perms or {})
        return merged

    def get_user_by_username(self, username):
        """Get user by username."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM app_users WHERE username = ?', (username.lower().strip(),))
            row = cursor.fetchone()
            if row:
                user = dict(row)
                user['permissions'] = self._merge_permissions(
                    user.get('role', ''), json.loads(user.get('permissions', '{}')))
                return user
        return None
    
    def get_all_users(self):
        """Get all active users."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM app_users WHERE is_active = 1 ORDER BY role, username')
            users = []
            for row in cursor.fetchall():
                user = dict(row)
                user['permissions'] = self._merge_permissions(
                    user.get('role', ''), json.loads(user.get('permissions', '{}')))
                users.append(user)
            return users
    
    def update_permissions(self, username, permissions):
        """Update user permissions. Superadmin permissions cannot be changed."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT role FROM app_users WHERE username = ?", (username.lower().strip(),))
            row = cursor.fetchone()
            if row and row['role'] == 'superadmin':
                return False
            cursor.execute('''
                UPDATE app_users SET permissions = ?, updated_at = ? WHERE username = ?
            ''', (json.dumps(permissions), datetime.now().isoformat(), username.lower().strip()))
            return cursor.rowcount > 0
    
    def reset_pin(self, username, new_pin, new_hint=''):
        """Reset user PIN."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE app_users SET pin_hash = ?, pin_hint = ?, updated_at = ? WHERE username = ?
            ''', (self.hash_pin(new_pin), new_hint, datetime.now().isoformat(), username.lower().strip()))
            return cursor.rowcount > 0
    
    def delete_user(self, username):
        """Soft-delete user (set inactive). Cannot delete superadmin or last admin."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT role FROM app_users WHERE username = ?", (username.lower().strip(),))
            row = cursor.fetchone()
            if row and row['role'] == 'superadmin':
                return False  # Cannot delete the superadmin
            # Check if this is the last admin
            cursor.execute("SELECT COUNT(*) as cnt FROM app_users WHERE role = 'admin' AND is_active = 1")
            admin_count = cursor.fetchone()['cnt']
            if row and row['role'] == 'admin' and admin_count <= 1:
                return False  # Cannot delete last admin
            
            cursor.execute('''
                UPDATE app_users SET is_active = 0, updated_at = ? WHERE username = ?
            ''', (datetime.now().isoformat(), username.lower().strip()))
            return cursor.rowcount > 0
    
    def update_role(self, username, new_role):
        """Update user role. Superadmin role cannot be changed."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT role FROM app_users WHERE username = ?", (username.lower().strip(),))
            row = cursor.fetchone()
            if row and row['role'] == 'superadmin':
                return False
            cursor.execute('''
                UPDATE app_users SET role = ?, updated_at = ? WHERE username = ?
            ''', (new_role, datetime.now().isoformat(), username.lower().strip()))
            return cursor.rowcount > 0
    
    def get_pin_hint(self, username):
        """Get PIN hint for user."""
        user = self.get_user_by_username(username)
        return user['pin_hint'] if user else ''


# =============================================================================
# SYNC MARKERS MANAGER
# =============================================================================
class MarkersManagerSQL:
    """Manages persistent sync markers (last_sync timestamps)."""
    
    def __init__(self, db_manager):
        self.db = db_manager
        
    def get_marker(self, key, default="0"):
        """Get marker value by key."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT marker_value FROM sync_markers WHERE marker_key = ?', (key,))
            result = cursor.fetchone()
            return result['marker_value'] if result else default
            
    def set_marker(self, key, value):
        """Set marker value by key."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO sync_markers (marker_key, marker_value)
                VALUES (?, ?)
            ''', (key, str(value)))
        return True


class SyncLogManager:
    """Manages the sync_log - unified change queue for all entity types.

    Every create/update/delete operation writes a row into sync_log.
    The sync engine pushes unsynced rows to the Master and pulls
    remote rows to apply locally.
    """

    def __init__(self, db_manager, device_key=""):
        self.db = db_manager
        self.device_key = device_key or ""

    def log(self, entity_type, entity_id, operation, data, version=1):
        """Append a change entry to the sync_log."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO sync_log
                (entity_type, entity_id, operation, data, device_key, version, created_at, synced)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """, (
                entity_type,
                str(entity_id),
                operation,
                json.dumps(data, ensure_ascii=False, default=str),
                self.device_key,
                version,
                datetime.now().isoformat()
            ))
            return cursor.lastrowid

    def get_unsynced(self, limit=500):
        """Get all unsynced log entries, oldest first."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM sync_log WHERE synced = 0 ORDER BY id ASC LIMIT ?
            """, (limit,))
            return [dict(r) for r in cursor.fetchall()]

    def get_unsynced_count(self):
        """Count pending unsynced entries."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM sync_log WHERE synced = 0")
            return cursor.fetchone()[0]

    def mark_synced(self, log_ids):
        """Mark specific log entries as synced."""
        if not log_ids:
            return
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            placeholders = ",".join(["?"] * len(log_ids))
            cursor.execute(f"UPDATE sync_log SET synced = 1 WHERE id IN ({placeholders})", log_ids)

    def get_last_id(self):
        """Get the highest sync_log id (used as sync marker)."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COALESCE(MAX(id), 0) FROM sync_log")
            return cursor.fetchone()[0]


class InventoryAuditManagerSQL:
    """Manager for inventory audits (stock-taking / ревизия)."""
    
    def __init__(self, db_manager, device_prefix=''):
        self.db = db_manager
        self.device_prefix = device_prefix
    
    # ── helpers ──────────────────────────────────────────────────────────
    def _gen_id(self):
        import uuid
        ts = datetime.now().strftime('%y%m%d%H%M')
        short = uuid.uuid4().hex[:6].upper()
        pfx = self.device_prefix or 'AUD'
        return f"{pfx}-A{ts}-{short}"
    
    # ── CRUD ─────────────────────────────────────────────────────────────
    def create_audit(self, audit_type='full', filter_criteria=None, created_by='', notes='', zero_negatives=False, **kwargs):
        """Create a new audit and snapshot all (or filtered) goods."""
        import json as _json
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        audit_id = self._gen_id()
        criteria = filter_criteria or {}
        
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            
            # 1. Get candidate goods
            if audit_type == 'full':
                cursor.execute('SELECT id, code, name, barcode, sale_price, purchase_price, quantity FROM goods')
            else:
                search = criteria.get('search', '').strip().lower()
                cursor.execute('''
                    SELECT id, code, name, barcode, sale_price, purchase_price, quantity FROM goods 
                    WHERE LOWER(name) LIKE ? OR LOWER(code) LIKE ? OR barcode LIKE ?
                ''', (f"%{search}%", f"%{search}%", f"%{search}%"))
            
            goods = [dict(r) for r in cursor.fetchall()]
            if not goods:
                return None
            
            # 2. Register audit
            cursor.execute('''
                INSERT INTO inventory_audits 
                (id, created_at, status, audit_type, filter_criteria, 
                 created_by, created_device, snapshot_total_items, notes, zero_negatives, updated_at)
                VALUES (?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (audit_id, now, audit_type, _json.dumps(criteria, ensure_ascii=False),
                  created_by, kwargs.get('created_device', ''), len(goods), notes, 
                  1 if zero_negatives else 0, now))
            
            # 3. Snapshot each good
            for g in goods:
                cursor.execute('''
                    INSERT INTO inventory_audit_items
                    (audit_id, good_id, good_code, good_name, good_barcode,
                     sale_price, purchase_price, expected_qty)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (audit_id, g['id'], g['code'], g['name'], 
                      g.get('barcode', ''), g.get('sale_price', 0),
                      g.get('purchase_price', 0), g.get('quantity', 0)))
        
        return self.get_audit(audit_id)

    def get_audit_active_summary(self, audit_id):
        """Perform instant SQL-based summary for confirmation before finalizing.
        Prevents UI blocking for large inventories.
        """
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT audit_type, zero_negatives FROM inventory_audits WHERE id = ?', (audit_id,))
            audit = cursor.fetchone()
            if not audit: return None
            
            is_full = audit['audit_type'] == 'full'
            zero_neg = bool(audit['zero_negatives'])
            
            cursor.execute('''
                SELECT 
                    COUNT(*) as total_items,
                    SUM(CASE WHEN actual_qty IS NOT NULL THEN 1 ELSE 0 END) as counted,
                    -- Potential shortage (money value of uncounted items in full audit)
                    SUM(CASE 
                        WHEN actual_qty IS NULL AND ? = 1 AND (
                            expected_qty > 0 OR (? = 1 AND expected_qty < 0)
                        ) THEN ABS(expected_qty * sale_price)
                        ELSE 0 
                    END) as potential_shortage_money
                FROM inventory_audit_items
                WHERE audit_id = ?
            ''', (1 if is_full else 0, 1 if zero_neg else 0, audit_id))
            
            res = dict(cursor.fetchone())
            res['uncounted'] = res['total_items'] - res['counted']
            return res

    
    def get_audit(self, audit_id):
        """Get audit header by ID."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM inventory_audits WHERE id = ?', (audit_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_audit_items(self, audit_id, only_counted=False, only_uncounted=False, 
                        search_query='', show_discrepancies=False):
        """Get audit items with optional filters."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            query = 'SELECT * FROM inventory_audit_items WHERE audit_id = ?'
            params = [audit_id]
            
            if only_counted:
                query += ' AND actual_qty IS NOT NULL'
            elif only_uncounted:
                query += ' AND actual_qty IS NULL'
            
            if search_query:
                query += ' AND (good_name LIKE ? OR good_code LIKE ? OR good_barcode LIKE ?)'
                s = f"%{search_query}%"
                params.extend([s, s, s])
            
            if show_discrepancies:
                query += ' AND actual_qty IS NOT NULL AND difference != 0'
            
            query += ' ORDER BY good_name'
            cursor.execute(query, params)
            return [dict(r) for r in cursor.fetchall()]
    
    def update_item_count(self, audit_id, good_code, actual_qty, counted_by=''):
        """Record actual quantity for a single item."""
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, good_code, expected_qty FROM inventory_audit_items 
                WHERE audit_id = ? AND good_code = ?
            ''', (audit_id, good_code))
            item = cursor.fetchone()
            if not item:
                return False
            
            difference = float(actual_qty) - float(item['expected_qty'])
            cursor.execute('''
                UPDATE inventory_audit_items 
                SET actual_qty = ?, difference = ?, counted_at = ?, counted_by = ?
                WHERE audit_id = ? AND good_code = ?
            ''', (float(actual_qty), difference, now, counted_by, audit_id, good_code))
            
            cursor.execute('''
                UPDATE inventory_audits 
                SET counted_items = (
                    SELECT COUNT(*) FROM inventory_audit_items 
                    WHERE audit_id = ? AND actual_qty IS NOT NULL
                ), updated_at = ?
                WHERE id = ?
            ''', (audit_id, now, audit_id))
        
        return True
    
    def set_uncounted_to_expected(self, audit_id, counted_by=''):
        """Set actual_qty = expected_qty for all uncounted items in an audit. 
        This 'approves' the remaining items as matching the system.
        """
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Update items: actual = expected, difference = 0
            cursor.execute('''
                UPDATE inventory_audit_items 
                SET actual_qty = expected_qty, difference = 0, 
                    counted_at = ?, counted_by = ?
                WHERE audit_id = ? AND actual_qty IS NULL
            ''', (now, counted_by, audit_id))
            
            updated_count = cursor.rowcount
            
            # Update audit header progress
            cursor.execute('''
                UPDATE inventory_audits 
                SET counted_items = (
                    SELECT COUNT(*) FROM inventory_audit_items 
                    WHERE audit_id = ? AND actual_qty IS NOT NULL
                ), updated_at = ?
                WHERE id = ?
            ''', (audit_id, now, audit_id))
            
            return updated_count
    
    def finalize_audit(self, audit_id, completed_by='', completed_device=''):
        """Finalize audit: calculate sold_during_audit, adjusted differences, totals.
        
        Args:
            audit_id: ID of the audit
            completed_by: Username
            completed_device: Device name. Must match created_device if enforced.
        """
        import json as _json
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Get audit header
            cursor.execute('SELECT * FROM inventory_audits WHERE id = ?', (audit_id,))
            audit = cursor.fetchone()
            if not audit or audit['status'] != 'active':
                return None
            
            # Device Lock Check - ensured Row access is safe
            try:
                created_device = audit['created_device']
            except (KeyError, TypeError):
                created_device = None
                
            if created_device and completed_device and created_device != completed_device:
                # Return indicating device mismatch
                return {"error": "device_mismatch", "required_device": created_device}
            
            created_at = audit['created_at']
            
            # Calculate sold_during_audit for each item from receipt_items
            cursor.execute('''
                SELECT ri.good_code, COALESCE(SUM(ri.quantity), 0) as total_sold
                FROM receipt_items ri
                JOIN receipts r ON r.id = ri.receipt_id
                WHERE r.datetime >= ? AND r.datetime <= ?
                AND r.status = 'completed'
                GROUP BY ri.good_code
            ''', (created_at, now))
            
            sold_map = {}
            for row in cursor.fetchall():
                sold_map[row['good_code']] = row['total_sold']
            
            # Also account for refunds during audit period
            cursor.execute('''
                SELECT ri.good_code, COALESCE(SUM(ri.refunded_qty), 0) as total_refunded
                FROM receipt_items ri
                JOIN receipts r ON r.id = ri.receipt_id
                WHERE r.refund_datetime >= ? AND r.refund_datetime <= ?
                GROUP BY ri.good_code
            ''', (created_at, now))
            
            refund_map = {}
            for row in cursor.fetchall():
                refund_map[row['good_code']] = row['total_refunded']
            
            # Update each audit item
            total_surplus = 0
            total_shortage = 0
            total_diff_money = 0
            
            cursor.execute('SELECT * FROM inventory_audit_items WHERE audit_id = ?', (audit_id,))
            items = cursor.fetchall()
            
            for item in items:
                code = item['good_code']
                sold = sold_map.get(code, 0)
                refunded = refund_map.get(code, 0)
                net_sold = sold - refunded
                
                expected = item['expected_qty']
                adjusted = expected - net_sold  # what should be on shelf now
                actual = item['actual_qty']
                
                if actual is not None:
                    diff = actual - adjusted
                    diff_money = diff * (item['sale_price'] or 0)
                    
                    if diff > 0:
                        total_surplus += diff
                    elif diff < 0:
                        total_shortage += abs(diff)
                    total_diff_money += diff_money
                else:
                    # For full audit: uncounted items = shortage of adjusted amount
                    is_full = audit['audit_type'] == 'full'
                    # ZERO OUT POSITIVES always in full audit (missing stuff)
                    # ZERO OUT NEGATIVES only if flag is set
                    should_zero = is_full and (adjusted > 0 or (adjusted < 0 and audit['zero_negatives']))
                    
                    if should_zero:
                        diff = -adjusted  # treated as if actual = 0
                        diff_money = diff * (item['sale_price'] or 0)
                        total_shortage += abs(diff) if diff < 0 else 0
                        total_surplus += diff if diff > 0 else 0
                        total_diff_money += diff_money
                    else:
                        diff = 0
                        diff_money = 0
                
                cursor.execute('''
                    UPDATE inventory_audit_items 
                    SET sold_during_audit = ?, adjusted_expected = ?, 
                        difference = ?, difference_money = ?
                    WHERE id = ?
                ''', (net_sold, adjusted, diff, diff_money, item['id']))
            
            # Update audit header
            cursor.execute('''
                UPDATE inventory_audits 
                SET status = 'completed', completed_at = ?, completed_by = ?,
                    completed_device = ?,
                    total_surplus = ?, total_shortage = ?, total_difference_money = ?,
                    updated_at = ?
                WHERE id = ?
            ''', (now, completed_by, completed_device, total_surplus, total_shortage, 
                  total_diff_money, now, audit_id))
        
        return self.get_audit(audit_id)
    
    def apply_audit(self, audit_id, applied_by='', applied_device=''):
        """Apply audit results to goods.quantity.
        
        Args:
            audit_id: ID
            applied_by: Username
            applied_device: Device name. Must match creator.
        """
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        import json as _json
        
        # Step 1: Read audit data
        audit = self.get_audit(audit_id)
        if not audit or audit['status'] != 'completed' or audit['applied']:
            return False
        
        # Device Lock Check
        created_device = audit.get('created_device')
        if created_device and applied_device and created_device != applied_device:
            return "device_mismatch"
        
        is_full = audit['audit_type'] == 'full'
        items = self.get_audit_items(audit_id)
        
        updated = 0
        pre_apply_snapshot = {}
        changes = {}
        
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            for item in items:
                code = item['good_code']
                actual = item['actual_qty']
                
                # For full audit: uncounted = 0
                if actual is None and is_full:
                    actual = 0.0
                    # Update local audit item record
                    adj = item.get('adjusted_expected') or item['expected_qty'] or 0
                    cursor.execute('''
                        UPDATE inventory_audit_items 
                        SET actual_qty = 0, difference = ?, difference_money = ?,
                            counted_at = ?, counted_by = ?
                        WHERE audit_id = ? AND good_code = ?
                    ''', (-adj, -adj * (item.get('sale_price') or 0),
                          now, 'system:full_audit', audit_id, code))
                elif actual is None:
                    continue
                
                # Use the DIFFERENCE for differential sync
                # difference = actual_qty - expected_qty (stored in DB)
                diff = item.get('difference') or 0
                changes[code] = (float(diff), float(actual)) # Store both for logging

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            for code, (diff, counted_actual) in changes.items():
                cursor.execute('SELECT quantity FROM goods WHERE code = ?', (code,))
                current = cursor.fetchone()
                if not current:
                    continue
                
                old_qty = current['quantity']
                pre_apply_snapshot[code] = old_qty
                
                # Differential application: current_stock + correction_delta
                # This ensures sales that happened DURING the audit are preserved.
                new_qty = float(old_qty) + diff
                
                cursor.execute('UPDATE goods SET quantity = ?, synced = 0 WHERE code = ?',
                               (new_qty, code))
                if cursor.rowcount > 0:
                    updated += 1
                
            # Mark as applied
            snapshot_json = 'PRE_APPLY:' + _json.dumps(pre_apply_snapshot)
            cursor.execute('''
                UPDATE inventory_audits 
                SET applied = 1, applied_at = ?, applied_by = ?,
                    applied_device = ?,
                    notes = CASE WHEN notes = '' THEN ? ELSE notes || char(10) || ? END,
                    updated_at = ?
                WHERE id = ?
            ''', (now, applied_by, applied_device, snapshot_json, snapshot_json, now, audit_id))
            
        return updated
    
    def cancel_audit(self, audit_id):
        """Cancel an active audit without applying changes."""
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE inventory_audits SET status = 'cancelled', completed_at = ?, updated_at = ?
                WHERE id = ? AND status = 'active'
            ''', (now, now, audit_id))
            return cursor.rowcount > 0
    
    
    def get_all_audits_sync(self, after_ts=None):
        """Get audits and their items for syncing, optionally after a timestamp.
        Uses updated_at column for proper incremental sync (no more returning all active audits)."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            if after_ts:
                # Only return audits that have actually changed since after_ts
                cursor.execute('''
                    SELECT * FROM inventory_audits 
                    WHERE updated_at > ?
                       OR (updated_at IS NULL AND (
                           created_at > ? 
                           OR (completed_at IS NOT NULL AND completed_at > ?)
                           OR (applied_at IS NOT NULL AND applied_at > ?)
                       ))
                ''', (after_ts, after_ts, after_ts, after_ts))
            else:
                cursor.execute('SELECT * FROM inventory_audits')
            
            audits = [dict(r) for r in cursor.fetchall()]
            
            # Fetch items
            for audit in audits:
                cursor.execute('SELECT * FROM inventory_audit_items WHERE audit_id = ?', (audit['id'],))
                items = [dict(r) for r in cursor.fetchall()]
                # remove internal db ID
                for it in items:
                    it.pop('id', None)
                audit['items'] = items
            return audits

    def upsert_audits_batch(self, audits_data):
        """Upsert a batch of audits from sync target.
        Returns count of actually changed audits (not just received)."""
        upserted = 0
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            for audit in audits_data:
                audit_id = audit['id']
                
                # If audit is deleted, soft-delete it locally to propagate the state properly
                if audit.get('status') == 'deleted':
                    cursor.execute("UPDATE inventory_audits SET status = 'deleted', updated_at = ? WHERE id = ?", 
                                   (audit.get('updated_at') or now, audit_id))
                    cursor.execute('DELETE FROM inventory_audit_items WHERE audit_id = ?', (audit_id,))
                    upserted += 1
                    continue
                
                # Check if we already have this audit with identical data
                cursor.execute('SELECT status, applied, counted_items, completed_at, applied_at, updated_at FROM inventory_audits WHERE id = ?', (audit_id,))
                existing = cursor.fetchone()
                if existing:
                    # Timestamp-based merge: the NEWER version always wins.
                    # This prevents the Secondary's stale applied=0 audit from
                    # overwriting the Master's applied=1 state.
                    existing_ts = str(existing.get('updated_at') or '')
                    incoming_ts = str(audit.get('updated_at') or '')
                    if existing_ts and incoming_ts and incoming_ts <= existing_ts:
                        continue  # Existing data is newer or same age — preserve it
                    
                    # Skip if nothing has actually changed (saves a DELETE+INSERT)
                    if (existing['status'] == audit.get('status', 'active') and
                        existing['applied'] == audit.get('applied', 0) and
                        existing['counted_items'] == audit.get('counted_items', 0) and
                        existing['completed_at'] == audit.get('completed_at') and
                        existing['applied_at'] == audit.get('applied_at')):
                        continue  # No changes — skip this audit entirely

                # Delete old data and re-insert (safe upsert)
                cursor.execute('DELETE FROM inventory_audit_items WHERE audit_id = ?', (audit_id,))
                cursor.execute('DELETE FROM inventory_audits WHERE id = ?', (audit_id,))
                
                cursor.execute('''
                    INSERT INTO inventory_audits (
                        id, created_at, completed_at, status, audit_type, filter_criteria,
                        created_by, completed_by, snapshot_total_items, counted_items,
                        total_surplus, total_shortage, total_difference_money,
                        applied, applied_at, applied_by, notes, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    audit_id, audit.get('created_at', ''), audit.get('completed_at'),
                    audit.get('status', 'active'), audit.get('audit_type', 'full'),
                    audit.get('filter_criteria', '{}'), audit.get('created_by', ''),
                    audit.get('completed_by', ''), audit.get('snapshot_total_items', 0),
                    audit.get('counted_items', 0), audit.get('total_surplus', 0),
                    audit.get('total_shortage', 0), audit.get('total_difference_money', 0),
                    audit.get('applied', 0), audit.get('applied_at'),
                    audit.get('applied_by', ''), audit.get('notes', ''),
                    audit.get('updated_at') or now
                ))
                
                for item in audit.get('items', []):
                    cursor.execute('''
                        INSERT INTO inventory_audit_items (
                            audit_id, good_id, good_code, good_name, good_barcode,
                            sale_price, purchase_price, expected_qty, actual_qty,
                            sold_during_audit, adjusted_expected, difference,
                            difference_money, counted_at, counted_by
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        audit_id, item.get('good_id', ''), item.get('good_code', ''),
                        item.get('good_name', ''), item.get('good_barcode', ''),
                        item.get('sale_price', 0), item.get('purchase_price', 0),
                        item.get('expected_qty', 0), item.get('actual_qty'),
                        item.get('sold_during_audit', 0), item.get('adjusted_expected', 0),
                        item.get('difference', 0), item.get('difference_money', 0),
                        item.get('counted_at'), item.get('counted_by', '')
                    ))
                upserted += 1
        return upserted

    def get_all_audits(self, limit=50):
        """Get all audits ordered by date desc (excluding soft-deleted)."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM inventory_audits 
                WHERE status != 'deleted'
                ORDER BY created_at DESC LIMIT ?
            ''', (limit,))
            return [dict(r) for r in cursor.fetchall()]
    
    def get_active_audit(self):
        """Get currently active audit (if any)."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM inventory_audits WHERE status = 'active' LIMIT 1")
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_audit_summary(self, audit_id):
        """Get summary statistics for completed audit."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('SELECT * FROM inventory_audits WHERE id = ?', (audit_id,))
            audit = cursor.fetchone()
            if not audit:
                return None
            
            cursor.execute('''
                SELECT 
                    COUNT(*) as total_items,
                    SUM(CASE WHEN actual_qty IS NOT NULL THEN 1 ELSE 0 END) as counted,
                    SUM(CASE WHEN actual_qty IS NOT NULL AND difference = 0 THEN 1 ELSE 0 END) as matches,
                    SUM(CASE WHEN actual_qty IS NOT NULL AND difference > 0 THEN 1 ELSE 0 END) as surplus_items,
                    SUM(CASE WHEN actual_qty IS NOT NULL AND difference < 0 THEN 1 ELSE 0 END) as shortage_items,
                    SUM(CASE WHEN actual_qty IS NOT NULL AND difference > 0 THEN difference ELSE 0 END) as surplus_qty,
                    SUM(CASE WHEN actual_qty IS NOT NULL AND difference < 0 THEN ABS(difference) ELSE 0 END) as shortage_qty,
                    SUM(CASE WHEN actual_qty IS NOT NULL AND difference > 0 THEN difference_money ELSE 0 END) as surplus_money,
                    SUM(CASE WHEN actual_qty IS NOT NULL AND difference < 0 THEN ABS(difference_money) ELSE 0 END) as shortage_money,
                    SUM(CASE WHEN actual_qty IS NOT NULL THEN difference_money ELSE 0 END) as net_difference_money
                FROM inventory_audit_items WHERE audit_id = ?
            ''', (audit_id,))
            
            stats = dict(cursor.fetchone())
            stats['audit'] = dict(audit)
            return stats
    
    def delete_audit(self, audit_id):
        """Delete audit. If it was applied, rollback quantities accounting for sales since apply."""
        import json as _json
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                
                # Fetch applied_at from existing audit record
                cursor.execute('SELECT applied_at, notes FROM inventory_audits WHERE id = ?', (audit_id,))
                row = cursor.fetchone()
                if not row:
                    return False
                
                applied_at = row['applied_at']
                notes = row['notes'] or ''
                
                # If applied, perform rollback
                if applied_at:
                    # Parse pre_apply snapshot from notes
                    pre_apply = {}
                    if 'PRE_APPLY:' in notes:
                        try:
                            p_part = notes.split('PRE_APPLY:')[1].split('\n')[0]
                            pre_apply = _json.loads(p_part)
                        except: pass
                    
                    if pre_apply:
                        # Get sales/refunds since application to correctly restore current status
                        cursor.execute('''
                            SELECT ri.good_code, SUM(ri.quantity) as sold
                            FROM receipts r
                            JOIN receipt_items ri ON r.id = ri.receipt_id
                            WHERE (r.datetime >= ? OR r.refund_datetime >= ?) AND r.status = 'completed'
                            GROUP BY ri.good_code
                        ''', (applied_at, applied_at))
                        sales_refunds = {r['good_code']: r['sold'] for r in cursor.fetchall()}
                        
                        # Rollback each item
                        for code, old_qty in pre_apply.items():
                            sold_net = sales_refunds.get(code, 0)
                            restored_qty = old_qty - sold_net
                            
                            # Actual stock update
                            cursor.execute('''
                                UPDATE goods SET quantity = ?
                                WHERE code = ?
                            ''', (restored_qty, code))
                            
                # Soft delete audit records so sync clients pick up the deletion
                cursor.execute("UPDATE inventory_audits SET status = 'deleted', completed_at = ?, updated_at = ? WHERE id = ?", (now, now, audit_id))
                return True
        except Exception as e:
            print(f"Error deleting audit: {e}")
            return False

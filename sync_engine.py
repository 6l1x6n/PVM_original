# -*- coding: utf-8 -*-
"""
PVM.core - Sync Engine (MEGA folder transport)
===============================================
Per ТЗ §7.5: transport-agnostic orchestrator. Registers entities, manages the
outbox queue (sync_log), consumes remote JSONL files from a shared folder
(MEGA-synced), writes periodic full snapshots and cleans stale files.

Protocol v2 (all files live under the store's own MEGA folder):
  outbox/{device}_{ts}_{uuid}.jsonl      — batched changes (JSONL, checksummed)
  snapshots/snapshot_{entity}_{ts}.jsonl — full catalog dumps (weekly)
  acks/{file}.jsonl.ack                  — per-device apply acknowledgements

Safety rules:
  - writers use tmp+rename (transport) — readers never see partial files
  - every change carries event_id + store_id; consumers stage them into a
    transactional inbox (sync_inbox) and apply each event exactly once
  - partial/corrupted JSONL (bad lines, checksum mismatch) is rejected whole
  - applying a change never re-enqueues it (suppress_sync_log in handlers)
  - the janitor deletes an outbox file ONLY when every known device has
    acknowledged it AND it is older than the grace period — no lost changes
  - conflicts: LWW by updated_at; cashier owns goods.quantity
"""

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import db_sqlite
from sync_transport import SyncTransport
from transport_local import LocalFolderTransport
from sync_queue import SyncQueue
from sync_registry import register_core_entities


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SyncEngine:
    """Orchestrates folder-based sync between devices of one store."""

    OUTBOX_GRACE_DAYS = 35
    SNAPSHOT_GRACE_DAYS = 45
    SNAPSHOT_KEEP = 2
    SNAPSHOT_INTERVAL_DAYS = 7
    MAX_BACKOFF_SEC = 300
    BATCH_SIZE = 100
    INBOX_MAX_ATTEMPTS = 10

    def __init__(self, db_manager, device_key: str,
                 folder_path: str, device_type: str = "cashier",
                 sync_interval: int = 10):
        self.db = db_manager
        self.device_key = str(device_key or "")
        self.device_type = (device_type or "cashier").lower()
        self.preserve_quantity = (self.device_type == "cashier")
        self.interval = max(5, min(int(sync_interval or 10), 600))

        self.transport: SyncTransport = LocalFolderTransport(folder_path)
        self.queue = SyncQueue(
            db_manager, self.transport, self.device_key,
            batch_size=self.BATCH_SIZE,
            receipts_getter=self._get_unsynced_receipts,
            receipts_marker=self._mark_receipt_synced,
            purchases_getter=self._get_unsynced_purchases,
            purchases_marker=self._mark_purchase_synced,
            writeoffs_getter=self._get_unsynced_writeoffs,
            writeoffs_marker=self._mark_writeoff_synced,
        )

        self.goods_mgr = db_sqlite.GoodsManagerSQL(db_manager)
        self.partners_mgr = db_sqlite.PartnersManagerSQL(db_manager)
        self.receipts_mgr = db_sqlite.ReceiptsManagerSQL(
            db_manager, device_prefix=self.device_key[:4])
        self.purchases_mgr = db_sqlite.PurchasesManagerSQL(
            db_manager, device_prefix=self.device_key[:4])
        self.writeoffs_mgr = db_sqlite.WriteoffsManagerSQL(
            db_manager, device_prefix=self.device_key[:4])
        self.audits_mgr = db_sqlite.InventoryAuditManagerSQL(
            db_manager, device_prefix=self.device_key[:4])

        self._handlers = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._requested = False
        self._fail_streak = 0
        self.last_sync = None
        self.last_error = None
        self.last_flushed = 0
        self.last_applied = 0
        self._pending_rebase = None
        self._rebase_cutoff = ""

        register_core_entities(self)

        # Snapshot schedule survives restarts via sync_markers
        self._snapshot_marker = db_sqlite.MarkersManagerSQL(db_manager)
        self._last_snapshot_iso = self._snapshot_marker.get_marker(
            "mega_last_snapshot", "")
        self._rebase_cutoff = self._snapshot_marker.get_marker(
            "stock_rebase_cutoff", "")
        self._register_device(self.device_key)

    # ------------------------------------------------------------------
    # registration / logging
    # ------------------------------------------------------------------
    def register(self, entity: str, handler):
        """Register an entity apply-handler: handler(engine, change)
        -> True (applied) / False (transient error) / "skip" (stale)."""
        self._handlers[entity] = handler

    def _get_unsynced_receipts(self, limit=50):
        try:
            return self.receipts_mgr.get_unsynced_receipts()[:limit]
        except Exception:
            return []

    def _mark_receipt_synced(self, receipt_id):
        try:
            self.receipts_mgr.mark_receipt_synced(receipt_id)
        except Exception:
            pass

    def _get_unsynced_purchases(self, limit=50):
        try:
            return self.purchases_mgr.get_unsynced_purchases()[:limit]
        except Exception:
            return []

    def _mark_purchase_synced(self, purchase_id):
        try:
            self.purchases_mgr.mark_purchase_synced(purchase_id)
        except Exception:
            pass

    def _get_unsynced_writeoffs(self, limit=50):
        try:
            return self.writeoffs_mgr.get_unsynced_writeoffs()[:limit]
        except Exception:
            return []

    def _mark_writeoff_synced(self, writeoff_id):
        try:
            self.writeoffs_mgr.mark_writeoff_synced(writeoff_id)
        except Exception:
            pass

    def log(self, msg: str, level: str = "info"):
        try:
            print(f"[SyncEngine] {msg}")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # public API for UI
    # ------------------------------------------------------------------
    def request_sync(self):
        """Fire-and-forget: trigger a sync on the next worker tick."""
        self._requested = True

    def stop(self):
        """Signal the engine to stop (set by the shutdown coordinator)."""
        self._stop_event.set()

    def is_stopped(self) -> bool:
        return self._stop_event.is_set()

    def request_full_resync(self):
        """Forget applied files so all remote files are re-applied (LWW makes
        it idempotent) and pull fresh snapshots."""
        try:
            with self.db.get_connection() as conn:
                conn.cursor().execute("DELETE FROM sync_applied_files")
        except Exception:
            pass
        try:
            with self.db.get_connection() as conn:
                # stock_delta events must NOT be re-applied (they are
                # additive — a full resync would double-count stock).
                conn.cursor().execute(
                    "UPDATE sync_inbox SET applied = 0 WHERE entity_type != 'stock_delta'")
        except Exception:
            pass
        self._snapshot_marker.set_marker("mega_last_snapshot", "")
        self._snapshot_marker.set_marker("stock_rebase_v2", "")
        self._snapshot_marker.set_marker("stock_rebase_cutoff", "")
        self._rebase_cutoff = ""
        self._last_snapshot_iso = ""
        self._requested = True

    def pending_count(self) -> int:
        return self.queue.pending_count()

    def status(self) -> dict:
        return {
            "folder": getattr(self.transport, "base_dir", ""),
            "device_type": self.device_type,
            "pending": self.pending_count(),
            "last_sync": self.last_sync,
            "last_error": str(self.last_error) if self.last_error else None,
            "last_flushed": self.last_flushed,
            "last_applied": self.last_applied,
        }

    def due(self) -> bool:
        """True if a sync cycle should run now."""
        if self._stop_event.is_set():
            return False
        if self._requested:
            return True
        if self.last_sync is None:
            return True
        if self.last_error:
            backoff = min(self.interval * (2 ** self._fail_streak),
                          self.MAX_BACKOFF_SEC)
            elapsed = (datetime.now() - self.last_sync).total_seconds()
            return elapsed >= backoff
        elapsed = (datetime.now() - self.last_sync).total_seconds()
        return elapsed >= self.interval

    # ------------------------------------------------------------------
    # main cycle
    # ------------------------------------------------------------------
    def sync_once(self) -> dict:
        """One full cycle: flush outbox → consume remote → snapshots → janitor."""
        if not self._lock.acquire(blocking=False):
            return {"status": "busy"}
        try:
            self._requested = False
            if not self.transport.connect():
                raise RuntimeError("Транспорт недоступен (папка MEGA)")

            self.last_flushed = self.queue.flush(self.BATCH_SIZE)
            self.last_flushed += self.queue.flush_receipts(50)
            self.last_flushed += self.queue.flush_purchases(50)
            self.last_flushed += self.queue.flush_writeoffs(50)
            self.last_flushed += self._flush_audits()
            if self._stop_event.is_set():
                return {"status": "stopped"}
            self._consume_outbox()

            if self._snapshot_due():
                if self._write_snapshots():
                    self._last_snapshot_iso = _now_iso()
                    self._snapshot_marker.set_marker(
                        "mega_last_snapshot", self._last_snapshot_iso)
            if self._stop_event.is_set():
                return {"status": "stopped"}
            self._consume_snapshots()
            self._apply_pending_rebase()
            if self._stop_event.is_set():
                return {"status": "stopped"}
            self.last_applied = self._apply_inbox()
            if self._stop_event.is_set():
                return {"status": "stopped"}
            self._janitor()

            self.last_sync = datetime.now()
            self.last_error = None
            self._fail_streak = 0
            return {"status": "ok", "flushed": self.last_flushed,
                    "applied": self.last_applied}
        except Exception as e:
            self.last_sync = datetime.now()
            self.last_error = e
            self._fail_streak += 1
            self.log(f"sync failed ({self._fail_streak}): {e}", "error")
            return {"status": "error", "error": str(e)}
        finally:
            self._lock.release()

    def _flush_audits(self) -> int:
        """Push changed audits (with items) as one JSONL file.

        Audits carry no sync_log trigger; an incremental marker (updated_at
        of the last pushed audit) keeps the push cheap and complete.
        """
        try:
            marker = self._snapshot_marker.get_marker("mega_last_audits_sync", "")
            rows = self.audits_mgr.get_all_audits_sync(after_ts=marker or None)
            if not rows:
                return 0
            entries = [{
                "entity_type": "audits",
                "entity_id": a["id"],
                "operation": "UPDATE",
                "data": json.dumps(a, ensure_ascii=False, default=str),
                "created_at": a.get("updated_at") or a.get("created_at") or _now_iso(),
            } for a in rows]
            payload = self.queue.serialize_entries(entries)
            name = self.queue.make_file_name(self.device_key)
            if not self.transport.upload(name, payload):
                return 0
            newest = max((a.get("updated_at") or "") for a in rows)
            self._snapshot_marker.set_marker("mega_last_audits_sync", newest)
            return len(entries)
        except Exception as e:
            self.log(f"flush audits failed: {e}", "error")
            return 0

    # ------------------------------------------------------------------
    # consume remote changes (outbox)
    # ------------------------------------------------------------------
    def _is_applied(self, file_name: str) -> bool:
        try:
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT 1 FROM sync_applied_files WHERE file_name = ?",
                            (file_name,))
                return cur.fetchone() is not None
        except Exception:
            return False

    def _record_applied(self, file_name: str, content: bytes):
        fhash = hashlib.sha256(content).hexdigest()[:32]
        try:
            with self.db.get_connection() as conn:
                conn.cursor().execute('''
                    INSERT OR REPLACE INTO sync_applied_files
                    (file_name, file_hash, applied_at) VALUES (?, ?, ?)
                ''', (file_name, fhash, _now_iso()))
        except Exception:
            pass

    def _register_device(self, device_key: str):
        if not device_key:
            return
        now = _now_iso()
        try:
            with self.db.get_connection() as conn:
                conn.cursor().execute('''
                    INSERT INTO sync_device_registry (device_key, first_seen, last_seen)
                    VALUES (?, ?, ?)
                    ON CONFLICT(device_key) DO UPDATE SET last_seen = excluded.last_seen
                ''', (device_key, now, now))
        except Exception:
            pass

    def _known_devices(self) -> list:
        """Live devices in this folder: registry entries seen recently plus
        devices found in fresh ack files (a device that only consumes changes
        never shows up in the registry of the file author — without this it
        would block the janitor forever). Stale entries are ignored so a
        retired device does not block outbox cleanup permanently."""
        devices = set()
        cutoff = (datetime.now() - timedelta(days=60)).isoformat(timespec="seconds")
        try:
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute('SELECT device_key FROM sync_device_registry WHERE last_seen >= ?',
                            (cutoff,))
                devices.update(r["device_key"] for r in cur.fetchall())
        except Exception:
            pass
        try:
            ack_cutoff = time.time() - 60 * 86400
            for ack_name in self.transport.list_files("acks/"):
                st = self.transport.stat(f"acks/{ack_name}")
                if st and st[1] < ack_cutoff:
                    continue
                content = self.transport.download(f"acks/{ack_name}")
                if not content:
                    continue
                try:
                    d = json.loads(content.decode("utf-8"))
                    if d.get("device_key"):
                        devices.add(d["device_key"])
                except Exception:
                    pass
        except Exception:
            pass
        devices.add(self.device_key)
        return sorted(devices)

    def _write_ack(self, fname: str, count: int):
        """Announce this device applied an outbox file (janitor safety)."""
        try:
            payload = json.dumps(
                {"device_key": self.device_key, "applied": count,
                 "at": _now_iso()}).encode("utf-8")
            self.transport.upload(f"acks/{fname}.ack", payload)
        except Exception:
            pass

    def _parse_jsonl(self, content: bytes):
        """Return (manifest, [change, ...]) or (None, None) on invalid content.

        Protocol v2 files are rejected entirely when any line is unparseable
        or the manifest checksum/total do not match (no partial applies).
        Legacy v1 files (no checksum) are accepted line-by-line as before.
        """
        try:
            text = content.decode("utf-8")
            lines = [ln for ln in text.splitlines() if ln.strip()]
            if not lines:
                return None, None
            manifest = json.loads(lines[0])
            if not isinstance(manifest, dict) or "__manifest__" not in manifest:
                return None, None
            m = manifest["__manifest__"]
            changes = []
            for ln in lines[1:]:
                try:
                    changes.append(json.loads(ln))
                except Exception:
                    return None, None  # partial/corrupt file — reject whole
            if m.get("protocol") == 2 or m.get("checksum"):
                if not m.get("checksum"):
                    return None, None
                payload = ("\n".join(lines[1:]) + "\n").encode("utf-8")
                if hashlib.sha256(payload).hexdigest() != m.get("checksum"):
                    return None, None
                if m.get("total") is not None and int(m.get("total")) != len(changes):
                    return None, None
            return m, changes
        except Exception:
            return None, None

    def _apply_change(self, change: dict):
        """Apply one change. Returns True / False (error) / "skip" (stale)."""
        entity = change.get("entity")
        handler = self._handlers.get(entity)
        if not handler:
            return False
        try:
            return handler(self, change)
        except Exception as e:
            self.log(f"handler error {entity}: {e}", "error")
            return False

    def _stage_changes(self, fname: str, manifest: dict, changes: list) -> bool:
        """Insert all changes of one file into the transactional inbox.

        Dedup is by event_id (INSERT OR IGNORE): re-delivered files never
        stage duplicates. Returns False when staging itself failed (retry
        next cycle)."""
        source = manifest.get("source_device") or ""
        now = _now_iso()
        try:
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                for idx, change in enumerate(changes):
                    entity = change.get("entity") or ""
                    if entity not in self._handlers:
                        continue
                    event_id = change.get("event_id")
                    if not event_id:
                        # Legacy v1 file: deterministic per-file event ids
                        event_id = hashlib.sha256(
                            f"{fname}:{idx}".encode()).hexdigest()[:32]
                    try:
                        data = json.dumps(
                            change.get("data") or {}, ensure_ascii=False, default=str)
                    except Exception:
                        data = "{}"
                    cur.execute('''
                        INSERT OR IGNORE INTO sync_inbox
                        (event_id, source_device, file_name, entity_type, entity_id,
                         operation, data, updated_at, received_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (event_id, source, fname, entity,
                          str(change.get("entity_id") or ""),
                          (change.get("operation") or "UPDATE").upper(),
                          data, change.get("updated_at") or "", now))
            return True
        except Exception as e:
            self.log(f"stage {fname} failed: {e}", "error")
            return False

    def _consume_outbox(self) -> int:
        staged = 0
        for fname in self.transport.list_files("outbox/"):
            if not fname.endswith(".jsonl"):
                continue
            if self._is_applied(fname):
                continue
            content = self.transport.download(f"outbox/{fname}")
            if not content:
                continue
            manifest, changes = self._parse_jsonl(content)
            if not manifest:
                self.log(f"outbox {fname}: invalid content rejected", "error")
                continue
            # Self-sync exclusion: skip files written by this device
            if manifest.get("source_device") == self.device_key:
                self._record_applied(fname, content)
                continue
            self._register_device(manifest.get("source_device") or "")
            if self._stage_changes(fname, manifest, changes):
                self._record_applied(fname, content)
                self._write_ack(fname, len(changes))
                staged += len(changes)
            else:
                self.log(f"outbox {fname}: staging failed, will retry", "error")
        return staged

    # ------------------------------------------------------------------
    # inbox apply (exactly-once, retried per event)
    # ------------------------------------------------------------------
    def _apply_inbox(self) -> int:
        applied = 0
        while True:
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute('''
                    SELECT * FROM sync_inbox
                    WHERE applied = 0 AND attempts < ?
                    ORDER BY received_at ASC, event_id ASC LIMIT 50
                ''', (self.INBOX_MAX_ATTEMPTS,))
                rows = [dict(r) for r in cur.fetchall()]
            if not rows:
                break
            for row in rows:
                change = {
                    "entity": row["entity_type"],
                    "entity_id": row["entity_id"],
                    "operation": row["operation"],
                    "updated_at": row["updated_at"],
                    "data": {},
                }
                try:
                    change["data"] = json.loads(row["data"] or "{}")
                except Exception:
                    change["data"] = {}
                if (row["entity_type"] == "stock_delta" and self._rebase_cutoff
                        and (row["updated_at"] or "") <= self._rebase_cutoff):
                    # Movement predates the rebase snapshot — its effect is
                    # already included in the rebased absolute quantities.
                    with self.db.get_connection() as conn:
                        conn.cursor().execute(
                            "UPDATE sync_inbox SET applied = 1, last_error = 'rebase' WHERE event_id = ?",
                            (row["event_id"],))
                    continue
                result = self._apply_change(change)
                with self.db.get_connection() as conn:
                    cur = conn.cursor()
                    if result is True:
                        cur.execute(
                            "UPDATE sync_inbox SET applied = 1, last_error = '' WHERE event_id = ?",
                            (row["event_id"],))
                        applied += 1
                    elif result == "skip":
                        # Permanently stale (LWW): no point retrying.
                        cur.execute(
                            "UPDATE sync_inbox SET applied = -1, last_error = 'stale' WHERE event_id = ?",
                            (row["event_id"],))
                    else:
                        cur.execute('''
                            UPDATE sync_inbox
                            SET attempts = attempts + 1, last_error = ?
                            WHERE event_id = ?
                        ''', ("apply failed", row["event_id"]))
        return applied

    def _apply_pending_rebase(self):
        """Apply the one-time stock rebase collected from a cashier snapshot.

        Runs BEFORE `_apply_inbox` in the same cycle: deltas already included
        in the snapshot's absolute quantities (movement time <= snapshot
        generated_at_utc) are skipped afterwards via the stored cutoff, so
        they are neither double-counted nor lost. The markers are set only
        after a successful apply; a failed rebase is retried on the next
        cashier snapshot (or after a full resync, which re-considers
        already-applied snapshot files).
        """
        item = self._pending_rebase
        if not item:
            return
        self._pending_rebase = None
        rows, cutoff = item
        try:
            with self.db.suppress_sync_log():
                with self.db.get_connection() as conn:
                    cur = conn.cursor()
                    for row in rows:
                        data = row.get("data") or {}
                        code = row.get("entity_id") or data.get("code")
                        qty = data.get("quantity")
                        if not code or qty is None:
                            continue
                        try:
                            cur.execute('UPDATE goods SET quantity = ? WHERE code = ?',
                                        (float(qty), str(code)))
                        except (ValueError, TypeError):
                            continue
            self._rebase_cutoff = cutoff or ""
            self._snapshot_marker.set_marker("stock_rebase_v2", _now_iso())
            self._snapshot_marker.set_marker("stock_rebase_cutoff", self._rebase_cutoff)
            self.log(f"stock rebase applied ({len(rows)} rows, cutoff {cutoff})")
        except Exception as e:
            self.log(f"stock rebase failed: {e}", "error")

    # ------------------------------------------------------------------
    # snapshots
    # ------------------------------------------------------------------
    def _snapshot_due(self) -> bool:
        if not self._last_snapshot_iso:
            return True
        try:
            last = datetime.fromisoformat(self._last_snapshot_iso)
            return (datetime.now() - last) >= timedelta(days=self.SNAPSHOT_INTERVAL_DAYS)
        except Exception:
            return True

    def _write_snapshots(self) -> bool:
        ok_any = False
        for entity, rows in (("goods", self._snapshot_rows_goods()),
                             ("partners", self._snapshot_rows_partners()),
                             ("receipts", self._snapshot_rows_receipts()),
                             ("purchases", self._snapshot_rows_purchases()),
                             ("writeoffs", self._snapshot_rows_writeoffs()),
                             ("audits", self._snapshot_rows_audits())):
            if rows is None:
                continue
            payload = self._snapshot_payload(entity, rows)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            name = f"snapshots/snapshot_{entity}_{ts}.jsonl"
            if self.transport.upload(name, payload):
                ok_any = True
                self.log(f"snapshot {entity}: {len(rows)} rows -> {name}")
        return ok_any

    def _snapshot_rows_goods(self):
        try:
            return [{"entity_id": g["code"], "updated_at": g.get("updated_at", ""),
                     "data": g} for g in self.goods_mgr.get_all_goods()]
        except Exception as e:
            self.log(f"snapshot goods failed: {e}", "error")
            return None

    def _snapshot_rows_partners(self):
        try:
            return [{"entity_id": p["id"], "updated_at": p.get("updated_at", ""),
                     "data": p} for p in self.partners_mgr.get_all_partners()]
        except Exception as e:
            self.log(f"snapshot partners failed: {e}", "error")
            return None

    def _snapshot_rows_receipts(self):
        try:
            return [{"entity_id": r["id"],
                     "updated_at": r.get("updated_at") or r.get("datetime", ""),
                     "data": r} for r in self.receipts_mgr.get_all_receipts()]
        except Exception as e:
            self.log(f"snapshot receipts failed: {e}", "error")
            return None

    def _snapshot_rows_purchases(self):
        try:
            return [{"entity_id": p["id"],
                     "updated_at": p.get("updated_at") or p.get("datetime", ""),
                     "data": p} for p in self.purchases_mgr.get_all_purchases()]
        except Exception as e:
            self.log(f"snapshot purchases failed: {e}", "error")
            return None

    def _snapshot_rows_writeoffs(self):
        try:
            return [{"entity_id": w["id"],
                     "updated_at": w.get("updated_at") or w.get("datetime", ""),
                     "data": w} for w in self.writeoffs_mgr.get_all_writeoffs()]
        except Exception as e:
            self.log(f"snapshot writeoffs failed: {e}", "error")
            return None

    def _snapshot_rows_audits(self):
        try:
            return [{"entity_id": a["id"],
                     "updated_at": (a.get("updated_at") or a.get("completed_at")
                                    or a.get("applied_at") or a.get("created_at") or ""),
                     "data": a} for a in self.audits_mgr.get_all_audits_sync()]
        except Exception as e:
            self.log(f"snapshot audits failed: {e}", "error")
            return None

    def _snapshot_payload(self, entity: str, rows: list) -> bytes:
        change_lines = []
        for row in rows:
            change_lines.append(json.dumps(row, ensure_ascii=False, default=str))
        payload = "\n".join(change_lines) + "\n"
        checksum = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        manifest = {
            "__manifest__": {
                "type": "snapshot",
                "entity": entity,
                "protocol": 2,
                "source_device": self.device_key,
                "device_type": self.device_type,
                "generated_at": _now_iso(),
                "generated_at_utc": _now_utc(),
                "total": len(rows),
                "checksum": checksum,
                "version": 2,
            }
        }
        lines = [json.dumps(manifest, ensure_ascii=False)] + change_lines
        return ("\n".join(lines) + "\n").encode("utf-8")

    def _consume_snapshots(self) -> int:
        staged = 0
        for fname in self.transport.list_files("snapshots/"):
            if not fname.endswith(".jsonl"):
                continue
            full_name = f"snapshots/{fname}"
            if self._is_applied(full_name):
                continue
            content = self.transport.download(full_name)
            if not content:
                continue
            manifest, rows = self._parse_jsonl(content)
            if not manifest or manifest.get("type") != "snapshot":
                continue
            if manifest.get("source_device") == self.device_key:
                self._record_applied(full_name, content)
                continue
            entity = manifest.get("entity")
            if entity not in self._handlers:
                self._record_applied(full_name, content)
                continue
            # Stock rebase (delta-protocol bootstrap): the first goods
            # snapshot from the cashier after the protocol switch becomes the
            # absolute stock base. Applied BEFORE the inbox (see
            # _apply_pending_rebase); deltas already included in the snapshot
            # are then skipped by the rebase cutoff.
            if (entity == "goods" and manifest.get("device_type") == "cashier"
                    and not self._snapshot_marker.get_marker("stock_rebase_v2", "")):
                self._pending_rebase = (rows, manifest.get("generated_at_utc") or "")
            changes = []
            for idx, row in enumerate(rows):
                if not isinstance(row, dict):
                    continue
                # Deterministic event id: re-delivery can never duplicate
                event_id = hashlib.sha256(
                    f"snap:{entity}:{row.get('entity_id')}:{row.get('updated_at')}"
                    .encode()).hexdigest()[:32]
                changes.append({
                    "event_id": event_id,
                    "entity": entity,
                    "entity_id": row.get("entity_id"),
                    "operation": "UPDATE",
                    "updated_at": row.get("updated_at"),
                    "data": row.get("data", {}),
                })
            if self._stage_changes(full_name, manifest, changes):
                self._record_applied(full_name, content)
                staged += len(changes)
        return staged

    # ------------------------------------------------------------------
    # janitor
    # ------------------------------------------------------------------
    def _janitor(self):
        try:
            cutoff = datetime.now() - timedelta(days=self.OUTBOX_GRACE_DAYS)
            registry = self._known_devices()
            for fname in self.transport.list_files("outbox/"):
                if not fname.endswith(".jsonl"):
                    continue
                st = self.transport.stat(f"outbox/{fname}")
                if not st:
                    continue
                if datetime.fromtimestamp(st[1]) > cutoff:
                    continue
                if self._fully_acked(fname, registry) and not self._has_pending_events(fname):
                    self.transport.delete(f"outbox/{fname}")

            # Ack cleanup: remove acks of files that are long gone
            ack_cutoff = datetime.now() - timedelta(days=self.SNAPSHOT_GRACE_DAYS)
            for fname in self.transport.list_files("acks/"):
                st = self.transport.stat(f"acks/{fname}")
                if st and datetime.fromtimestamp(st[1]) < ack_cutoff:
                    self.transport.delete(f"acks/{fname}")

            # snapshots: keep the two newest per entity
            keep = {}
            for fname in self.transport.list_files("snapshots/"):
                st = self.transport.stat(f"snapshots/{fname}")
                if not st:
                    continue
                entity = fname.split("_")[1] if fname.startswith("snapshot_") else "?"
                keep.setdefault(entity, []).append((st[1], fname))
            old_cutoff = datetime.now() - timedelta(days=self.SNAPSHOT_GRACE_DAYS)
            for entity, items in keep.items():
                items.sort(reverse=True)
                for _, fname in items[self.SNAPSHOT_KEEP:]:
                    st = self.transport.stat(f"snapshots/{fname}")
                    if st and datetime.fromtimestamp(st[1]) < old_cutoff:
                        self.transport.delete(f"snapshots/{fname}")

            # Local hygiene: synced sync_log rows and applied-file records are
            # pruned past the (generous) grace periods.
            try:
                with self.db.get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("""
                        DELETE FROM sync_log WHERE synced = 1 AND id NOT IN (
                            SELECT id FROM sync_log ORDER BY id DESC LIMIT 1000)
                    """)
                    cur.execute("""
                        DELETE FROM sync_applied_files
                        WHERE applied_at < datetime('now', '-45 days')
                    """)
                    cur.execute("""
                        DELETE FROM sync_inbox
                        WHERE applied != 0 AND received_at < datetime('now', '-45 days')
                    """)
            except Exception:
                pass
        except Exception as e:
            self.log(f"janitor: {e}", "error")

    def _fully_acked(self, fname: str, registry: list) -> bool:
        """True when every known live device (except the file's author, which
        never acks its own files) acknowledged this outbox file. When this
        device is the only known participant, only its OWN files may be
        cleaned (a single-device store must not accumulate forever)."""
        acks = set()
        for ack_name in self.transport.list_files("acks/"):
            if not ack_name.startswith(fname + ".ack"):
                continue
            content = self.transport.download(f"acks/{ack_name}")
            if not content:
                continue
            try:
                d = json.loads(content.decode("utf-8"))
                if d.get("device_key"):
                    acks.add(d.get("device_key"))
            except Exception:
                pass
        others = [k for k in registry if k != self.device_key]
        if not others:
            return fname.startswith(self.device_key + "_")
        return all(k in acks for k in others)

    def _has_pending_events(self, fname: str) -> bool:
        """True while this device still has unapplied inbox events from the
        file — the janitor must not delete a source whose changes failed to
        apply locally."""
        try:
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute('SELECT 1 FROM sync_inbox WHERE file_name = ? AND applied = 0 LIMIT 1',
                            (fname,))
                return cur.fetchone() is not None
        except Exception:
            return False


# =============================================================================
# DIAGNOSTICS (used by the Settings tab "Проверить синхронизацию" button)
# =============================================================================
def run_transport_diagnostics(folder: str):
    """Run write/read/delete tests against a folder.

    Returns a list of (name, ok, detail) tuples matching the Settings UI:
    write_test, read_test, delete_test, cloud_sync.
    """
    if not folder or not os.path.isdir(folder):
        return [
            ("write_test", False, "Папка не существует"),
            ("read_test", False, "—"),
            ("delete_test", False, "—"),
            ("cloud_sync", False, "Укажите существующую папку"),
        ]
    transport = LocalFolderTransport(folder)
    results = []
    probe_name = "diag_probe.bin"
    try:
        ok = transport.connect()
        results.append(("write_test", ok,
                        "Запись работает" if ok else "Нет доступа на запись"))
    except Exception as e:
        results.append(("write_test", False, str(e)))

    try:
        data = b"PVM_DIAG"
        up = transport.upload(probe_name, data)
        down = transport.download(probe_name)
        ok = up and down == data
        results.append(("read_test", ok,
                        "Чтение работает" if ok else "Файл не читается"))
    except Exception as e:
        results.append(("read_test", False, str(e)))

    try:
        ok = transport.delete(probe_name)
        results.append(("delete_test", ok,
                        "Удаление работает" if ok else "Не удалось удалить"))
    except Exception as e:
        results.append(("delete_test", False, str(e)))

    cloud_sync = False
    detail = "Папка доступна, но проверьте, что это синхронизируемая папка MEGA"
    mega_marker = os.path.join(folder, "..", "MEGA")
    if os.path.isdir(mega_marker) or "MEGA" in os.path.abspath(folder):
        cloud_sync = True
        detail = "Похоже на синхронизируемую папку MEGA"
    results.append(("cloud_sync", cloud_sync, detail))
    return results

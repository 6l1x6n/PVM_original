# -*- coding: utf-8 -*-
"""
PVM.core - Sync Queue
======================
Per ТЗ §7.5: buffer with dedup (collapses multiple changes of one entity),
retries and ordering. Built on top of the existing local `sync_log` table
(triggers already append every change; `get_unsynced`/`mark_synced` provide
the durable outbox). The queue serializes pending changes into JSONL batch
files and hands them to the transport.

JSONL format (protocol v2):
    line 0: manifest   {"__manifest__": {"type": "changes", "protocol": 2,
                          "store_id", "source_device", "generated_at_utc",
                          "total", "checksum"}}
    line N: change     {"event_id", "store_id", "entity", "operation",
                          "entity_id", "updated_at", "ts_utc", "data"}

`checksum` is the SHA-256 of the raw change lines: consumers reject partially
synced/corrupted files instead of half-applying them. `event_id` makes every
change idempotent on the consumer side (transactional inbox dedup).
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sync_transport import SyncTransport


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _event_id() -> str:
    return uuid.uuid4().hex


class SyncQueue:
    """Durable outbox queue backed by sync_log, emitting JSONL batch files."""

    def __init__(self, db_manager, transport: SyncTransport, device_key: str,
                 batch_size: int = 100,
                 receipts_getter=None, receipts_marker=None,
                 purchases_getter=None, purchases_marker=None,
                 writeoffs_getter=None, writeoffs_marker=None):
        self.db = db_manager
        self.transport = transport
        self.device_key = device_key
        self.batch_size = batch_size
        # Receipts/purchases/writeoffs have no sync_log trigger; they are
        # pushed via their own `synced` flags (full data incl. items).
        self.receipts_getter = receipts_getter
        self.receipts_marker = receipts_marker
        self.purchases_getter = purchases_getter
        self.purchases_marker = purchases_marker
        self.writeoffs_getter = writeoffs_getter
        self.writeoffs_marker = writeoffs_marker

    # -- serialization ----------------------------------------------------
    def _manifest(self, total: int, checksum: str) -> dict:
        return {
            "__manifest__": {
                "type": "changes",
                "protocol": 2,
                "store_id": self.device_key,
                "source_device": self.device_key,
                "generated_at": _now_iso(),
                "generated_at_utc": _now_utc(),
                "total": total,
                "checksum": checksum,
            }
        }

    @staticmethod
    def _collapse(entries: list) -> list:
        """Collapse multiple changes of the same entity: keep the last one
        (oldest-first input, so later rows win). stock_delta rows are never
        collapsed — every movement is additive and must reach consumers."""
        ordered = {}
        deltas = []
        for e in entries:
            if e["entity_type"] == "stock_delta":
                deltas.append(e)
                continue
            key = (e["entity_type"], e["entity_id"])
            ordered[key] = e
        result = list(ordered.values())
        result.sort(key=lambda e: e["id"])
        deltas.sort(key=lambda e: e["id"])
        return result + deltas

    def _change_line(self, e: dict) -> dict:
        try:
            data = json.loads(e.get("data") or "{}")
        except Exception:
            data = {}
        # LWW timestamp: the entity's own updated_at (ISO with 'T') — the
        # sync_log created_at is UTC 'YYYY-MM-DD HH:MM:SS' and must NOT be
        # used for comparisons against local ISO timestamps.
        if e["entity_type"] == "stock_delta":
            # Movement time (UTC) — used by the consumer to skip deltas that
            # are already included in the rebase snapshot.
            updated_at = (e.get("ts_utc") or e.get("created_at")
                          or data.get("updated_at") or _now_iso())
        else:
            updated_at = data.get("updated_at") or e.get("created_at") or _now_iso()
        return {
            "event_id": e.get("event_id") or _event_id(),
            "store_id": e.get("store_id") or self.device_key,
            "entity": e["entity_type"],
            "operation": (e.get("operation") or "UPDATE").upper(),
            "entity_id": str(e["entity_id"]),
            "updated_at": updated_at,
            "ts_utc": e.get("ts_utc") or _now_utc(),
            "data": data,
        }

    def serialize_entries(self, entries: list) -> bytes:
        """Serialize entries into a v2 JSONL payload with manifest checksum."""
        change_lines = []
        for e in entries:
            change_lines.append(json.dumps(self._change_line(e), ensure_ascii=False))
        payload = "\n".join(change_lines) + "\n"
        checksum = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        manifest_line = json.dumps(
            self._manifest(len(change_lines), checksum), ensure_ascii=False)
        return (manifest_line + "\n" + payload).encode("utf-8")

    def _serialize(self, entries: list) -> bytes:
        return self.serialize_entries(entries)

    @staticmethod
    def make_file_name(device_key: str) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        short = uuid.uuid4().hex[:8].upper()
        return f"outbox/{device_key}_{ts}_{short}.jsonl"

    def _file_name(self, device_key: str) -> str:
        return self.make_file_name(device_key)

    # -- outbox flush -----------------------------------------------------
    def pending_count(self) -> int:
        try:
            return self.db.sync_log.get_unsynced_count()
        except Exception:
            return 0

    def flush(self, limit: int = None) -> int:
        """Write up to `limit` unsynced sync_log rows into one JSONL file.

        Rows are marked synced ONLY after the file was uploaded successfully,
        so a failed write never loses data. Returns number of rows flushed.
        """
        limit = limit or self.batch_size
        if not self.transport.connect():
            return 0
        try:
            entries = self.db.sync_log.get_unsynced(limit=limit)
        except Exception:
            return 0
        if not entries:
            return 0

        collapsed = self._collapse(entries)
        payload = self._serialize(collapsed)
        name = self.make_file_name(self.device_key)
        if not self.transport.upload(name, payload):
            return 0

        try:
            # Mark EVERY row of the batch as synced — collapsed-away rows must
            # never be re-sent later (their payload already reached consumers).
            self.db.sync_log.mark_synced([e["id"] for e in entries])
        except Exception:
            # Marking failed: rows stay unsynced and would be re-sent with a
            # new file name — idempotent on the consumer side via LWW, so we
            # can tolerate this.
            pass
        return len(collapsed)

    def flush_flag_entities(self, entity_name: str, getter, marker,
                            limit: int = None) -> int:
        """Push unsynced rows of a flag-synced entity (receipts/purchases/
        writeoffs) as one JSONL file with full data. Returns rows flushed;
        they are marked synced only after the upload succeeded."""
        if not getter or not marker:
            return 0
        limit = limit or self.batch_size
        if not self.transport.connect():
            return 0
        try:
            rows = getter(limit)
        except Exception:
            return 0
        if not rows:
            return 0

        entries = []
        for r in rows:
            entries.append({
                "entity_type": entity_name,
                "entity_id": r.get("id") or r.get("code") or str(r),
                "operation": "UPDATE",
                "data": json.dumps(r, ensure_ascii=False, default=str),
                "created_at": r.get("updated_at") or r.get("datetime") or _now_iso(),
            })
        payload = self._serialize(entries)
        name = self.make_file_name(self.device_key)
        if not self.transport.upload(name, payload):
            return 0

        for r in rows:
            try:
                marker(r.get("id") or r.get("code") or str(r))
            except Exception:
                pass
        return len(entries)

    def flush_receipts(self, limit: int = None) -> int:
        """Push unsynced receipts (no sync_log trigger exists for receipts)."""
        return self.flush_flag_entities(
            "receipts", self.receipts_getter, self.receipts_marker, limit)

    def flush_purchases(self, limit: int = None) -> int:
        """Push unsynced purchases with full item data."""
        return self.flush_flag_entities(
            "purchases", self.purchases_getter, self.purchases_marker, limit)

    def flush_writeoffs(self, limit: int = None) -> int:
        """Push unsynced writeoffs with full item data."""
        return self.flush_flag_entities(
            "writeoffs", self.writeoffs_getter, self.writeoffs_marker, limit)

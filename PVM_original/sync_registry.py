# -*- coding: utf-8 -*-
"""
PVM.core - Sync Entity Registry
================================
Per ТЗ §7.5: registers business entities (goods, partners, receipts,
purchases, writeoffs, audits) with apply callbacks used by the SyncEngine
to apply remote changes into the local database.

Safety rules:
  - conflicts resolved LWW (last-writer-wins) by updated_at (real datetime
    comparison, not string comparison)
  - every apply runs inside db.suppress_sync_log() so applying a remote
    change never re-enqueues it into the local outbox (echo-loop prevention)
  - handlers return True (applied) / False (transient error, retry) /
    "skip" (permanently stale — mark applied without retrying)
  - stock is synced as additive stock_delta events (order-independent,
    idempotent via event_id dedup) — catalog events never touch the
    quantity of existing goods on any device
  - goods deletes are tombstones: the full OLD row is re-applied with
    is_deleted=1 so the catalog data is never lost
  - partner deletes become tombstones too (is_blocked=1): a partner with
    local sales history cannot be hard-deleted
"""

from datetime import datetime, timezone

from db_sqlite import (GoodsManagerSQL, PartnersManagerSQL, ReceiptsManagerSQL,
                       PurchasesManagerSQL, WriteoffsManagerSQL,
                       InventoryAuditManagerSQL)


def _parse_ts(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except Exception:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _lww_newer(remote_ts, local_ts):
    """True if the remote change is newer than the local row (LWW).

    ISO timestamps are compared as real datetimes (aware values are
    normalized to naive UTC); unparseable values or naive/aware mismatches
    fall back to string comparison so payloads stay deterministic.
    """
    if not local_ts:
        return True
    if not remote_ts:
        return False
    r = _parse_ts(remote_ts)
    l = _parse_ts(local_ts)
    if r is not None and l is not None:
        try:
            return r > l
        except TypeError:
            pass
    return str(remote_ts) > str(local_ts)


def _apply_goods(engine, change):
    code = str(change.get("entity_id") or change.get("data", {}).get("code") or "")
    if not code:
        return False
    op = (change.get("operation") or "UPDATE").upper()
    data = change.get("data") or {}
    goods_mgr = engine.goods_mgr
    _, local = goods_mgr.get_good(code)

    if op == "DELETE":
        # Tombstone: merge full local row with the remote payload so a
        # soft-delete never blanks name/prices/barcode. Legacy payloads may
        # only carry the code — local data fills the gaps.
        payload = {k: v for k, v in data.items() if v is not None}
        payload['code'] = code
        payload['is_deleted'] = 1
        if local:
            for k in ('name', 'pv', 'barcode', 'purchase_price', 'sale_price'):
                if payload.get(k) in (None, ''):
                    payload[k] = local.get(k)
        try:
            with engine.db.suppress_sync_log():
                goods_mgr.add_good_from_dict(
                    payload, preserve_quantity=True)
            return True
        except Exception as e:
            engine.log(f"apply goods delete {code} failed: {e}", "error")
            return False

    if local and not _lww_newer(change.get("updated_at"), local.get("updated_at")):
        return "skip"
    try:
        # Catalog-only apply: the quantity of existing goods is never touched
        # on any device — stock travels exclusively as stock_delta events.
        # A previously unknown good is inserted with the payload quantity as
        # the starting base.
        with engine.db.suppress_sync_log():
            goods_mgr.add_good_from_dict(
                data, preserve_quantity=True)
        return True
    except Exception as e:
        engine.log(f"apply goods {code} failed: {e}", "error")
        return False


def _apply_stock_delta(engine, change):
    """Apply an additive stock movement (sale/purchase/writeoff/audit/refund).

    Deltas are order-independent and idempotent via event_id dedup, so all
    devices converge to the same total stock regardless of arrival order.
    """
    code = str(change.get("entity_id") or change.get("data", {}).get("code") or "")
    data = change.get("data") or {}
    if not code:
        return False
    try:
        delta = float(data.get("delta") or 0)
    except (TypeError, ValueError):
        delta = 0.0
    if delta == 0:
        return True
    try:
        with engine.db.suppress_sync_log():
            with engine.db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute('UPDATE goods SET quantity = quantity + ? WHERE code = ?',
                            (delta, code))
                if cur.rowcount == 0:
                    # The good does not exist locally yet — retry until its
                    # catalog INSERT event arrives.
                    return False
        return True
    except Exception as e:
        engine.log(f"apply stock_delta {code} failed: {e}", "error")
        return False


def _apply_partner(engine, change):
    pid = str(change.get("entity_id") or "")
    if not pid:
        return False
    op = (change.get("operation") or "UPDATE").upper()
    data = change.get("data") or {}
    partners_mgr = engine.partners_mgr

    if op == "DELETE":
        # Tombstone: a partner with local sales history cannot be hard-deleted
        # (business rule) — mark blocked instead so catalogs stay consistent.
        try:
            local = partners_mgr.get_partner(pid)
            if local:
                with engine.db.suppress_sync_log():
                    partners_mgr.add_partner_from_dict({
                        "id": pid,
                        "name": data.get("name") or local.get("name") or "",
                        "is_blocked": 1,
                        "block_reason": "Удалён",
                        "updated_at": change.get("updated_at") or "",
                    })
            return True
        except Exception as e:
            engine.log(f"apply partner delete {pid} failed: {e}", "error")
            return False

    local = None
    try:
        local = partners_mgr.get_partner(pid)
    except Exception:
        pass
    if not _lww_newer(change.get("updated_at"), (local or {}).get("updated_at")):
        return "skip"
    try:
        with engine.db.suppress_sync_log():
            partners_mgr.add_partner_from_dict(data)
        return True
    except Exception as e:
        engine.log(f"apply partner {pid} failed: {e}", "error")
        return False


def _apply_receipt(engine, change):
    rid = change.get("entity_id") or (change.get("data") or {}).get("id")
    if not rid:
        return False
    op = (change.get("operation") or "UPDATE").upper()
    data = change.get("data") or {}
    receipts_mgr = engine.receipts_mgr

    if op == "DELETE":
        return "skip"  # receipts are never hard-deleted; refunds are UPDATEs

    local = None
    try:
        local = receipts_mgr.get_receipt_by_id(rid)
    except Exception:
        pass
    if not _lww_newer(change.get("updated_at"), (local or {}).get("updated_at")):
        return "skip"
    try:
        # Inventory side-effects travel with the goods quantity changes
        # (goods triggers), so applying a receipt must not touch stock.
        with engine.db.suppress_sync_log():
            receipts_mgr.add_receipt(data, skip_inventory=True)
        return True
    except Exception as e:
        engine.log(f"apply receipt {rid} failed: {e}", "error")
        return False


def _apply_purchase(engine, change):
    pid = str(change.get("entity_id") or (change.get("data") or {}).get("id") or "")
    if not pid:
        return False
    op = (change.get("operation") or "UPDATE").upper()
    data = change.get("data") or {}
    if not data:
        data = {"id": pid, "datetime": change.get("updated_at") or ""}
    purchases_mgr = engine.purchases_mgr

    local = None
    try:
        local = purchases_mgr.get_purchase(pid)
    except Exception:
        pass

    if op == "DELETE":
        # Tombstone: cancellation is the delete semantic for purchases.
        data.setdefault("status", "cancelled")

    if not _lww_newer(change.get("updated_at"), (local or {}).get("updated_at")):
        return "skip"
    try:
        # skip_inventory=True: stock deltas propagate via goods quantity
        # changes; applying a purchase must not double-count them.
        with engine.db.suppress_sync_log():
            purchases_mgr.add_purchase_from_sync(data, skip_inventory=True)
        return True
    except Exception as e:
        engine.log(f"apply purchase {pid} failed: {e}", "error")
        return False


def _apply_writeoff(engine, change):
    wid = str(change.get("entity_id") or (change.get("data") or {}).get("id") or "")
    if not wid:
        return False
    op = (change.get("operation") or "UPDATE").upper()
    data = change.get("data") or {}
    if not data:
        data = {"id": wid, "updated_at": change.get("updated_at") or ""}
    writeoffs_mgr = engine.writeoffs_mgr

    if op == "DELETE":
        return "skip"  # writeoffs are never hard-deleted

    local = None
    try:
        local = writeoffs_mgr.get_writeoff_by_id(wid)
    except Exception:
        pass
    if not _lww_newer(change.get("updated_at"), (local or {}).get("updated_at")):
        return "skip"
    try:
        with engine.db.suppress_sync_log():
            writeoffs_mgr.add_writeoff(data)
        return True
    except Exception as e:
        engine.log(f"apply writeoff {wid} failed: {e}", "error")
        return False


def _apply_audit(engine, change):
    aid = str(change.get("entity_id") or (change.get("data") or {}).get("id") or "")
    if not aid:
        return False
    data = change.get("data") or {}
    if not data:
        data = {"id": aid, "updated_at": change.get("updated_at") or ""}
    try:
        # upsert_audits_batch merges by updated_at internally (LWW) and
        # handles 'deleted' status tombstones.
        with engine.db.suppress_sync_log():
            engine.audits_mgr.upsert_audits_batch([data])
        return True
    except Exception as e:
        engine.log(f"apply audit {aid} failed: {e}", "error")
        return False


def register_core_entities(engine):
    """Register all v2 entities (goods, stock_delta, partners, receipts,
    purchases, writeoffs, audits)."""
    engine.register("goods", _apply_goods)
    engine.register("stock_delta", _apply_stock_delta)
    engine.register("partners", _apply_partner)
    engine.register("receipts", _apply_receipt)
    engine.register("purchases", _apply_purchase)
    engine.register("writeoffs", _apply_writeoff)
    engine.register("audits", _apply_audit)

# PVM Supabase Audit And Migration

Status: audit completed; preparation scripts are being applied in stages. The
old tariff trigger remains active until explicit cutover.

No `DROP`, `DELETE`, function replacement, trigger change, cron change, or RLS change was executed during the audit.

## Client Usage (v3.11.76)

Актуальные таблицы, которые использует клиент:

| Таблица | Что туда пишется | Рост |
|---|---|---|
| `users` | 1 строка на устройство (heartbeat `last_seen` раз в 2ч) | нет |
| `store_activation` | 1 строка на магазин (период активации) — клиент только читает | нет |
| `notifications` | уведомления (создаются админом) | почти нет |
| `pricing` | клиент читает напрямую (тарифный бейдж) | нет |
| `session_history` | **итог каждого прогона PV (INSERT, без очистки, без idempotency-ключа)** | **растёт** |

Ключевые факты клиента:

- `db.fetch_notifications()` выполняется **ОДИН раз на процесс** (`_notifications_fetched_once`).
  **`db.reset_notifications_cache()` НЕ существует** — кэш уведомлений не сбрасывается при
  смене пользователя (известное ограничение).
- **`pvbot_runs`/`pvbot_events` клиент НЕ использует** — таблицы созданы `pvbot_logs.sql`,
  клиентский код их не пишет; `_cleanup_pvbot_logs` не существует (ретеншн 90 дней — не работает).
- `session_history` очистки не имеет — единственная растущая таблица.
- Heartbeat: `sync_with_database` (ui.py:2163) — `users.update` раз в 2 часа (7200с) +
  проверка лицензии (2 SELECT: users + store_activation).
- Запуск: credentials (1), лицензия (2), уведомления (1), pricing, version.json из Storage (~5KB, ETag→304).
- MEGA-синк устройств через Supabase **не идёт**; Supabase — лицензии, уведомления, история сессий и канал обновлений.

### Квоты (расчёт на free tier, v3.11.76)

| Показатель | Free Tier лимит | 2 магазина | 10 магазинов | 20 магазинов |
|---|---|---|---|---|
| БД (Postgres) | 500 MB | ~1-2 MB | ~5-10 MB | ~10-20 MB |
| Рост БД в год (session_history и др.) | — | ~5-10 MB | ~25-50 MB | ~50-100 MB |
| REST-запросы (~36/день/устройство) | без жёсткого лимита / в кредите | ~2,4K/мес | ~12K/мес | ~24K/мес |
| Исходящий трафик (version.json + обновления) | 5 GB/мес | ~2-5 MB/мес | ~10-25 MB/мес | ~20-50 MB/мес |
| Хранилище объектов (бакет `backend`, ~1.5MB) | 1 GB | ~1,5 MB | ~1,5 MB | ~1,5 MB |
| MAU (Auth) | 50 000 | 0 | 0 | 0 |
| Edge Functions | 500K/мес | 0 | 0 | 0 |

Вывод: все квоты free tier покрывают 20 магазинов с запасом ×10-600.
Единственный реальный рост — `session_history`; следить за частотой релизов
(каждый релиз = перекачка изменённых модулей на все устройства).

## Current Tariff Chain

```text
session_history INSERT
    -> trigger_auto_pricing
    -> auto_update_pricing()
    -> pricing INSERT/UPDATE
    -> pricing.final_fee
```

The database also contains `calculate_monthly_pricing(integer, integer)`. It uses the same old tariff scale and is not connected to a trigger or cron job in the audited output.

## Current Tariff Problems

- Thresholds `39900` to `99900` are hard-coded in old functions.
- The old calculation divides by calendar period days.
- Zero and technical sessions are not explicitly filtered.
- `final_fee` has a default of `90000`.
- `pricing` has duplicate unique indexes for `(login, month, year)`.
- `pricing` allows `anon` INSERT, UPDATE, DELETE, and TRUNCATE.
- `session_history` allows public mutation under the current `Allow all for anon` policy.
- Session upload uses ordinary INSERT without an idempotency key.
- The old trigger only responds to INSERT, not UPDATE or DELETE.

## Audited Public Tables

The application contract uses:

- `users`
- `notifications`
- `pricing`
- `session_history`
- `pvm_sync_goods`
- `pvm_sync_partners`
- `pvm_sync_receipts`
- `pvm_sync_purchases`
- `pvm_sync_writeoffs`
- `pvm_sync_audits`

The following archive tables exist but are empty:

- `pricing_archive`
- `session_history_archive`

The `pvm_sync_*` tables are also empty. The dependency audit found only their indexes and sequences, not views, foreign keys, or database functions that depend on them.

## Store Subscription Model

Billing belongs to the store login, not to individual devices. The prepared
`store_activation` table has one row per `login`:

```text
login
activation_start
activation_end
status
```

The device key identifies an individual device. Effective device access is
based on the device row existing and the store subscription:

```text
users.device_key exists
AND store_activation.status = 'active'
```

Clients may read `store_activation` to validate access, but only an
administrator changes its dates and status. `pricing_rules` remains readable
and writable only from SQL Editor/admin infrastructure.

The paid period includes `activation_end`. A store with `activation_end =
2026-08-12` is active through August 12 and expires only when:

```sql
activation_end < current_date
```

The legacy `users.status` is not used for subscription access. Replacing one
device key with `xxxx` disables that device without changing the store dates,
tariff, or access for other devices.

### Manual renewal (Table Editor)

- Renewal is done in `store_activation` only — `users.status` and
  `users.activation_start/end` do not affect the license (client reads
  `store_activation` only).
- With `store_activation_auto_renew.sql` applied, flipping `status` to `active`
  on an expired row auto-sets the period: `activation_start = current_date`,
  `activation_end = current_date + 1 month` (same behavior the client used to
  have for `users`). A future `activation_end` is never overwritten — manual
  multi-month renewals still work.
- Without the trigger the key rule is: `activation_end` must be `>= current_date`
  (`current_date > activation_end` = expired, inclusive end). Status alone is
  not enough.
- Date format accepted by the client: `YYYY-MM-DD`, `DD.MM.YYYY`, `DD/MM/YY`.

The store period is also the tariff period, inclusive of both dates. The
period identity in `pricing.month` and `pricing.year` comes from
`activation_start`, not from the session calendar month.

## New Tariff Definition

Eligible session:

```sql
successful > 0
AND total_sales > 0
```

```text
active_days_count = COUNT(DISTINCT session_date)
average_daily_sales = SUM(total_sales) / active_days_count
```

Multiple eligible sessions on one calendar day count as one active day. Zero sessions do not affect either the numerator or denominator.

The new tariff control points are:

| Minimum daily sales | Fee |
| ------------------: | --: |
| 0 | 99,900 |
| 1,000,000 | 99,900 |
| 2,000,000 | 160,000 |
| 3,000,000 | 190,000 |
| 4,000,000 | 220,000 |

The fee is linearly interpolated between neighboring points and rounded to the nearest 100 tenge. Values below the first point are clamped to the minimum fee; values above the last point are clamped to the maximum fee.

## New Configuration Table

`pricing_rules` stores the control points:

```text
id
min_daily_sales
fee
active
created_at
updated_at
```

Changing a point in SQL Editor changes the future calculation without replacing the tariff function:

```sql
UPDATE public.pricing_rules
SET fee = 170000,
    updated_at = now()
WHERE min_daily_sales = 2000000;
```

The table must not be writable by `anon` or `authenticated`. The intended administration path is Supabase SQL Editor or a future protected admin tool.

## New Pricing Fields

Existing fields remain:

- `total_monthly_sales`
- `daily_average_sales`
- `sessions_count`
- `calculated_fee`
- `final_fee`
- `days_in_period`
- `period_start`
- `period_end`

New field:

- `active_days_count`: unique active calendar days used as the denominator.

`days_in_period` remains the calendar length of the period and is not used as the denominator for the new average.

## Historical Data Policy

Existing `pricing` rows are not mass-recalculated.

The store-level function only calculates sessions inside the current active
paid period. A late INSERT outside that period does not create or rewrite a
pricing row. A new paid period gets a new pricing identity from its
`activation_start`.

This policy should be revisited if late offline uploads are expected to change closed billing periods.

## Prepared SQL Files

- `supabase/pricing_prepare.sql`: creates `pricing_rules`, adds `active_days_count`, and defines the new function. It does not switch the trigger or change RLS.
- `supabase/store_activation_prepare.sql`: creates the one-row-per-store subscription table, seeds active stores, and replaces the prepared function with activation-period logic. It does not switch the old trigger or cron.
- `supabase/store_activation_cron_prepare.sql`: defines store-level expiration handling; its cron schedule remains commented until the application cutover.
- `supabase/store_activation_cutover.sql`: replaces the old device-level cron with the store-level cron. Execute only after the client deployment.
- `supabase/store_activation_autosync.sql`: users→store_activation trigger and
  backfill (without it new active devices may miss a `store_activation` row).
- `supabase/store_activation_auto_renew.sql`: adds `trg_store_activation_auto_renew`
  — flipping `store_activation.status` to `active` on an expired row auto-starts
  a fresh 1-month period (manual Table Editor renewal becomes a single field change).
- `supabase/pricing_cutover.sql`: switches the trigger and hardens pricing/session write permissions. Execute only after review.
- `supabase/pricing_tests.sql`: read-only checks for control points and active-day behavior.
- `supabase/cleanup_after_migration.sql`: preflight checks and commented cleanup commands. No destructive statement is active.

## Sync Audit

The repository now contains a single synchronization mechanism: **MEGA folder sync** (protocol v2).

- LAN FastAPI Master/Client (`server_api.py`, `sync_client.py`) — **removed**.
- Supabase Cloud Relay (`pvm_sync_*` tables, `db.CloudSyncManager`) — **removed**.
- MEGA/folder sync (`sync_engine.py`, `sync_queue.py`, `sync_registry.py`, `sync_transport.py`, `transport_local.py`, `sync_setup_wizard.py`) — **the only sync path**.

Devices of one store share a single MEGA folder. The engine writes JSONL change files to `outbox/`
and weekly full snapshots to `snapshots/`. Protocol v2 features (all confirmed in code):

- `event_id` + `sync_inbox` with `INSERT OR IGNORE` — deduplication (`sync_engine.py:413-431`).
- JSONL manifest + checksum; corrupted files rejected whole (`sync_queue.py:65-121`).
- Tombstones: goods delete — soft-delete with merge, purchase — `cancelled`.
- LWW by `updated_at` (datetime, Z→+00:00); cashier owns `goods.quantity`.
- **6 entities registered**: goods, partners, receipts, purchases, writeoffs, audits
  (`sync_registry.py:237-245`).
- Apply with up to 10 attempts and stale marking (`applied=-1`); acks; janitor
  (outbox grace 35 days, snapshots keep=2 older than 45 days).
- `SyncEngine.stop()` — shutdown-coordinator hook (in-flight cycle exits between stages).

No Supabase tables are involved in device-to-device sync; Supabase keeps licenses, notifications,
session history and the update channel only.

## Cron

The only relevant cron job found is:

```text
auto-deactivate-expired
0 0 * * *
```

It deactivates expired users and is unrelated to pricing. It should remain.

## Edge Functions

The Supabase dashboard reports zero Edge Functions. The tariff logic is therefore in PostgreSQL, not in a Supabase Edge Function.

## Code Risks

- Session uploads are not idempotent (plain INSERT, 3 retries) and can duplicate `total_sales` after a network retry.
- Resumed sessions can create multiple partial records.
- `successful` counts successful processing events, not guaranteed unique orders.
- `total_sales` is computed from successful bot items and does not represent POS receipts.
- `version.json` manifest is not signed (update pipeline executes remote modules).

## Required Execution Order

1. Review this document and `pricing_prepare.sql`.
2. Execute `pricing_prepare.sql` only after approval.
3. Run `pricing_tests.sql`.
4. Review the resulting rules and test output.
5. Execute `store_activation_prepare.sql` and verify all store periods.
6. **Execute `store_activation_autosync.sql`** (backfill + trigger users→store_activation) —
   without it new active devices may miss a `store_activation` row.
7. Deploy the client code that reads `store_activation`.
8. Execute `store_activation_cutover.sql` to replace the device-level cron.
9. Review and execute `pricing_cutover.sql` to switch the tariff trigger.
10. Run `recalc_current_period.sql` (recalculation of the current period) — note that it skips
    periods without eligible sessions, while the trigger may still create a minimum-fee row.
11. Run cleanup preflight from `cleanup_after_migration.sql`.
12. Execute cleanup only after old clients and pending relay data are confirmed absent.

> SQL files not covered above: `pvbot_logs.sql` creates `pvbot_runs`/`pvbot_events`
> with `GRANT ALL ... TO anon` — the client does not use these tables; grant should be revoked.
> LAN and Cloud Relay paths are already removed from the client code; device sync relies on the MEGA folder only.

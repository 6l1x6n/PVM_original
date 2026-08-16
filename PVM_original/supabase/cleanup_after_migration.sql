-- PVM Supabase cleanup plan
-- This file intentionally contains no active DROP/DELETE/ALTER statements.
-- Run the preflight queries first. Uncomment destructive statements only after
-- the application has shipped Mega Sync and no old client remains.

-- PRECHECK 1: all relay tables must be empty.
SELECT 'pvm_sync_goods' AS table_name, count(*) AS row_count FROM public.pvm_sync_goods
UNION ALL
SELECT 'pvm_sync_partners', count(*) FROM public.pvm_sync_partners
UNION ALL
SELECT 'pvm_sync_receipts', count(*) FROM public.pvm_sync_receipts
UNION ALL
SELECT 'pvm_sync_purchases', count(*) FROM public.pvm_sync_purchases
UNION ALL
SELECT 'pvm_sync_writeoffs', count(*) FROM public.pvm_sync_writeoffs
UNION ALL
SELECT 'pvm_sync_audits', count(*) FROM public.pvm_sync_audits;

-- PRECHECK 2: no database objects should reference relay tables except their
-- own indexes/sequences. Application source references must be checked too.
SELECT
    n.nspname AS schema_name,
    p.proname AS function_name,
    pg_get_functiondef(p.oid) AS definition
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE pg_get_functiondef(p.oid) ILIKE '%pvm_sync_%';

-- PRECHECK 3: archives are empty before considering removal.
SELECT 'pricing_archive' AS table_name, count(*) AS row_count
FROM public.pricing_archive
UNION ALL
SELECT 'session_history_archive', count(*)
FROM public.session_history_archive;

-- PRECHECK 4: verify no running application still advertises the old provider
-- by reviewing public.users.sync_provider in a controlled admin session.
SELECT sync_provider, count(*)
FROM public.users
GROUP BY sync_provider
ORDER BY sync_provider;

-- DESTRUCTIVE STAGE, DO NOT UNCOMMENT YET:
-- BEGIN;
-- DROP TABLE IF EXISTS public.pvm_sync_goods;
-- DROP TABLE IF EXISTS public.pvm_sync_partners;
-- DROP TABLE IF EXISTS public.pvm_sync_receipts;
-- DROP TABLE IF EXISTS public.pvm_sync_purchases;
-- DROP TABLE IF EXISTS public.pvm_sync_writeoffs;
-- DROP TABLE IF EXISTS public.pvm_sync_audits;
-- DROP TABLE IF EXISTS public.pricing_archive;
-- DROP TABLE IF EXISTS public.session_history_archive;
-- COMMIT;

-- OLD TARIFF OBJECTS, DO NOT UNCOMMENT YET:
-- DROP TRIGGER IF EXISTS trigger_auto_pricing ON public.session_history;
-- DROP FUNCTION IF EXISTS public.auto_update_pricing();
-- DROP FUNCTION IF EXISTS public.calculate_monthly_pricing(integer, integer);

-- The duplicate pricing index can be removed only after verifying which name
-- is referenced by deployment tooling:
-- DROP INDEX IF EXISTS public.pricing_unique_period;

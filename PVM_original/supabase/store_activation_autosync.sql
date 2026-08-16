-- PVM Supabase: automatic store-level activation sync
-- =====================================================
-- After store_activation_prepare.sql, the license for a device is read from
-- public.store_activation (one row per store login). Newly registered stores
-- get a row in public.users only — nothing created the store_activation row,
-- so the client kept reporting "СЦ не активирован" and the POS would not start.
--
-- This script:
--   1. Backfills store_activation from every already-approved device
--      (public.users.status = 'active', non-empty login).
--   2. Adds a trigger on public.users so approving a device in the admin UI
--      (status = 'active') automatically creates the store row.
--
-- The store period is created once and never moved by later device changes
-- (per design from store_activation_prepare.sql). Renewals are handled the
-- same way as before: by updating store_activation dates directly.
--
-- Run once in the Supabase SQL editor (idempotent — safe to re-run).

BEGIN;

-- Parse activation dates stored as text in public.users (DD.MM.YYYY, ISO or DD/MM/YY)
CREATE OR REPLACE FUNCTION public.parse_store_date(t text)
RETURNS date
LANGUAGE sql
IMMUTABLE
AS $fn$
    SELECT CASE
        WHEN t IS NULL OR btrim(t) = '' THEN NULL
        WHEN t ~ '^\d{2}\.\d{2}\.\d{4}$' THEN to_date(t, 'DD.MM.YYYY')
        WHEN t ~ '^\d{4}-\d{2}-\d{2}$' THEN t::date
        WHEN t ~ '^\d{2}/\d{2}/\d{2}$' THEN to_date(t, 'DD/MM/YY')
        ELSE NULL
    END;
$fn$;

-- Trigger function: auto-create the store row when a device is approved
CREATE OR REPLACE FUNCTION public.sync_store_activation()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $fn$
DECLARE
    v_login text;
    v_start date;
    v_end date;
BEGIN
    v_login := NULLIF(btrim(NEW.login), '');
    IF v_login IS NULL OR lower(NEW.status) <> 'active' THEN
        RETURN NEW;
    END IF;

    v_start := public.parse_store_date(NEW.activation_start);
    v_end := public.parse_store_date(NEW.activation_end);
    IF v_start IS NULL THEN
        v_start := CURRENT_DATE;
    END IF;
    IF v_end IS NULL OR v_end < v_start THEN
        v_end := v_start + interval '1 month';
    END IF;

    INSERT INTO public.store_activation (login, activation_start, activation_end, status)
    VALUES (v_login, v_start, v_end, 'active')
    ON CONFLICT (login) DO NOTHING;

    RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS trg_users_sync_store_activation ON public.users;
CREATE TRIGGER trg_users_sync_store_activation
AFTER INSERT OR UPDATE OF status, login, activation_start, activation_end ON public.users
FOR EACH ROW EXECUTE FUNCTION public.sync_store_activation();

-- Backfill: create store rows for stores already approved but missing in store_activation.
-- Multiple devices may share one login; DISTINCT ON picks a single (deterministic)
-- row per login, preferring the device with the earliest activation date.
INSERT INTO public.store_activation (login, activation_start, activation_end, status)
SELECT
    NULLIF(btrim(u.login), ''),
    COALESCE(public.parse_store_date(u.activation_start), CURRENT_DATE),
    CASE
        WHEN public.parse_store_date(u.activation_end) IS NULL
          OR public.parse_store_date(u.activation_end) < COALESCE(public.parse_store_date(u.activation_start), CURRENT_DATE)
        THEN COALESCE(public.parse_store_date(u.activation_start), CURRENT_DATE) + interval '1 month'
        ELSE public.parse_store_date(u.activation_end)
    END,
    'active'
FROM (
    SELECT DISTINCT ON (NULLIF(btrim(login), '')) login, activation_start, activation_end
    FROM public.users
    WHERE lower(status) = 'active'
      AND NULLIF(btrim(login), '') IS NOT NULL
    ORDER BY NULLIF(btrim(login), ''), activation_start NULLS LAST
) u
WHERE NOT EXISTS (
    SELECT 1 FROM public.store_activation sa
    WHERE sa.login = NULLIF(btrim(u.login), '')
);

COMMIT;

-- Verify:
-- SELECT * FROM public.store_activation ORDER BY login;

-- PVM Supabase pricing cutover
-- Execute only after pricing_prepare.sql and store_activation_prepare.sql
-- have been reviewed and applied.
-- This changes the active trigger and pricing write permissions. It does not
-- delete old functions or historical pricing rows.

BEGIN;

DO $preflight$
BEGIN
    IF to_regclass('public.store_activation') IS NULL THEN
        RAISE EXCEPTION 'Run store_activation_prepare.sql before pricing cutover';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.pricing_rules WHERE active = true) THEN
        RAISE EXCEPTION 'No active pricing rules configured';
    END IF;
END
$preflight$;

-- Keep the old implementation available for rollback/audit, but stop it from
-- writing new pricing rows.
DROP TRIGGER IF EXISTS trigger_auto_pricing ON public.session_history;

CREATE TRIGGER trigger_calculate_pricing_after_session
AFTER INSERT ON public.session_history
FOR EACH ROW
EXECUTE FUNCTION public.calculate_pricing_after_session();

-- The application only reads pricing. The SECURITY DEFINER trigger function is
-- the sole writer after this cutover.
ALTER TABLE public.pricing ENABLE ROW LEVEL SECURITY;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, TRIGGER, REFERENCES
    ON public.pricing FROM anon, authenticated;
GRANT SELECT ON public.pricing TO anon, authenticated;

DROP POLICY IF EXISTS "Allow all for anon" ON public.pricing;
DROP POLICY IF EXISTS "Anyone can read pricing" ON public.pricing;
CREATE POLICY "Clients can read pricing"
    ON public.pricing
    FOR SELECT
    TO anon, authenticated
    USING (true);

ALTER TABLE public.pricing_rules ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.pricing_rules FROM anon, authenticated;

-- Preserve session INSERT for the current client, but prevent mutation or
-- deletion of immutable session history. Tenant authentication remains a
-- separate follow-up because the current application sends public-key requests.
ALTER TABLE public.session_history ENABLE ROW LEVEL SECURITY;
REVOKE UPDATE, DELETE, TRUNCATE, TRIGGER, REFERENCES
    ON public.session_history FROM anon, authenticated;
GRANT INSERT, SELECT ON public.session_history TO anon, authenticated;

DROP POLICY IF EXISTS "Allow all for anon" ON public.session_history;
DROP POLICY IF EXISTS "Devices can insert own sessions" ON public.session_history;
DROP POLICY IF EXISTS "Devices can read own sessions" ON public.session_history;
CREATE POLICY "Clients can insert immutable sessions"
    ON public.session_history
    FOR INSERT
    TO anon, authenticated
    WITH CHECK (true);
CREATE POLICY "Clients can read sessions"
    ON public.session_history
    FOR SELECT
    TO anon, authenticated
    USING (true);

COMMIT;

-- Rollback trigger only, if needed after inspection:
-- BEGIN;
-- DROP TRIGGER IF EXISTS trigger_calculate_pricing_after_session ON public.session_history;
-- CREATE TRIGGER trigger_auto_pricing
-- AFTER INSERT ON public.session_history
-- FOR EACH ROW EXECUTE FUNCTION public.auto_update_pricing();
-- COMMIT;

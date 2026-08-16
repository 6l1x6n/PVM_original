-- PVM store-level expiration cron
-- Prepare only. Do not run until the application reads store_activation and
-- the old auto-deactivate-expired job is ready to be retired.

BEGIN;

CREATE OR REPLACE FUNCTION public.deactivate_expired_store_subscriptions()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $function$
BEGIN
    UPDATE public.store_activation
    SET status = 'inactive',
        updated_at = now()
    WHERE status = 'active'
      AND activation_end < current_date;
END;
$function$;

COMMENT ON FUNCTION public.deactivate_expired_store_subscriptions() IS
    'Disables store subscriptions after activation_end; activation_end remains valid inclusively.';

COMMIT;

-- After code deployment and old cron retirement, schedule once:
-- SELECT cron.schedule(
--     'auto-deactivate-expired-stores',
--     '0 0 * * *',
--     $$SELECT public.deactivate_expired_store_subscriptions();$$
-- );

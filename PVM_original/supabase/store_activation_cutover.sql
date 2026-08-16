-- PVM store-level subscription cutover
-- Execute after store_activation_prepare.sql and after deploying the client
-- code that reads store_activation. It retires the device-level expiration
-- cron and schedules expiration at the store level.

BEGIN;

DO $preflight$
BEGIN
    IF to_regclass('public.store_activation') IS NULL THEN
        RAISE EXCEPTION 'store_activation does not exist';
    END IF;
END
$preflight$;

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

-- Apply the inclusive-end rule immediately, then schedule the same check daily.
SELECT public.deactivate_expired_store_subscriptions();

DO $jobs$
DECLARE
    old_job_id bigint;
BEGIN
    SELECT jobid
    INTO old_job_id
    FROM cron.job
    WHERE jobname = 'auto-deactivate-expired'
    LIMIT 1;

    IF old_job_id IS NOT NULL THEN
        PERFORM cron.unschedule(old_job_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM cron.job
        WHERE jobname = 'auto-deactivate-expired-stores'
    ) THEN
        PERFORM cron.schedule(
            'auto-deactivate-expired-stores',
            '0 0 * * *',
            'SELECT public.deactivate_expired_store_subscriptions();'
        );
    END IF;
END
$jobs$;

COMMIT;

-- Verify after execution:
-- SELECT jobid, jobname, schedule, active, command
-- FROM cron.job
-- WHERE jobname = 'auto-deactivate-expired-stores';

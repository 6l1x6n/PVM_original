-- PVM Supabase: one-time recalculation of the current subscription period.
-- Requires: pricing_prepare.sql, store_activation_prepare.sql, pricing_cutover.sql.
--
-- Recomputes the pricing row for every store with status='active' in
-- store_activation, using exactly the same logic as the trigger
-- calculate_pricing_after_session(). Fixes rows that were written by the old
-- calculate_monthly_pricing() before cutover (e.g. id 217 with a collapsed
-- period). Historical periods without an active subscription are NOT touched.

BEGIN;

DO $preflight$
BEGIN
    IF to_regclass('public.store_activation') IS NULL THEN
        RAISE EXCEPTION 'Run store_activation_prepare.sql first';
    END IF;
    IF to_regclass('public.pricing_rules') IS NULL THEN
        RAISE EXCEPTION 'Run pricing_prepare.sql first';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.pricing_rules WHERE active = true) THEN
        RAISE EXCEPTION 'No active pricing rules configured';
    END IF;
END
$preflight$;

CREATE OR REPLACE FUNCTION public.recalc_current_period()
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $function$
DECLARE
    rec RECORD;
    v_total_sales numeric;
    v_sessions_count integer;
    v_active_days_count integer;
    v_average_daily_sales numeric;
    v_fee numeric;
    v_min_fee numeric;
    v_max_fee numeric;
    v_low record;
    v_high record;
    v_processed integer := 0;
BEGIN
    FOR rec IN
        SELECT sa.login, sa.activation_start, sa.activation_end
        FROM public.store_activation sa
        WHERE sa.status = 'active'
        ORDER BY sa.login
    LOOP
        SELECT
            coalesce(sum(sh.total_sales), 0),
            count(*),
            count(DISTINCT sh.session_date::date)
        INTO v_total_sales, v_sessions_count, v_active_days_count
        FROM public.session_history sh
        WHERE sh.login = rec.login
          AND sh.session_date::date BETWEEN rec.activation_start AND rec.activation_end
          AND sh.successful > 0
          AND sh.total_sales > 0;

        IF v_sessions_count = 0 THEN
            CONTINUE;
        END IF;

        v_average_daily_sales := v_total_sales / v_active_days_count;

        SELECT min(fee), max(fee)
        INTO v_min_fee, v_max_fee
        FROM public.pricing_rules
        WHERE active = true;

        IF v_min_fee IS NULL OR v_max_fee IS NULL THEN
            RAISE EXCEPTION 'No active pricing rules configured';
        END IF;

        SELECT min_daily_sales, fee INTO v_low
        FROM public.pricing_rules
        WHERE active = true AND min_daily_sales <= v_average_daily_sales
        ORDER BY min_daily_sales DESC LIMIT 1;

        SELECT min_daily_sales, fee INTO v_high
        FROM public.pricing_rules
        WHERE active = true AND min_daily_sales > v_average_daily_sales
        ORDER BY min_daily_sales ASC LIMIT 1;

        IF v_low.min_daily_sales IS NULL THEN
            SELECT min_daily_sales, fee INTO v_low
            FROM public.pricing_rules
            WHERE active = true
            ORDER BY min_daily_sales ASC LIMIT 1;
        END IF;

        IF v_high.min_daily_sales IS NULL
           OR v_high.min_daily_sales = v_low.min_daily_sales THEN
            v_fee := v_low.fee;
        ELSE
            v_fee := v_low.fee
                + (v_average_daily_sales - v_low.min_daily_sales)
                * (v_high.fee - v_low.fee)
                / (v_high.min_daily_sales - v_low.min_daily_sales);
        END IF;

        v_fee := round(v_fee / 100) * 100;
        v_fee := greatest(v_min_fee, least(v_fee, v_max_fee));

        INSERT INTO public.pricing (
            login, month, year, total_monthly_sales, daily_average_sales,
            sessions_count, calculated_fee, final_fee, days_in_period,
            active_days_count, period_start, period_end, updated_at
        )
        VALUES (
            rec.login,
            extract(month FROM rec.activation_start)::integer,
            extract(year FROM rec.activation_start)::integer,
            v_total_sales,
            v_average_daily_sales,
            v_sessions_count,
            v_fee,
            v_fee,
            rec.activation_end - rec.activation_start + 1,
            v_active_days_count,
            rec.activation_start,
            rec.activation_end,
            now()
        )
        ON CONFLICT (login, month, year) DO UPDATE SET
            total_monthly_sales = excluded.total_monthly_sales,
            daily_average_sales = excluded.daily_average_sales,
            sessions_count = excluded.sessions_count,
            calculated_fee = excluded.calculated_fee,
            final_fee = excluded.final_fee,
            days_in_period = excluded.days_in_period,
            active_days_count = excluded.active_days_count,
            period_start = excluded.period_start,
            period_end = excluded.period_end,
            updated_at = now();

        v_processed := v_processed + 1;
    END LOOP;

    RETURN v_processed;
END;
$function$;

-- Run the recalculation. Returns the number of processed stores.
SELECT public.recalc_current_period() AS stores_recalculated;

-- Verify the previously stale July row.
SELECT id, login, month, year, total_monthly_sales, daily_average_sales,
       sessions_count, active_days_count, calculated_fee, final_fee,
       period_start, period_end, updated_at
FROM public.pricing
WHERE login = 's240534'
ORDER BY year, month;

COMMIT;

-- Rollback if needed: the function is harmless to keep; rows can be recomputed
-- by running public.recalc_current_period() again. To restore old values there
-- is no stored copy; use the old function only as reference, do NOT run it.

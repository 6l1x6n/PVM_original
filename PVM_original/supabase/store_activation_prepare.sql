-- PVM Supabase store-level subscription preparation
-- Follow-up to pricing_prepare.sql. No old trigger, cron, or table is removed.

BEGIN;

CREATE TABLE IF NOT EXISTS public.store_activation (
    login text PRIMARY KEY,
    activation_start date NOT NULL,
    activation_end date NOT NULL,
    status text NOT NULL DEFAULT 'inactive',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT store_activation_dates_valid
        CHECK (activation_end >= activation_start),
    CONSTRAINT store_activation_status_valid
        CHECK (status IN ('active', 'inactive'))
);

ALTER TABLE public.store_activation ENABLE ROW LEVEL SECURITY;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON public.store_activation FROM anon, authenticated;
GRANT SELECT ON public.store_activation TO anon, authenticated;
DROP POLICY IF EXISTS "Clients can read store activation"
    ON public.store_activation;
CREATE POLICY "Clients can read store activation"
    ON public.store_activation
    FOR SELECT
    TO anon, authenticated
    USING (true);

-- One paid period per store login. The earliest currently active device date
-- seeds the store period; changing one device later cannot move this date.
INSERT INTO public.store_activation (
    login, activation_start, activation_end, status
)
SELECT
    u.login,
    min(to_date(u.activation_start, 'DD.MM.YYYY')) AS activation_start,
    (min(to_date(u.activation_start, 'DD.MM.YYYY')) + interval '1 month')::date,
    'active'
FROM public.users u
WHERE u.status = 'active'
  AND u.login IS NOT NULL
  AND u.login <> ''
  AND u.activation_start ~ '^\d{2}\.\d{2}\.\d{4}$'
GROUP BY u.login
ON CONFLICT (login) DO NOTHING;

-- Replace only the new function created by pricing_prepare.sql. The old
-- auto_update_pricing() function and trigger remain untouched.
CREATE OR REPLACE FUNCTION public.calculate_pricing_after_session()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $function$
DECLARE
    v_period_start date;
    v_period_end date;
    v_total_sales numeric := 0;
    v_sessions_count integer := 0;
    v_active_days_count integer := 0;
    v_average_daily_sales numeric := 0;
    v_fee numeric := 0;
    v_min_fee numeric := 0;
    v_max_fee numeric := 0;
    v_low record;
    v_high record;
BEGIN
    IF NEW.session_date IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT activation_start, activation_end
    INTO v_period_start, v_period_end
    FROM public.store_activation
    WHERE login = NEW.login
      AND status = 'active';

    IF v_period_start IS NULL
       OR NEW.session_date::date < v_period_start
       OR NEW.session_date::date > v_period_end THEN
        RETURN NEW;
    END IF;

    SELECT
        coalesce(sum(sh.total_sales), 0),
        count(*),
        count(DISTINCT sh.session_date::date)
    INTO v_total_sales, v_sessions_count, v_active_days_count
    FROM public.session_history sh
    WHERE sh.login = NEW.login
      AND sh.session_date::date BETWEEN v_period_start AND v_period_end
      AND sh.successful > 0
      AND sh.total_sales > 0;

    v_average_daily_sales := CASE
        WHEN v_active_days_count > 0
        THEN v_total_sales / v_active_days_count
        ELSE 0
    END;

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
        NEW.login,
        extract(month FROM v_period_start)::integer,
        extract(year FROM v_period_start)::integer,
        v_total_sales,
        v_average_daily_sales,
        v_sessions_count,
        v_fee,
        v_fee,
        v_period_end - v_period_start + 1,
        v_active_days_count,
        v_period_start,
        v_period_end,
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

    RETURN NEW;
END;
$function$;

COMMENT ON TABLE public.store_activation IS
    'One paid subscription period per store login; device rows do not own billing dates.';

COMMIT;

-- Verify before cutover:
-- SELECT * FROM public.store_activation ORDER BY login;

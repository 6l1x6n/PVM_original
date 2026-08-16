-- Read-only checks for the new pricing rules.
-- Run after pricing_prepare.sql and store_activation_prepare.sql. These queries
-- do not write application data.

-- Store-level subscription periods.
SELECT login, activation_start, activation_end, status
FROM public.store_activation
ORDER BY login;

-- Show configured control points.
SELECT min_daily_sales, fee, active
FROM public.pricing_rules
ORDER BY min_daily_sales;

-- Check expected interpolation values against the configured rules.
WITH samples(average_daily_sales) AS (
    VALUES
        (0::numeric),
        (1000000::numeric),
        (1500000::numeric),
        (2000000::numeric),
        (2500000::numeric),
        (3000000::numeric),
        (3500000::numeric),
        (4000000::numeric),
        (5000000::numeric)
),
points AS (
    SELECT min_daily_sales, fee
    FROM public.pricing_rules
    WHERE active = true
),
calculated AS (
    SELECT
        s.average_daily_sales,
        low.min_daily_sales AS low_sales,
        low.fee AS low_fee,
        high.min_daily_sales AS high_sales,
        high.fee AS high_fee
    FROM samples s
    LEFT JOIN LATERAL (
        SELECT * FROM points
        WHERE min_daily_sales <= s.average_daily_sales
        ORDER BY min_daily_sales DESC
        LIMIT 1
    ) low ON true
    LEFT JOIN LATERAL (
        SELECT * FROM points
        WHERE min_daily_sales > s.average_daily_sales
        ORDER BY min_daily_sales ASC
        LIMIT 1
    ) high ON true
)
SELECT
    average_daily_sales,
    round((
        CASE
            WHEN high_sales IS NULL THEN low_fee
            ELSE low_fee
                + (average_daily_sales - low_sales)
                * (high_fee - low_fee)
                / (high_sales - low_sales)
        END
    ) / 100) * 100 AS expected_fee
FROM calculated
ORDER BY average_daily_sales;

-- Verify that multiple sessions on one day count as one active day.
SELECT
    sa.login,
    sa.activation_start,
    sa.activation_end,
    count(*) FILTER (WHERE sh.successful > 0 AND sh.total_sales > 0) AS eligible_sessions,
    count(DISTINCT sh.session_date::date)
        FILTER (WHERE sh.successful > 0 AND sh.total_sales > 0) AS active_days_count,
    coalesce(sum(sh.total_sales)
        FILTER (WHERE sh.successful > 0 AND sh.total_sales > 0), 0) AS eligible_sales
FROM public.store_activation sa
LEFT JOIN public.session_history sh
    ON sh.login = sa.login
   AND sh.session_date::date BETWEEN sa.activation_start AND sa.activation_end
GROUP BY sa.login, sa.activation_start, sa.activation_end
ORDER BY sa.login;

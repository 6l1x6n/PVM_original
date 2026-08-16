-- PVM Supabase: продление лицензии магазина S24534
-- =====================================================
-- Что делает скрипт (выполнять целиком в SQL Editor):
--   1. Preflight: проверяет наличие таблицы store_activation.
--   2. Диагностика: показывает устройства магазина, текущую подписку и
--      вывод самой программы (license_state — как её посчитает клиент).
--   3. Продление: вносит/обновляет строку store_activation — период
--      с сегодня по +1 месяц (включительно), статус 'active'.
--   4. Синхронизация: ставит users.status = 'active' всем устройствам
--      магазина (на лицензию не влияет — клиент читает только
--      store_activation, но держит админ-панель и старый cron в согласии).
--   5. Контрольная проверка после продления.
--
-- Настройка:
--   * Логин магазина сопоставляется ТОЧНО: btrim(login) = 'S24534'.
--     Если логин другой — замените 'S24534' в строках ниже (помечены
--     комментом). Не используйте шаблоны с '%', чтобы случайно не
--     продлить похожий логин (например, XS24534).
--   * Срок продления — `interval '1 month'`; чтобы продлить на другой
--     период, замените на '6 months', '1 year' и т.п.
--   * Скрипт идемпотентен (безопасно перезапускать).
--   * ВНИМАНИЕ: скрипт начинает новый период с today — если у магазина
--     сейчас активная подписка, её остаток будет перезаписан.
--   * После выполнения клиент активируется сам: экран «Ожидание активации»
--     опрашивает сервер каждые 20 секунд (перезапуск не нужен).
--   * Если выполнен cutover, cron `auto-deactivate-expired-stores` вернёт
--     статус в 'inactive' только когда activation_end окажется в прошлом.

BEGIN;

-- 1. Preflight: таблица подписок должна существовать
DO $preflight$
BEGIN
    IF to_regclass('public.store_activation') IS NULL THEN
        RAISE EXCEPTION 'store_activation does not exist — сначала выполните store_activation_prepare.sql';
    END IF;
END
$preflight$;

-- Проверка, что устройства магазина вообще есть
DO $precheck$
DECLARE
    v_cnt integer;
BEGIN
    SELECT count(*) INTO v_cnt FROM public.users WHERE btrim(login) = 'S24534';
    IF v_cnt = 0 THEN
        RAISE WARNING 'Устройства с логином S24534 не найдены — проверьте логин магазина';
    END IF;
END
$precheck$;

-- 2. Диагностика (логин магазина: 'S24534')
SELECT u.device_key,
       btrim(u.login) AS login,
       u.status AS user_status,
       s.status AS store_status,
       s.activation_start,
       s.activation_end,
       current_date AS today,
       CASE
           WHEN s.status = 'active' AND s.activation_end >= current_date
               THEN 'АКТИВНА'
           ELSE 'НЕ АКТИВНА'
       END AS license_state
FROM public.users u
LEFT JOIN public.store_activation s
       ON s.login = btrim(u.login)
WHERE btrim(u.login) = 'S24534'
ORDER BY u.device_key;

-- 3. Продление подписки магазина (логин магазина: 'S24534')
INSERT INTO public.store_activation (
    login, activation_start, activation_end, status, updated_at
)
SELECT
    btrim(u.login),
    current_date,
    current_date + interval '1 month',
    'active',
    now()
FROM (
    SELECT DISTINCT btrim(login) AS login
    FROM public.users
    WHERE btrim(login) = 'S24534'
) u
ON CONFLICT (login) DO UPDATE SET
    status = 'active',
    activation_start = current_date,
    activation_end = current_date + interval '1 month',
    updated_at = now();

-- 4. Синхронизация users.status для всех устройств магазина
--    (триггер trg_users_sync_store_activation при этом безопасен —
--    INSERT ON CONFLICT DO NOTHING не перезапишет свежие даты)
UPDATE public.users
SET status = 'active'
WHERE btrim(login) = 'S24534';

COMMIT;

-- 5. Контрольная проверка после продления (логин магазина: 'S24534')
SELECT s.login,
       s.status AS store_status,
       s.activation_start,
       s.activation_end,
       (s.status = 'active' AND s.activation_end >= current_date) AS license_ok,
       (SELECT count(*) FROM public.users u
        WHERE btrim(u.login) = s.login) AS devices
FROM public.store_activation s
WHERE btrim(s.login) = 'S24534';

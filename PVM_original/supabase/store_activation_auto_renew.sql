-- PVM Supabase: авто-продление подписки при ручной активации
-- =============================================================
-- Повторяет поведение users (клиентский авто-реактиватор db.py): если админ
-- в Table Editor переключает status на 'active', а период истёк — начинается
-- новый период: сегодня → +1 месяц.
--
-- Срабатывает ТОЛЬКО при смене status (и при INSERT):
--   * ручные правки дат с будущей activation_end не перезаписываются;
--   * истёкшая строка (activation_end < today) при 'active' продлевается;
--   * заодно чинит сид из store_activation_autosync.sql, который мог создать
--     строку с уже истёкшей датой.
--
-- Выполнить один раз в SQL Editor (идемпотентно — безопасно перезапускать).

BEGIN;

CREATE OR REPLACE FUNCTION public.auto_renew_store_activation()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $fn$
BEGIN
    IF lower(NEW.status) = 'active'
       AND (NEW.activation_end < CURRENT_DATE
            OR NEW.activation_end < NEW.activation_start) THEN
        NEW.activation_start := CURRENT_DATE;
        NEW.activation_end := CURRENT_DATE + interval '1 month';
    END IF;
    RETURN NEW;
END;
$fn$;

COMMENT ON FUNCTION public.auto_renew_store_activation() IS
    'Sets a fresh 1-month period when a store is manually activated while expired.';

DROP TRIGGER IF EXISTS trg_store_activation_auto_renew ON public.store_activation;
CREATE TRIGGER trg_store_activation_auto_renew
BEFORE INSERT OR UPDATE OF status ON public.store_activation
FOR EACH ROW EXECUTE FUNCTION public.auto_renew_store_activation();

COMMIT;

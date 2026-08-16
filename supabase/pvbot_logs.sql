-- =============================================================================
-- PV Bot scheduler logs (v3.11.65)
-- Apply once in Supabase → SQL Editor.
-- pvbot_runs: one summary row per scheduled run (per store per night).
-- pvbot_events: event rows (info/warning/error) capped by the app.
-- Retention 90 days is enforced by the app on every upload (DELETE via REST).
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.pvbot_runs (
    id BIGSERIAL PRIMARY KEY,
    device_key TEXT NOT NULL,
    run_date TEXT NOT NULL,
    scheduled_time TEXT DEFAULT '',
    status TEXT DEFAULT 'unknown',
    error TEXT DEFAULT '',
    started_at TEXT DEFAULT '',
    finished_at TEXT DEFAULT '',
    events_count INTEGER DEFAULT 0,
    shutdown_after BOOLEAN DEFAULT FALSE,
    app_version TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pvbot_runs_device ON public.pvbot_runs (device_key, run_date DESC);
CREATE INDEX IF NOT EXISTS idx_pvbot_runs_date ON public.pvbot_runs (run_date);

CREATE TABLE IF NOT EXISTS public.pvbot_events (
    id BIGSERIAL PRIMARY KEY,
    device_key TEXT NOT NULL,
    run_date TEXT NOT NULL,
    ts TEXT DEFAULT '',
    level TEXT DEFAULT 'info',
    message TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pvbot_events_device ON public.pvbot_events (device_key, ts);
CREATE INDEX IF NOT EXISTS idx_pvbot_events_date ON public.pvbot_events (run_date);

GRANT ALL ON public.pvbot_runs TO service_role;
GRANT ALL ON public.pvbot_events TO service_role;
GRANT ALL ON public.pvbot_runs TO anon;
GRANT ALL ON public.pvbot_events TO anon;
GRANT USAGE ON SEQUENCE public.pvbot_runs_id_seq TO anon;
GRANT USAGE ON SEQUENCE public.pvbot_events_id_seq TO anon;

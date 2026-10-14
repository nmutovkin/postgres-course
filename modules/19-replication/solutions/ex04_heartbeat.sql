-- Exercise 4: heartbeat. Part 1, on the PRIMARY (database bazaar):
CREATE TABLE public.heartbeat (
    id int PRIMARY KEY,
    ts timestamptz NOT NULL
);
INSERT INTO public.heartbeat VALUES (1, now());

-- Part 2, the writer (lab version; in production a cron job or the app's scheduler):
--   while true; do
--     docker exec m19-primary psql -U postgres -d bazaar -qc "UPDATE public.heartbeat SET ts = now() WHERE id = 1"
--     sleep 1
--   done

-- Part 3, on ANY STANDBY: how stale is this server?
--   SELECT extract(epoch FROM now() - ts)::numeric(10,1) AS staleness_s FROM public.heartbeat WHERE id = 1;

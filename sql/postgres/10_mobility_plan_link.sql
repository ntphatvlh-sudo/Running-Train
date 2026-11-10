BEGIN;

ALTER TABLE public.mobility_sessions
    ADD COLUMN IF NOT EXISTS plan_id INTEGER;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_mobility_sessions_plan'
          AND conrelid = 'public.mobility_sessions'::regclass
    ) THEN
        ALTER TABLE public.mobility_sessions
            ADD CONSTRAINT fk_mobility_sessions_plan
            FOREIGN KEY (plan_id)
            REFERENCES public.training_plans (plan_id);
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS ix_mobility_sessions_plan_date
    ON public.mobility_sessions (plan_id, session_date);

CREATE UNIQUE INDEX IF NOT EXISTS uq_mobility_sessions_plan_date
    ON public.mobility_sessions (plan_id, session_date)
    WHERE plan_id IS NOT NULL;

ALTER TABLE public.planned_workouts
    DROP CONSTRAINT IF EXISTS ck_planned_workouts_type;

ALTER TABLE public.planned_workouts
    ADD CONSTRAINT ck_planned_workouts_type
    CHECK (
        workout_type IN (
            'EASY',
            'RECOVERY',
            'LONG_RUN',
            'TEMPO',
            'INTERVAL',
            'RACE',
            'STRENGTH',
            'REST',
            'CROSS_TRAINING'
        )
    );

COMMIT;

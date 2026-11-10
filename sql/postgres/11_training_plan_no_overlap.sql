BEGIN;

CREATE EXTENSION IF NOT EXISTS btree_gist;

ALTER TABLE public.training_plans
ADD CONSTRAINT ex_training_plans_no_date_overlap
EXCLUDE USING gist
(
    athlete_id WITH =,
    daterange(start_date, end_date, '[]') WITH &&
);

COMMIT;
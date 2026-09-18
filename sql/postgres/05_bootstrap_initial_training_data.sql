BEGIN;

DO $block$
BEGIN
    IF EXISTS (SELECT 1 FROM public.athletes)
       OR EXISTS (SELECT 1 FROM public.goals)
       OR EXISTS (SELECT 1 FROM public.training_plans)
    THEN
        RAISE EXCEPTION
            'Bootstrap bị dừng vì athlete, goal hoặc training plan đã tồn tại.';
    END IF;
END;
$block$;

WITH new_athlete AS
(
    INSERT INTO public.athletes
    (
        full_name,
        time_zone_name
    )
    VALUES
    (
        'Nguyễn Tấn Phát',
        'Asia/Ho_Chi_Minh'
    )
    RETURNING athlete_id
),
goal_rows
(
    goal_name,
    target_date,
    goal_status
) AS
(
    VALUES
        (
            'HM Sub 1:45 - Race 1 - 2026-08-30',
            DATE '2026-08-30',
            'COMPLETED'
        ),
        (
            'HM Sub 1:45 - Race 2 - 2026-11-08',
            DATE '2026-11-08',
            'ACTIVE'
        ),
        (
            'HM Sub 1:45 - A Goal - 2027-04-11',
            DATE '2027-04-11',
            'ACTIVE'
        )
),
new_goals AS
(
    INSERT INTO public.goals
    (
        athlete_id,
        goal_name,
        goal_type,
        target_distance_m,
        target_duration_sec,
        target_date,
        priority_level,
        status
    )
    SELECT
        new_athlete.athlete_id,
        goal_rows.goal_name,
        'RACE',
        21098,
        6299,
        goal_rows.target_date,
        1,
        goal_rows.goal_status
    FROM new_athlete
    CROSS JOIN goal_rows
    RETURNING
        goal_id,
        athlete_id,
        target_date
),
plan_rows
(
    target_date,
    plan_name,
    start_date,
    end_date,
    plan_status
) AS
(
    VALUES
        (
            DATE '2026-08-30',
            'HM Race 1 - 2026-08-30',
            DATE '2026-08-03',
            DATE '2026-08-30',
            'COMPLETED'
        ),
        (
            DATE '2026-11-08',
            'HM Race 2 - 2026-11-08',
            DATE '2026-08-31',
            DATE '2026-11-08',
            'ACTIVE'
        ),
        (
            DATE '2027-04-11',
            'HM A Goal - 2027-04-11',
            DATE '2026-11-09',
            DATE '2027-04-11',
            'DRAFT'
        )
)
INSERT INTO public.training_plans
(
    athlete_id,
    goal_id,
    plan_name,
    start_date,
    end_date,
    planned_days_per_week,
    plan_level,
    status,
    version_number
)
SELECT
    new_goals.athlete_id,
    new_goals.goal_id,
    plan_rows.plan_name,
    plan_rows.start_date,
    plan_rows.end_date,
    7,
    'INTERMEDIATE',
    plan_rows.plan_status,
    1
FROM new_goals
JOIN plan_rows
  ON plan_rows.target_date = new_goals.target_date;

SELECT
    a.athlete_id,
    a.full_name,
    g.goal_id,
    g.goal_name,
    g.target_date,
    g.status AS goal_status,
    tp.plan_id,
    tp.plan_name,
    tp.start_date,
    tp.end_date,
    tp.status AS plan_status
FROM public.athletes AS a
JOIN public.goals AS g
  ON g.athlete_id = a.athlete_id
JOIN public.training_plans AS tp
  ON tp.goal_id = g.goal_id
WHERE a.full_name = 'Nguyễn Tấn Phát'
ORDER BY tp.start_date;

COMMIT;
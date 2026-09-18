BEGIN;

CREATE OR REPLACE VIEW public.vw_planned_run_workouts AS
SELECT
    pw.planned_workout_id,
    pw.plan_id,
    tp.athlete_id,
    pw.scheduled_date,
    pw.workout_type,
    pw.workout_name,
    pw.planned_distance_m,
    pw.planned_duration_sec,
    pw.target_pace_sec_per_km,
    pw.target_heart_rate_min,
    pw.target_heart_rate_max,
    pw.priority_weight,
    pw.status,
    pw.notes
FROM public.planned_workouts AS pw
JOIN public.training_plans AS tp
  ON tp.plan_id = pw.plan_id
WHERE pw.planned_distance_m IS NOT NULL
  AND pw.planned_distance_m > 0;


CREATE OR REPLACE VIEW public.vw_run_workout_completion AS
SELECT
    planned.planned_workout_id,
    planned.plan_id,
    planned.athlete_id,
    planned.scheduled_date,
    planned.workout_type,
    planned.workout_name,
    planned.planned_distance_m,
    planned.planned_duration_sec,
    planned.target_pace_sec_per_km,

    EXISTS
    (
        SELECT 1
        FROM public.completed_activities AS activity
        WHERE activity.athlete_id = planned.athlete_id
          AND activity.activity_type = 'RUN'
          AND activity.activity_date = planned.scheduled_date
    ) AS is_completed

FROM public.vw_planned_run_workouts AS planned;


CREATE OR REPLACE VIEW public.vw_weekly_run_completion AS
SELECT
    run.plan_id,
    run.athlete_id,
    date_trunc('week', run.scheduled_date)::DATE
        AS week_start_date,
    (
        date_trunc('week', run.scheduled_date)::DATE
        + 6
    ) AS week_end_date,

    COUNT(*)::INTEGER AS due_run_workouts,

    COUNT(*) FILTER
    (
        WHERE run.is_completed
    )::INTEGER AS completed_run_workouts,

    (
        COUNT(*) FILTER
        (
            WHERE run.is_completed
        )::NUMERIC
        / NULLIF(COUNT(*), 0)
    )::NUMERIC(6, 4) AS run_completion_rate

FROM public.vw_run_workout_completion AS run
WHERE run.scheduled_date <= CURRENT_DATE
GROUP BY
    run.plan_id,
    run.athlete_id,
    date_trunc('week', run.scheduled_date)::DATE;


CREATE OR REPLACE VIEW public.vw_weekly_strength_completion AS
SELECT
    strength.athlete_id,
    date_trunc('week', strength.session_date)::DATE
        AS week_start_date,
    (
        date_trunc('week', strength.session_date)::DATE
        + 6
    ) AS week_end_date,

    COUNT(*)::INTEGER AS due_strength_sessions,

    COUNT(*) FILTER
    (
        WHERE strength.completed
    )::INTEGER AS completed_strength_sessions,

    (
        COUNT(*) FILTER
        (
            WHERE strength.completed
        )::NUMERIC
        / NULLIF(COUNT(*), 0)
    )::NUMERIC(6, 4)
        AS strength_session_completion_rate,

    AVG(
        strength.session_completion_rate
    )::NUMERIC(6, 4)
        AS average_exercise_completion_rate

FROM public.strength_sessions AS strength
WHERE strength.session_date <= CURRENT_DATE
GROUP BY
    strength.athlete_id,
    date_trunc('week', strength.session_date)::DATE;


CREATE OR REPLACE VIEW public.vw_weekly_mobility_completion AS
SELECT
    mobility.athlete_id,
    date_trunc('week', mobility.session_date)::DATE
        AS week_start_date,
    (
        date_trunc('week', mobility.session_date)::DATE
        + 6
    ) AS week_end_date,

    COUNT(*)::INTEGER AS due_mobility_sessions,

    COUNT(*) FILTER
    (
        WHERE mobility.completed
    )::INTEGER AS completed_mobility_sessions,

    (
        COUNT(*) FILTER
        (
            WHERE mobility.completed
        )::NUMERIC
        / NULLIF(COUNT(*), 0)
    )::NUMERIC(6, 4)
        AS mobility_completion_rate

FROM public.mobility_sessions AS mobility
WHERE mobility.session_date <= CURRENT_DATE
GROUP BY
    mobility.athlete_id,
    date_trunc('week', mobility.session_date)::DATE;


CREATE OR REPLACE VIEW public.vw_weekly_training_analysis AS
SELECT
    weekly_run.plan_id,
    tp.plan_name,
    weekly_run.athlete_id,
    weekly_run.week_start_date,
    weekly_run.week_end_date,

    weekly_run.due_run_workouts,
    weekly_run.completed_run_workouts,
    weekly_run.run_completion_rate,

    COALESCE(
        weekly_strength.due_strength_sessions,
        0
    ) AS due_strength_sessions,

    COALESCE(
        weekly_strength.completed_strength_sessions,
        0
    ) AS completed_strength_sessions,

    weekly_strength.strength_session_completion_rate,
    weekly_strength.average_exercise_completion_rate,

    COALESCE(
        weekly_mobility.due_mobility_sessions,
        0
    ) AS due_mobility_sessions,

    COALESCE(
        weekly_mobility.completed_mobility_sessions,
        0
    ) AS completed_mobility_sessions,

    weekly_mobility.mobility_completion_rate,

    weekly_run.week_end_date < CURRENT_DATE
        AS is_complete_week

FROM public.vw_weekly_run_completion AS weekly_run
JOIN public.training_plans AS tp
  ON tp.plan_id = weekly_run.plan_id
LEFT JOIN public.vw_weekly_strength_completion AS weekly_strength
  ON weekly_strength.athlete_id = weekly_run.athlete_id
 AND weekly_strength.week_start_date =
     weekly_run.week_start_date
LEFT JOIN public.vw_weekly_mobility_completion AS weekly_mobility
  ON weekly_mobility.athlete_id = weekly_run.athlete_id
 AND weekly_mobility.week_start_date =
     weekly_run.week_start_date;


CREATE OR REPLACE VIEW
public.vw_weekly_training_support_status AS
SELECT
    analysis.*,

    CASE
        WHEN analysis.due_strength_sessions = 0
            THEN 'NO STRENGTH DATA'
        WHEN analysis.strength_session_completion_rate >= 0.75
            THEN 'HIGH STRENGTH'
        ELSE 'LOW STRENGTH'
    END AS strength_status,

    CASE
        WHEN analysis.due_mobility_sessions = 0
            THEN 'NO MOBILITY DATA'
        WHEN analysis.mobility_completion_rate >= 0.75
            THEN 'HIGH MOBILITY'
        ELSE 'LOW MOBILITY'
    END AS mobility_status,

    CASE
        WHEN analysis.run_completion_rate < 0.75
            THEN 'RUN BELOW 75%'
        ELSE 'RUN ON TRACK'
    END AS run_status,

    CASE
        WHEN analysis.due_strength_sessions = 0
          OR analysis.due_mobility_sessions = 0
            THEN 'INSUFFICIENT SUPPORT DATA'
        WHEN analysis.strength_session_completion_rate >= 0.75
         AND analysis.mobility_completion_rate >= 0.75
            THEN 'HIGH SUPPORT ADHERENCE'
        WHEN analysis.strength_session_completion_rate < 0.75
         AND analysis.mobility_completion_rate < 0.75
            THEN 'LOW SUPPORT ADHERENCE'
        ELSE 'MIXED SUPPORT ADHERENCE'
    END AS support_status

FROM public.vw_weekly_training_analysis AS analysis;


CREATE OR REPLACE VIEW
public.vw_training_support_dashboard AS
SELECT
    plan_id,
    plan_name,
    week_start_date,
    week_end_date,

    due_run_workouts,
    completed_run_workouts,
    (
        run_completion_rate * 100
    )::NUMERIC(6, 2) AS run_completion_percent,

    due_strength_sessions,
    completed_strength_sessions,
    (
        strength_session_completion_rate * 100
    )::NUMERIC(6, 2) AS strength_completion_percent,

    (
        average_exercise_completion_rate * 100
    )::NUMERIC(6, 2)
        AS strength_exercise_completion_percent,

    due_mobility_sessions,
    completed_mobility_sessions,
    (
        mobility_completion_rate * 100
    )::NUMERIC(6, 2) AS mobility_completion_percent,

    strength_status,
    mobility_status,
    support_status,
    run_status,
    is_complete_week

FROM public.vw_weekly_training_support_status;


CREATE OR REPLACE VIEW public.vw_weekly_wellness_summary AS
WITH wellness_with_week AS
(
    SELECT
        wellness.athlete_id,
        wellness.wellness_date,
        date_trunc(
            'week',
            wellness.wellness_date
        )::DATE AS week_start_date,

        wellness.sleep_hours,
        wellness.fatigue_score,
        wellness.stress_score,
        wellness.soreness_score,
        wellness.energy_score,
        wellness.mood_score,
        wellness.motivation_score,
        wellness.readiness_score

    FROM public.daily_wellness AS wellness
    WHERE wellness.wellness_date <= CURRENT_DATE
)
SELECT
    athlete_id,
    week_start_date,
    week_start_date + 6 AS week_end_date,

    COUNT(*)::INTEGER AS wellness_days,

    AVG(sleep_hours)::NUMERIC(5, 2)
        AS average_sleep_hours,
    AVG(fatigue_score)::NUMERIC(4, 2)
        AS average_fatigue_score,
    AVG(stress_score)::NUMERIC(4, 2)
        AS average_stress_score,
    AVG(soreness_score)::NUMERIC(4, 2)
        AS average_soreness_score,
    AVG(energy_score)::NUMERIC(4, 2)
        AS average_energy_score,
    AVG(mood_score)::NUMERIC(4, 2)
        AS average_mood_score,
    AVG(motivation_score)::NUMERIC(4, 2)
        AS average_motivation_score,
    AVG(readiness_score)::NUMERIC(6, 2)
        AS average_readiness_score,

    COUNT(*) >= 5 AS has_enough_wellness_data,
    week_start_date + 6 < CURRENT_DATE
        AS is_complete_week

FROM wellness_with_week
GROUP BY
    athlete_id,
    week_start_date;


CREATE OR REPLACE VIEW public.vw_weekly_run_readiness AS
SELECT
    weekly_run.plan_id,
    tp.plan_name,
    weekly_run.athlete_id,
    weekly_run.week_start_date,
    weekly_run.week_end_date,

    weekly_run.due_run_workouts,
    weekly_run.completed_run_workouts,
    weekly_run.run_completion_rate,

    wellness.wellness_days,
    wellness.average_sleep_hours,
    wellness.average_fatigue_score,
    wellness.average_stress_score,
    wellness.average_soreness_score,
    wellness.average_energy_score,
    wellness.average_mood_score,
    wellness.average_motivation_score,
    wellness.average_readiness_score,

    COALESCE(
        wellness.has_enough_wellness_data,
        FALSE
    ) AS has_enough_wellness_data,

    weekly_run.week_end_date < CURRENT_DATE
        AS is_complete_week,

    CASE
        WHEN wellness.wellness_days IS NULL
            THEN 'NO WELLNESS DATA'
        WHEN NOT wellness.has_enough_wellness_data
            THEN 'INSUFFICIENT WELLNESS DATA'
        WHEN wellness.average_fatigue_score <= 2
         AND wellness.average_stress_score <= 2
         AND wellness.average_soreness_score <= 2
         AND wellness.average_readiness_score >= 80
            THEN 'GOOD READINESS'
        WHEN wellness.average_fatigue_score > 3
          OR wellness.average_stress_score > 3
          OR wellness.average_soreness_score > 3
          OR wellness.average_readiness_score < 60
            THEN 'LOW READINESS'
        ELSE 'MANAGEABLE READINESS'
    END AS readiness_status

FROM public.vw_weekly_run_completion AS weekly_run
JOIN public.training_plans AS tp
  ON tp.plan_id = weekly_run.plan_id
LEFT JOIN public.vw_weekly_wellness_summary AS wellness
  ON wellness.athlete_id = weekly_run.athlete_id
 AND wellness.week_start_date =
     weekly_run.week_start_date;

COMMIT;
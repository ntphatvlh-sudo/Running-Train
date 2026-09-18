BEGIN;

CREATE OR REPLACE VIEW public.vw_plan_run_completion AS
SELECT
    plan_id,
    athlete_id,

    COUNT(*)::INTEGER AS due_run_workouts,

    COUNT(*) FILTER
    (
        WHERE is_completed
    )::INTEGER AS completed_run_workouts,

    (
        COUNT(*) FILTER
        (
            WHERE is_completed
        )::NUMERIC
        / NULLIF(COUNT(*), 0)
    )::NUMERIC(6, 4) AS completion_rate

FROM public.vw_run_workout_completion
WHERE scheduled_date <= CURRENT_DATE
GROUP BY
    plan_id,
    athlete_id;


CREATE OR REPLACE VIEW
public.vw_matching_plan_recommendation AS
WITH recommendation_base AS
(
    SELECT
        completion.plan_id,
        tp.plan_name,
        completion.athlete_id,
        tp.start_date,
        tp.end_date,

        completion.due_run_workouts,
        completion.completed_run_workouts,
        completion.completion_rate,

        latest_week.week_start_date
            AS latest_complete_week_start,
        latest_week.week_end_date
            AS latest_complete_week_end,

        latest_week.strength_session_completion_rate,
        latest_week.mobility_completion_rate,
        latest_week.support_status,

        CASE
            WHEN completion.completion_rate >= 0.75
                THEN 0.00
            WHEN completion.completion_rate >= 0.65
                THEN 0.10
            WHEN completion.completion_rate >= 0.50
                THEN 0.15
            ELSE 0.20
        END::NUMERIC(6, 4) AS base_reduction_rate

    FROM public.vw_plan_run_completion AS completion
    JOIN public.training_plans AS tp
      ON tp.plan_id = completion.plan_id

    LEFT JOIN LATERAL
    (
        SELECT
            weekly.week_start_date,
            weekly.week_end_date,
            weekly.strength_session_completion_rate,
            weekly.mobility_completion_rate,
            weekly.support_status

        FROM public.vw_weekly_training_support_status
            AS weekly

        WHERE weekly.plan_id = completion.plan_id
          AND weekly.athlete_id = completion.athlete_id
          AND weekly.is_complete_week

        ORDER BY weekly.week_start_date DESC
        LIMIT 1
    ) AS latest_week
      ON TRUE
),
recommendation_adjusted AS
(
    SELECT
        base.*,

        CASE
            WHEN base.completion_rate >= 0.75
                THEN 0.00

            WHEN base.support_status =
                    'LOW SUPPORT ADHERENCE'
             AND base.base_reduction_rate < 0.20
                THEN LEAST(
                    base.base_reduction_rate + 0.05,
                    0.20
                )

            ELSE base.base_reduction_rate
        END::NUMERIC(6, 4)
            AS recommended_reduction_rate

    FROM recommendation_base AS base
)
SELECT
    adjusted.plan_id,
    adjusted.plan_name,
    adjusted.athlete_id,
    adjusted.start_date,
    adjusted.end_date,

    adjusted.due_run_workouts,
    adjusted.completed_run_workouts,
    adjusted.completion_rate,

    adjusted.latest_complete_week_start,
    adjusted.latest_complete_week_end,

    adjusted.strength_session_completion_rate,
    adjusted.mobility_completion_rate,

    COALESCE(
        adjusted.support_status,
        'INSUFFICIENT SUPPORT DATA'
    ) AS support_status,

    adjusted.base_reduction_rate,
    adjusted.recommended_reduction_rate,

    CASE
        WHEN adjusted.completion_rate >= 0.75
            THEN 'KEEP CURRENT PLAN'
        WHEN adjusted.recommended_reduction_rate = 0.10
            THEN 'LIGHT REDUCTION'
        WHEN adjusted.recommended_reduction_rate = 0.15
            THEN 'MODERATE REDUCTION'
        ELSE 'STRONG REDUCTION'
    END AS matching_action,

    CASE
        WHEN adjusted.completion_rate >= 0.75
            THEN
                'Run completion is at least 75%. Keep current plan.'

        WHEN adjusted.support_status =
                'LOW SUPPORT ADHERENCE'
            THEN
                'Run completion is below 75% and support adherence was low. Use a more cautious reduction.'

        WHEN adjusted.support_status IS NULL
            THEN
                'Run completion is below 75%. Support data is insufficient, so use the run-based reduction.'

        ELSE
            'Run completion is below 75%. Use the run-based reduction.'
    END AS recommendation_reason

FROM recommendation_adjusted AS adjusted;


CREATE OR REPLACE VIEW
public.vw_controlled_progression_eligibility AS
WITH ranked_complete_weeks AS
(
    SELECT
        weekly.plan_id,
        weekly.plan_name,
        weekly.athlete_id,
        weekly.week_start_date,
        weekly.week_end_date,

        weekly.due_run_workouts,
        weekly.completed_run_workouts,
        weekly.run_completion_rate,

        weekly.wellness_days,
        weekly.average_fatigue_score,
        weekly.average_stress_score,
        weekly.average_soreness_score,
        weekly.average_readiness_score,

        weekly.has_enough_wellness_data,
        weekly.readiness_status,

        ROW_NUMBER() OVER
        (
            PARTITION BY weekly.plan_id
            ORDER BY weekly.week_start_date DESC
        ) AS week_rank

    FROM public.vw_weekly_run_readiness AS weekly
    WHERE weekly.is_complete_week
),
last_two_weeks AS
(
    SELECT
        plan_id,
        MAX(plan_name) AS plan_name,
        MAX(athlete_id) AS athlete_id,

        COUNT(*)::INTEGER AS complete_week_count,

        MIN(week_start_date)
            AS earlier_week_start_date,
        MAX(week_start_date)
            AS latest_week_start_date,

        MIN(run_completion_rate)
            AS minimum_run_completion_rate,
        MIN(wellness_days)
            AS minimum_wellness_days,

        MAX(average_fatigue_score)
            AS maximum_average_fatigue,
        MAX(average_stress_score)
            AS maximum_average_stress,
        MAX(average_soreness_score)
            AS maximum_average_soreness,
        MIN(average_readiness_score)
            AS minimum_average_readiness,

        MIN(
            has_enough_wellness_data::INTEGER
        ) AS both_weeks_have_enough_wellness

    FROM ranked_complete_weeks
    WHERE week_rank <= 2
    GROUP BY plan_id
)
SELECT
    plan_id,
    plan_name,
    athlete_id,
    complete_week_count,
    earlier_week_start_date,
    latest_week_start_date,

    minimum_run_completion_rate,
    minimum_wellness_days,
    maximum_average_fatigue,
    maximum_average_stress,
    maximum_average_soreness,
    minimum_average_readiness,

    CASE
        WHEN complete_week_count < 2
            THEN FALSE
        WHEN latest_week_start_date
             - earlier_week_start_date <> 7
            THEN FALSE
        WHEN both_weeks_have_enough_wellness <> 1
            THEN FALSE
        WHEN minimum_run_completion_rate < 0.90
            THEN FALSE
        WHEN maximum_average_fatigue > 2
            THEN FALSE
        WHEN maximum_average_stress > 2
            THEN FALSE
        WHEN maximum_average_soreness > 2
            THEN FALSE
        WHEN minimum_average_readiness < 80
            THEN FALSE
        ELSE TRUE
    END AS is_eligible_for_progression,

    CASE
        WHEN complete_week_count < 2
            THEN 'NEED TWO COMPLETE WEEKS'
        WHEN latest_week_start_date
             - earlier_week_start_date <> 7
            THEN 'COMPLETE WEEKS ARE NOT CONSECUTIVE'
        WHEN both_weeks_have_enough_wellness <> 1
            THEN 'INSUFFICIENT WELLNESS DATA'
        WHEN minimum_run_completion_rate < 0.90
            THEN 'RUN COMPLETION BELOW 90%'
        WHEN maximum_average_fatigue > 2
            THEN 'FATIGUE ABOVE 2'
        WHEN maximum_average_stress > 2
            THEN 'STRESS ABOVE 2'
        WHEN maximum_average_soreness > 2
            THEN 'SORENESS ABOVE 2'
        WHEN minimum_average_readiness < 80
            THEN 'READINESS BELOW 80'
        ELSE 'ELIGIBLE FOR CONTROLLED PROGRESSION'
    END AS eligibility_reason

FROM last_two_weeks;


CREATE OR REPLACE VIEW public.vw_next_race_protection AS
SELECT
    tp.plan_id,
    tp.athlete_id,
    MIN(workout.scheduled_date) AS next_race_date,
    (
        MIN(workout.scheduled_date)
        - CURRENT_DATE
    )::INTEGER AS days_until_race

FROM public.training_plans AS tp
JOIN public.planned_workouts AS workout
  ON workout.plan_id = tp.plan_id
WHERE UPPER(workout.workout_type) = 'RACE'
  AND workout.scheduled_date >= CURRENT_DATE
GROUP BY
    tp.plan_id,
    tp.athlete_id;


CREATE OR REPLACE VIEW
public.vw_controlled_progression_decision AS
SELECT
    eligibility.plan_id,
    eligibility.plan_name,
    eligibility.athlete_id,

    eligibility.complete_week_count,
    eligibility.earlier_week_start_date,
    eligibility.latest_week_start_date,

    eligibility.minimum_run_completion_rate,
    eligibility.maximum_average_fatigue,
    eligibility.maximum_average_stress,
    eligibility.maximum_average_soreness,
    eligibility.minimum_average_readiness,

    eligibility.is_eligible_for_progression,
    eligibility.eligibility_reason,

    race.next_race_date,
    race.days_until_race,

    CASE
        WHEN NOT eligibility.is_eligible_for_progression
            THEN FALSE
        WHEN race.days_until_race BETWEEN 0 AND 14
            THEN FALSE
        ELSE TRUE
    END AS can_apply_controlled_progression,

    CASE
        WHEN NOT eligibility.is_eligible_for_progression
            THEN eligibility.eligibility_reason
        WHEN race.days_until_race BETWEEN 0 AND 14
            THEN 'BLOCKED BY 14-DAY RACE PROTECTION'
        ELSE 'CONTROLLED PROGRESSION AVAILABLE'
    END AS progression_decision,

    CASE
        WHEN eligibility.is_eligible_for_progression
         AND
         (
             race.days_until_race IS NULL
             OR race.days_until_race > 14
         )
            THEN 0.05
        ELSE 0.00
    END::NUMERIC(6, 4) AS recommended_increase_rate

FROM public.vw_controlled_progression_eligibility
    AS eligibility
LEFT JOIN public.vw_next_race_protection AS race
  ON race.plan_id = eligibility.plan_id;


CREATE OR REPLACE VIEW
public.vw_adaptive_plan_recommendation AS
SELECT
    matching.plan_id,
    matching.plan_name,
    matching.athlete_id,
    matching.start_date,
    matching.end_date,

    matching.due_run_workouts,
    matching.completed_run_workouts,
    matching.completion_rate,

    matching.support_status,

    matching.base_reduction_rate,
    matching.recommended_reduction_rate,

    COALESCE(
        progression.recommended_increase_rate,
        0.00
    )::NUMERIC(6, 4) AS recommended_increase_rate,

    progression.complete_week_count,
    progression.minimum_run_completion_rate,
    progression.maximum_average_fatigue,
    progression.maximum_average_stress,
    progression.maximum_average_soreness,
    progression.minimum_average_readiness,
    progression.next_race_date,
    progression.days_until_race,
    progression.progression_decision,

    CASE
        WHEN matching.completion_rate < 0.75
            THEN matching.matching_action

        WHEN progression.can_apply_controlled_progression
            THEN 'CONTROLLED PROGRESSION'

        WHEN matching.completion_rate >= 0.90
            THEN 'KEEP AND MONITOR'

        ELSE 'KEEP CURRENT PLAN'
    END AS adaptive_action,

    CASE
        WHEN matching.completion_rate < 0.75
            THEN matching.recommendation_reason

        WHEN progression.can_apply_controlled_progression
            THEN
                'Run completion and readiness were strong for two complete consecutive weeks. A 5% progression is available.'

        WHEN matching.completion_rate >= 0.90
         AND progression.progression_decision =
                'BLOCKED BY 14-DAY RACE PROTECTION'
            THEN
                'Performance is strong, but progression is blocked within 14 days of a race.'

        WHEN matching.completion_rate >= 0.90
            THEN CONCAT(
                'Performance is strong, but progression is not available: ',
                COALESCE(
                    progression.progression_decision,
                    'INSUFFICIENT READINESS DATA'
                )
            )

        ELSE
            'Run completion is between 75% and 90%. Keep the current plan.'
    END AS adaptive_reason

FROM public.vw_matching_plan_recommendation AS matching
LEFT JOIN public.vw_controlled_progression_decision
    AS progression
  ON progression.plan_id = matching.plan_id;


CREATE OR REPLACE VIEW
public.vw_adaptive_plan_dashboard AS
SELECT
    recommendation.plan_id,
    recommendation.plan_name,
    recommendation.start_date,
    recommendation.end_date,

    recommendation.due_run_workouts,
    recommendation.completed_run_workouts,

    (
        recommendation.completion_rate * 100
    )::NUMERIC(6, 2) AS run_completion_percent,

    recommendation.support_status,

    (
        recommendation.recommended_reduction_rate * 100
    )::NUMERIC(6, 2)
        AS recommended_reduction_percent,

    (
        recommendation.recommended_increase_rate * 100
    )::NUMERIC(6, 2)
        AS recommended_increase_percent,

    recommendation.complete_week_count,

    (
        recommendation.minimum_run_completion_rate * 100
    )::NUMERIC(6, 2)
        AS minimum_two_week_run_completion_percent,

    recommendation.maximum_average_fatigue,
    recommendation.maximum_average_stress,
    recommendation.maximum_average_soreness,
    recommendation.minimum_average_readiness,

    recommendation.next_race_date,
    recommendation.days_until_race,
    recommendation.progression_decision,

    recommendation.adaptive_action,
    recommendation.adaptive_reason

FROM public.vw_adaptive_plan_recommendation
    AS recommendation;


CREATE OR REPLACE VIEW
public.vw_planned_workout_version_history AS
SELECT
    adjustment.adjustment_run_id,
    adjustment.plan_id,
    adjustment.adjustment_type,

    adjustment.status AS adjustment_status,

    (
        adjustment.completion_rate * 100
    )::NUMERIC(6, 2) AS completion_percent,

    (
        adjustment.reduction_rate * 100
    )::NUMERIC(6, 2) AS reduction_percent,

    (
        adjustment.increase_rate * 100
    )::NUMERIC(6, 2) AS increase_percent,

    adjustment.window_start_date,
    adjustment.window_end_date,
    adjustment.created_at,
    adjustment.applied_at,

    history.planned_workout_id,

    history.previous_scheduled_date
        AS scheduled_date,

    history.previous_workout_type
        AS workout_type,

    history.previous_workout_name,
    history.new_workout_name,

    history.previous_planned_distance_m,
    history.new_planned_distance_m,

    history.previous_planned_duration_sec,
    history.new_planned_duration_sec,

    history.previous_target_pace_sec_per_km,
    history.new_target_pace_sec_per_km,

    history.previous_notes,
    history.new_notes,
    history.changed_at

FROM public.plan_adjustment_runs AS adjustment
JOIN public.planned_workout_history AS history
  ON history.adjustment_run_id =
     adjustment.adjustment_run_id;

COMMIT;
BEGIN;


CREATE OR REPLACE FUNCTION public.preview_adaptive_plan
(
    p_plan_id INTEGER,
    p_window_days INTEGER DEFAULT 7,
    p_adjustment_type TEXT DEFAULT 'REDUCTION'
)
RETURNS TABLE
(
    result_set SMALLINT,
    payload JSONB
)
LANGUAGE plpgsql
AS
$$
DECLARE
    v_type TEXT := UPPER(TRIM(p_adjustment_type));
    v_completion_rate NUMERIC(6, 4);
    v_change_rate NUMERIC(6, 4);
    v_action TEXT;
    v_reason TEXT;
    v_support_status TEXT;
    v_window_start DATE := CURRENT_DATE + 1;
    v_window_end DATE;
BEGIN
    IF p_window_days < 1 THEN
        RAISE EXCEPTION
            'WindowDays phải lớn hơn hoặc bằng 1.';
    END IF;

    IF v_type NOT IN ('REDUCTION', 'PROGRESSION') THEN
        RAISE EXCEPTION
            'AdjustmentType phải là REDUCTION hoặc PROGRESSION.';
    END IF;

    v_window_end := CURRENT_DATE + p_window_days;

    IF v_type = 'REDUCTION' THEN
        SELECT
            recommendation.completion_rate,
            recommendation.recommended_reduction_rate,
            recommendation.matching_action,
            recommendation.recommendation_reason,
            recommendation.support_status
        INTO
            v_completion_rate,
            v_change_rate,
            v_action,
            v_reason,
            v_support_status
        FROM public.vw_matching_plan_recommendation
            AS recommendation
        WHERE recommendation.plan_id = p_plan_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION
                'Không tìm thấy đề xuất giảm tải cho PlanID=%.',
                p_plan_id;
        END IF;

        RETURN QUERY
        SELECT
            1::SMALLINT,
            jsonb_build_object(
                'PlanID', p_plan_id,
                'AdjustmentType', v_type,
                'RunCompletionPercent',
                    ROUND(v_completion_rate * 100, 2),
                'SupportStatus', v_support_status,
                'AdaptiveAction', v_action,
                'RecommendedChangePercent',
                    ROUND(v_change_rate * 100, 2),
                'AdaptiveReason', v_reason,
                'ProposedWindowStart', v_window_start,
                'ProposedWindowEnd', v_window_end
            );

        IF COALESCE(v_change_rate, 0) <= 0 THEN
            RETURN;
        END IF;

    ELSE
        SELECT
            recommendation.completion_rate,
            recommendation.recommended_increase_rate,
            recommendation.adaptive_action,
            recommendation.adaptive_reason,
            recommendation.support_status
        INTO
            v_completion_rate,
            v_change_rate,
            v_action,
            v_reason,
            v_support_status
        FROM public.vw_adaptive_plan_recommendation
            AS recommendation
        WHERE recommendation.plan_id = p_plan_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION
                'Không tìm thấy Adaptive Plan cho PlanID=%.',
                p_plan_id;
        END IF;

        RETURN QUERY
        SELECT
            1::SMALLINT,
            jsonb_build_object(
                'PlanID', p_plan_id,
                'AdjustmentType', v_type,
                'RunCompletionPercent',
                    ROUND(v_completion_rate * 100, 2),
                'SupportStatus', v_support_status,
                'AdaptiveAction', v_action,
                'RecommendedChangePercent',
                    ROUND(COALESCE(v_change_rate, 0) * 100, 2),
                'AdaptiveReason', v_reason,
                'ProposedWindowStart', v_window_start,
                'ProposedWindowEnd', v_window_end
            );

        IF COALESCE(v_change_rate, 0) <= 0 THEN
            RETURN;
        END IF;
    END IF;

    RETURN QUERY
    SELECT
        2::SMALLINT,
        jsonb_build_object(
            'PlannedWorkoutID',
                workout.planned_workout_id,
            'ScheduledDate',
                workout.scheduled_date,
            'WorkoutType',
                workout.workout_type,
            'CurrentWorkoutName',
                workout.workout_name,
            'CurrentDistanceM',
                workout.planned_distance_m,
            'ProposedDistanceM',
                CASE
                    WHEN workout.planned_distance_m IS NULL
                        THEN NULL
                    WHEN v_type = 'REDUCTION'
                        THEN ROUND(
                            workout.planned_distance_m
                            * (1 - v_change_rate),
                            -2
                        )::INTEGER
                    ELSE ROUND(
                        workout.planned_distance_m
                        * (1 + v_change_rate),
                        -2
                    )::INTEGER
                END,
            'CurrentDurationSec',
                workout.planned_duration_sec,
            'ProposedDurationSec',
                CASE
                    WHEN workout.planned_duration_sec IS NULL
                        THEN NULL
                    WHEN v_type = 'REDUCTION'
                        THEN ROUND(
                            workout.planned_duration_sec
                            * (1 - v_change_rate)
                        )::INTEGER
                    ELSE ROUND(
                        workout.planned_duration_sec
                        * (1 + v_change_rate)
                    )::INTEGER
                END,
            'TargetPaceSecPerKm',
                workout.target_pace_sec_per_km,
            'ChangePercent',
                ROUND(v_change_rate * 100, 2)
        )
    FROM public.vw_planned_run_workouts AS workout
    WHERE workout.plan_id = p_plan_id
      AND UPPER(workout.workout_type) <> 'RACE'
      AND workout.scheduled_date
            BETWEEN v_window_start AND v_window_end
    ORDER BY workout.scheduled_date;
END;
$$;


CREATE OR REPLACE FUNCTION public.apply_adaptive_plan
(
    p_plan_id INTEGER,
    p_window_days INTEGER DEFAULT 7,
    p_adjustment_type TEXT DEFAULT 'REDUCTION'
)
RETURNS TABLE
(
    adjustment_run_id BIGINT,
    adjustment_type TEXT,
    change_percent NUMERIC(6, 2),
    changed_workout_count INTEGER
)
LANGUAGE plpgsql
AS
$$
DECLARE
    v_type TEXT := UPPER(TRIM(p_adjustment_type));
    v_completion_rate NUMERIC(6, 4);
    v_change_rate NUMERIC(6, 4);
    v_action TEXT;
    v_run_id BIGINT;
    v_changed_count INTEGER;
    v_window_start DATE := CURRENT_DATE + 1;
    v_window_end DATE;
BEGIN
    IF p_window_days < 1 THEN
        RAISE EXCEPTION
            'WindowDays phải lớn hơn hoặc bằng 1.';
    END IF;

    IF v_type NOT IN ('REDUCTION', 'PROGRESSION') THEN
        RAISE EXCEPTION
            'AdjustmentType phải là REDUCTION hoặc PROGRESSION.';
    END IF;

    v_window_end := CURRENT_DATE + p_window_days;

    -- Ngăn hai phiên Apply cùng sửa một plan đồng thời.
    PERFORM pg_advisory_xact_lock(p_plan_id);

    IF NOT EXISTS
    (
        SELECT 1
        FROM public.training_plans
        WHERE plan_id = p_plan_id
    ) THEN
        RAISE EXCEPTION
            'Không tìm thấy PlanID=%.',
            p_plan_id;
    END IF;

    IF v_type = 'REDUCTION' THEN
        SELECT
            recommendation.completion_rate,
            recommendation.recommended_reduction_rate,
            recommendation.matching_action
        INTO
            v_completion_rate,
            v_change_rate,
            v_action
        FROM public.vw_matching_plan_recommendation
            AS recommendation
        WHERE recommendation.plan_id = p_plan_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION
                'Không tìm thấy đề xuất cho PlanID=%.',
                p_plan_id;
        END IF;

        IF COALESCE(v_change_rate, 0) <= 0 THEN
            RAISE EXCEPTION
                'Matching Plan đề nghị giữ nguyên; không có thay đổi để áp dụng.';
        END IF;

        IF v_completion_rate >= 0.75 THEN
            RAISE EXCEPTION
                'Completion rate không dưới 75%%; không cần giảm tải.';
        END IF;

    ELSE
        SELECT
            recommendation.completion_rate,
            recommendation.recommended_increase_rate,
            recommendation.adaptive_action
        INTO
            v_completion_rate,
            v_change_rate,
            v_action
        FROM public.vw_adaptive_plan_recommendation
            AS recommendation
        WHERE recommendation.plan_id = p_plan_id;

        IF NOT FOUND
           OR v_action <> 'CONTROLLED PROGRESSION'
           OR COALESCE(v_change_rate, 0) <= 0
        THEN
            RAISE EXCEPTION
                'Plan chưa đủ điều kiện Controlled Progression.';
        END IF;

        IF EXISTS
        (
            SELECT 1
            FROM public.vw_next_race_protection
            WHERE plan_id = p_plan_id
              AND days_until_race BETWEEN 0 AND 14
        ) THEN
            RAISE EXCEPTION
                'Không thể tăng tải trong vòng 14 ngày trước Race.';
        END IF;

        IF EXISTS
        (
            SELECT 1
            FROM public.plan_adjustment_runs
            WHERE plan_id = p_plan_id
              AND status = 'APPLIED'
              AND adjustment_type = 'PROGRESSION'
              AND applied_at >=
                    CURRENT_TIMESTAMP - INTERVAL '14 days'
        ) THEN
            RAISE EXCEPTION
                'Plan đã được tăng tải trong 14 ngày gần nhất.';
        END IF;
    END IF;

    IF EXISTS
    (
        SELECT 1
        FROM public.plan_adjustment_runs
        WHERE plan_id = p_plan_id
          AND status = 'APPLIED'
          AND window_start_date <= v_window_end
          AND window_end_date >= v_window_start
    ) THEN
        RAISE EXCEPTION
            'Khoảng ngày này đã có adjustment APPLIED.';
    END IF;

    IF NOT EXISTS
    (
        SELECT 1
        FROM public.vw_planned_run_workouts
        WHERE plan_id = p_plan_id
          AND UPPER(workout_type) <> 'RACE'
          AND scheduled_date
                BETWEEN v_window_start AND v_window_end
    ) THEN
        RAISE EXCEPTION
            'Không có bài chạy phù hợp trong khoảng điều chỉnh.';
    END IF;

    INSERT INTO public.plan_adjustment_runs
    (
        plan_id,
        completion_rate,
        threshold_rate,
        window_start_date,
        window_end_date,
        reduction_rate,
        increase_rate,
        adjustment_type,
        reason,
        status
    )
    VALUES
    (
        p_plan_id,
        v_completion_rate,
        CASE
            WHEN v_type = 'REDUCTION' THEN 0.75
            ELSE 0.90
        END,
        v_window_start,
        v_window_end,
        CASE
            WHEN v_type = 'REDUCTION'
                THEN v_change_rate
            ELSE 0.00
        END,
        CASE
            WHEN v_type = 'PROGRESSION'
                THEN v_change_rate
            ELSE NULL
        END,
        v_type,
        CASE
            WHEN v_type = 'REDUCTION'
                THEN 'Run completion rate dưới 75%'
            ELSE
                'Controlled progression after two strong complete weeks'
        END,
        'PREVIEW'
    )
    RETURNING plan_adjustment_runs.adjustment_run_id
    INTO v_run_id;

    INSERT INTO public.planned_workout_history
    (
        adjustment_run_id,
        planned_workout_id,
        plan_id,
        previous_scheduled_date,
        previous_workout_type,
        previous_workout_name,
        previous_planned_distance_m,
        previous_planned_duration_sec,
        previous_target_pace_sec_per_km,
        previous_target_heart_rate_min,
        previous_target_heart_rate_max,
        previous_priority_weight,
        previous_status,
        previous_notes,
        new_workout_name,
        new_planned_distance_m,
        new_planned_duration_sec,
        new_target_pace_sec_per_km,
        new_notes
    )
    SELECT
        v_run_id,
        workout.planned_workout_id,
        workout.plan_id,
        workout.scheduled_date,
        workout.workout_type,
        workout.workout_name,
        workout.planned_distance_m,
        workout.planned_duration_sec,
        workout.target_pace_sec_per_km,
        workout.target_heart_rate_min,
        workout.target_heart_rate_max,
        workout.priority_weight,
        workout.status,
        workout.notes,

        LEFT(
            workout.workout_name
            || CASE
                WHEN v_type = 'REDUCTION'
                    THEN ' [Adjusted -'
                ELSE ' [Progression +'
               END
            || ROUND(v_change_rate * 100)::INTEGER::TEXT
            || '%]',
            200
        ),

        CASE
            WHEN workout.planned_distance_m IS NULL
                THEN NULL
            WHEN v_type = 'REDUCTION'
                THEN ROUND(
                    workout.planned_distance_m
                    * (1 - v_change_rate),
                    -2
                )::INTEGER
            ELSE ROUND(
                workout.planned_distance_m
                * (1 + v_change_rate),
                -2
            )::INTEGER
        END,

        CASE
            WHEN workout.planned_duration_sec IS NULL
                THEN NULL
            WHEN v_type = 'REDUCTION'
                THEN ROUND(
                    workout.planned_duration_sec
                    * (1 - v_change_rate)
                )::INTEGER
            ELSE ROUND(
                workout.planned_duration_sec
                * (1 + v_change_rate)
            )::INTEGER
        END,

        workout.target_pace_sec_per_km,

        LEFT(
            COALESCE(workout.notes || ' | ', '')
            || CASE
                WHEN v_type = 'REDUCTION'
                    THEN
                        'Adjusted because run completion was '
                        || ROUND(
                            v_completion_rate * 100,
                            2
                        )::TEXT
                        || '%. '
                ELSE
                    'Controlled progression +'
                    || ROUND(
                        v_change_rate * 100
                    )::INTEGER::TEXT
                    || '%. '
               END
            || 'AdjustmentRunID='
            || v_run_id::TEXT,
            1000
        )

    FROM public.vw_planned_run_workouts AS workout
    WHERE workout.plan_id = p_plan_id
      AND UPPER(workout.workout_type) <> 'RACE'
      AND workout.scheduled_date
            BETWEEN v_window_start AND v_window_end;

    GET DIAGNOSTICS v_changed_count = ROW_COUNT;

    UPDATE public.planned_workouts AS current_workout
    SET
        workout_name =
            history.new_workout_name,
        planned_distance_m =
            history.new_planned_distance_m,
        planned_duration_sec =
            history.new_planned_duration_sec,
        target_pace_sec_per_km =
            history.new_target_pace_sec_per_km,
        notes =
            history.new_notes
    FROM public.planned_workout_history AS history
    WHERE history.adjustment_run_id = v_run_id
      AND history.planned_workout_id =
            current_workout.planned_workout_id;

    UPDATE public.plan_adjustment_runs
    SET
        status = 'APPLIED',
        applied_at = CURRENT_TIMESTAMP
    WHERE plan_adjustment_runs.adjustment_run_id =
            v_run_id;

    RETURN QUERY
    SELECT
        v_run_id,
        v_type,
        ROUND(v_change_rate * 100, 2)::NUMERIC(6, 2),
        v_changed_count;
END;
$$;


CREATE OR REPLACE FUNCTION
public.rollback_run_plan_adjustment
(
    p_adjustment_run_id BIGINT
)
RETURNS TABLE
(
    adjustment_run_id BIGINT,
    plan_id INTEGER,
    new_status TEXT,
    restored_workout_count INTEGER
)
LANGUAGE plpgsql
AS
$$
DECLARE
    v_plan_id INTEGER;
    v_restored_count INTEGER;
BEGIN
    SELECT adjustment.plan_id
    INTO v_plan_id
    FROM public.plan_adjustment_runs AS adjustment
    WHERE adjustment.adjustment_run_id =
            p_adjustment_run_id
      AND adjustment.status = 'APPLIED'
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Không tìm thấy adjustment APPLIED có ID=%.',
            p_adjustment_run_id;
    END IF;

    PERFORM pg_advisory_xact_lock(v_plan_id);

    IF EXISTS
    (
        SELECT 1
        FROM public.plan_adjustment_runs AS newer
        WHERE newer.plan_id = v_plan_id
          AND newer.status = 'APPLIED'
          AND newer.adjustment_run_id >
                p_adjustment_run_id
    ) THEN
        RAISE EXCEPTION
            'Không thể rollback vì plan có adjustment mới hơn.';
    END IF;

    UPDATE public.planned_workouts AS workout
    SET
        scheduled_date =
            history.previous_scheduled_date,
        workout_type =
            history.previous_workout_type,
        workout_name =
            history.previous_workout_name,
        planned_distance_m =
            history.previous_planned_distance_m,
        planned_duration_sec =
            history.previous_planned_duration_sec,
        target_pace_sec_per_km =
            history.previous_target_pace_sec_per_km,
        target_heart_rate_min =
            history.previous_target_heart_rate_min,
        target_heart_rate_max =
            history.previous_target_heart_rate_max,
        priority_weight =
            history.previous_priority_weight,
        status =
            history.previous_status,
        notes =
            history.previous_notes
    FROM public.planned_workout_history AS history
    WHERE history.adjustment_run_id =
            p_adjustment_run_id
      AND history.planned_workout_id =
            workout.planned_workout_id;

    GET DIAGNOSTICS v_restored_count = ROW_COUNT;

    IF v_restored_count = 0 THEN
        RAISE EXCEPTION
            'Adjustment không có lịch sử workout để khôi phục.';
    END IF;

    UPDATE public.plan_adjustment_runs
    SET status = 'ROLLED_BACK'
    WHERE plan_adjustment_runs.adjustment_run_id =
            p_adjustment_run_id;

    RETURN QUERY
    SELECT
        p_adjustment_run_id,
        v_plan_id,
        'ROLLED_BACK'::TEXT,
        v_restored_count;
END;
$$;


COMMIT;
BEGIN;

CREATE OR REPLACE FUNCTION public.rebuild_rule_workout_matches
(
    p_athlete_id INTEGER,
    p_plan_id INTEGER DEFAULT NULL,
    p_maximum_day_difference INTEGER DEFAULT 1,
    p_minimum_confidence NUMERIC DEFAULT 0.7000
)
RETURNS TABLE
(
    rule_matches_removed INTEGER,
    rule_matches_created INTEGER
)
LANGUAGE plpgsql
AS $function$
DECLARE
    v_requires_rebuild BOOLEAN := FALSE;
    v_removed_ids BIGINT[] := ARRAY[]::BIGINT[];
    v_removed_count INTEGER := 0;
    v_created_count INTEGER := 0;
BEGIN
    IF p_maximum_day_difference NOT BETWEEN 0 AND 7 THEN
        RAISE EXCEPTION
            'MaximumDayDifference phải từ 0 đến 7.'
            USING ERRCODE = '22023';
    END IF;

    IF p_minimum_confidence NOT BETWEEN 0 AND 1 THEN
        RAISE EXCEPTION
            'MinimumConfidence phải từ 0 đến 1.'
            USING ERRCODE = '22023';
    END IF;

    /*
        Chỉ rebuild khi một hoạt động chạy đúng ngày chưa được match,
        trong khi workout tương ứng đang bị RULE match với ngày khác.
    */
    SELECT EXISTS
    (
        SELECT 1
        FROM public.planned_workouts AS pw
        JOIN public.training_plans AS tp
          ON tp.plan_id = pw.plan_id
        JOIN public.workout_matches AS occupied
          ON occupied.planned_workout_id = pw.planned_workout_id
         AND occupied.match_method = 'RULE'
        JOIN public.completed_activities AS occupied_activity
          ON occupied_activity.activity_id = occupied.activity_id
        JOIN public.completed_activities AS exact_activity
          ON exact_activity.athlete_id = tp.athlete_id
         AND exact_activity.activity_type = 'RUN'
         AND exact_activity.activity_date = pw.scheduled_date
        WHERE tp.athlete_id = p_athlete_id
          AND
          (
              p_plan_id IS NULL
              OR pw.plan_id = p_plan_id
          )
          AND pw.planned_distance_m > 0
          AND occupied_activity.activity_date <> pw.scheduled_date
          AND NOT EXISTS
          (
              SELECT 1
              FROM public.workout_matches AS existing_match
              WHERE existing_match.activity_id =
                    exact_activity.activity_id
          )
    )
    INTO v_requires_rebuild;

    /*
        Chỉ xóa RULE match trong phạm vi được chọn.
        MANUAL match luôn được giữ nguyên.
    */
    IF v_requires_rebuild THEN
        WITH removed AS
        (
            DELETE FROM public.workout_matches AS wm
            USING
                public.planned_workouts AS pw,
                public.training_plans AS tp
            WHERE pw.planned_workout_id = wm.planned_workout_id
              AND tp.plan_id = pw.plan_id
              AND wm.match_method = 'RULE'
              AND tp.athlete_id = p_athlete_id
              AND
              (
                  p_plan_id IS NULL
                  OR pw.plan_id = p_plan_id
              )
            RETURNING wm.planned_workout_id
        )
        SELECT
            COALESCE(
                array_agg(removed.planned_workout_id),
                ARRAY[]::BIGINT[]
            ),
            COUNT(*)::INTEGER
        INTO
            v_removed_ids,
            v_removed_count
        FROM removed;

        /*
            Workout mất RULE match không còn bằng chứng hoàn thành.
            Chỉ reset khi nó không còn bất kỳ match nào khác.
        */
        UPDATE public.planned_workouts AS pw
        SET status = 'PLANNED'
        WHERE pw.planned_workout_id = ANY(v_removed_ids)
          AND NOT EXISTS
          (
              SELECT 1
              FROM public.workout_matches AS remaining_match
              WHERE remaining_match.planned_workout_id =
                    pw.planned_workout_id
          );
    END IF;

    /*
        Tạo và xếp hạng các cặp workout/activity có khả năng khớp.
    */
    WITH candidate_base AS
    (
        SELECT
            pw.planned_workout_id,
            ca.activity_id,

            ABS(ca.activity_date - pw.scheduled_date)
                AS day_difference,

            CASE
                WHEN pw.scheduled_date = ca.activity_date
                    THEN 1.0000
                WHEN ABS(
                    ca.activity_date - pw.scheduled_date
                ) = 1
                    THEN 0.7500
                ELSE 0.5000
            END::NUMERIC(5, 4) AS date_score,

            CASE
                WHEN pw.planned_distance_m IS NULL
                  OR pw.planned_distance_m = 0
                  OR ca.distance_m IS NULL
                    THEN 0.5000
                WHEN ABS(
                    ca.distance_m - pw.planned_distance_m
                ) >= pw.planned_distance_m
                    THEN 0.0000
                ELSE
                    1.0000
                    - (
                        ABS(
                            ca.distance_m
                            - pw.planned_distance_m
                        )::NUMERIC
                        / pw.planned_distance_m
                    )
            END::NUMERIC(5, 4) AS distance_score,

            CASE
                WHEN pw.planned_duration_sec IS NULL
                  OR pw.planned_duration_sec = 0
                  OR ca.duration_sec IS NULL
                    THEN 0.5000
                WHEN ABS(
                    ca.duration_sec - pw.planned_duration_sec
                ) >= pw.planned_duration_sec
                    THEN 0.0000
                ELSE
                    1.0000
                    - (
                        ABS(
                            ca.duration_sec
                            - pw.planned_duration_sec
                        )::NUMERIC
                        / pw.planned_duration_sec
                    )
            END::NUMERIC(5, 4) AS duration_score

        FROM public.planned_workouts AS pw
        JOIN public.training_plans AS tp
          ON tp.plan_id = pw.plan_id
        JOIN public.completed_activities AS ca
          ON ca.athlete_id = tp.athlete_id
         AND ABS(
             ca.activity_date - pw.scheduled_date
         ) <= p_maximum_day_difference
        WHERE tp.athlete_id = p_athlete_id
          AND
          (
              p_plan_id IS NULL
              OR pw.plan_id = p_plan_id
          )
          AND pw.planned_distance_m > 0
          AND pw.workout_type NOT IN ('REST', 'STRENGTH')
          AND NOT EXISTS
          (
              SELECT 1
              FROM public.workout_matches AS existing_workout_match
              WHERE existing_workout_match.planned_workout_id =
                    pw.planned_workout_id
          )
          AND NOT EXISTS
          (
              SELECT 1
              FROM public.workout_matches AS existing_activity_match
              WHERE existing_activity_match.activity_id =
                    ca.activity_id
          )
    ),
    candidate_scores AS
    (
        SELECT
            planned_workout_id,
            activity_id,
            day_difference,
            (
                  date_score * 0.50
                + distance_score * 0.30
                + duration_score * 0.20
            )::NUMERIC(5, 4) AS match_confidence
        FROM candidate_base
    ),
    ranked_candidates AS
    (
        SELECT
            planned_workout_id,
            activity_id,
            match_confidence,

            ROW_NUMBER() OVER
            (
                PARTITION BY planned_workout_id
                ORDER BY
                    match_confidence DESC,
                    day_difference,
                    activity_id
            ) AS planned_workout_rank,

            ROW_NUMBER() OVER
            (
                PARTITION BY activity_id
                ORDER BY
                    match_confidence DESC,
                    day_difference,
                    planned_workout_id
            ) AS activity_rank
        FROM candidate_scores
    )
    INSERT INTO public.workout_matches
    (
        planned_workout_id,
        activity_id,
        match_method,
        match_confidence,
        intensity_completion,
        structure_completion
    )
    SELECT
        ranked.planned_workout_id,
        ranked.activity_id,
        'RULE',
        ranked.match_confidence,
        NULL,
        NULL
    FROM ranked_candidates AS ranked
    WHERE ranked.planned_workout_rank = 1
      AND ranked.activity_rank = 1
      AND ranked.match_confidence >= p_minimum_confidence
    ON CONFLICT DO NOTHING;

    GET DIAGNOSTICS v_created_count = ROW_COUNT;

    /*
        Tính lại trạng thái cho các workout đã được match.
    */
    WITH match_completion AS
    (
        SELECT
            pw.planned_workout_id,

            (
                (
                    CASE
                        WHEN pw.planned_distance_m IS NULL
                          OR pw.planned_distance_m = 0
                            THEN 1.0
                        WHEN ca.distance_m >= pw.planned_distance_m
                            THEN 1.0
                        ELSE
                            ca.distance_m::NUMERIC
                            / pw.planned_distance_m
                    END
                    +
                    CASE
                        WHEN pw.planned_duration_sec IS NULL
                          OR pw.planned_duration_sec = 0
                            THEN 1.0
                        WHEN ca.duration_sec >= pw.planned_duration_sec
                            THEN 1.0
                        ELSE
                            ca.duration_sec::NUMERIC
                            / pw.planned_duration_sec
                    END
                ) / 2.0
            )::NUMERIC(6, 4) AS completion_rate

        FROM public.workout_matches AS wm
        JOIN public.planned_workouts AS pw
          ON pw.planned_workout_id = wm.planned_workout_id
        JOIN public.training_plans AS tp
          ON tp.plan_id = pw.plan_id
        JOIN public.completed_activities AS ca
          ON ca.activity_id = wm.activity_id
        WHERE tp.athlete_id = p_athlete_id
          AND
          (
              p_plan_id IS NULL
              OR pw.plan_id = p_plan_id
          )
    )
    UPDATE public.planned_workouts AS pw
    SET status =
        CASE
            WHEN mc.completion_rate >= 0.90
                THEN 'COMPLETED'
            WHEN mc.completion_rate > 0
                THEN 'PARTIAL'
            ELSE 'PLANNED'
        END
    FROM match_completion AS mc
    WHERE mc.planned_workout_id = pw.planned_workout_id;

    RETURN QUERY
    SELECT
        v_removed_count,
        v_created_count;
END;
$function$;

COMMIT;
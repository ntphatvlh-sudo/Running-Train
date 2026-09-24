BEGIN;


ALTER TABLE public.strength_sessions
    ADD COLUMN IF NOT EXISTS status VARCHAR(20),
    ADD COLUMN IF NOT EXISTS runner_notes VARCHAR(1000);


UPDATE public.strength_sessions
SET status =
    CASE
        WHEN completed THEN 'COMPLETED'
        ELSE 'PLANNED'
    END
WHERE status IS NULL;


ALTER TABLE public.strength_sessions
    ALTER COLUMN status SET DEFAULT 'PLANNED',
    ALTER COLUMN status SET NOT NULL;


ALTER TABLE public.strength_sessions
    DROP CONSTRAINT IF EXISTS ck_strength_sessions_status;


ALTER TABLE public.strength_sessions
    ADD CONSTRAINT ck_strength_sessions_status
    CHECK
    (
        status IN
        (
            'PLANNED',
            'COMPLETED',
            'SKIPPED'
        )
    );


ALTER TABLE public.mobility_sessions
    ADD COLUMN IF NOT EXISTS status VARCHAR(20),
    ADD COLUMN IF NOT EXISTS runner_notes VARCHAR(1000);


UPDATE public.mobility_sessions
SET status =
    CASE
        WHEN completed THEN 'COMPLETED'
        ELSE 'PLANNED'
    END
WHERE status IS NULL;


ALTER TABLE public.mobility_sessions
    ALTER COLUMN status SET DEFAULT 'PLANNED',
    ALTER COLUMN status SET NOT NULL;


ALTER TABLE public.mobility_sessions
    DROP CONSTRAINT IF EXISTS ck_mobility_sessions_status;


ALTER TABLE public.mobility_sessions
    ADD CONSTRAINT ck_mobility_sessions_status
    CHECK
    (
        status IN
        (
            'PLANNED',
            'COMPLETED',
            'SKIPPED'
        )
    );


COMMIT;



BEGIN;


CREATE OR REPLACE FUNCTION public.update_support_session_status
(
    p_athlete_id INTEGER,
    p_support_type VARCHAR,
    p_session_id BIGINT,
    p_status VARCHAR,
    p_runner_notes VARCHAR DEFAULT NULL
)
RETURNS TABLE
(
    updated_support_type VARCHAR,
    updated_session_id BIGINT,
    updated_status VARCHAR
)
LANGUAGE plpgsql
AS $function$
DECLARE
    v_support_type VARCHAR;
    v_status VARCHAR;
    v_updated_count INTEGER;
BEGIN
    v_support_type :=
        UPPER(BTRIM(p_support_type));

    v_status :=
        UPPER(BTRIM(p_status));


    IF v_support_type NOT IN
    (
        'STRENGTH',
        'MOBILITY'
    ) THEN
        RAISE EXCEPTION
            'Loại buổi tập không hợp lệ.';
    END IF;


    IF v_status NOT IN
    (
        'COMPLETED',
        'SKIPPED'
    ) THEN
        RAISE EXCEPTION
            'Trạng thái không hợp lệ.';
    END IF;


    IF v_support_type = 'STRENGTH' THEN

        UPDATE public.strength_sessions AS session
        SET
            completed =
                (v_status = 'COMPLETED'),

            completed_exercise_count =
                CASE
                    WHEN v_status = 'COMPLETED'
                        THEN planned_exercise_count
                    ELSE 0
                END,

            session_completion_rate =
                CASE
                    WHEN v_status = 'COMPLETED'
                        THEN 1
                    ELSE 0
                END,

            status = v_status,

            runner_notes =
                NULLIF(
                    BTRIM(p_runner_notes),
                    ''
                )

        WHERE session.strength_session_id =
                p_session_id
          AND session.athlete_id =
                p_athlete_id;


        GET DIAGNOSTICS
            v_updated_count = ROW_COUNT;


        IF v_updated_count = 1 THEN

            UPDATE public.strength_exercises
            SET exercise_completed =
                (v_status = 'COMPLETED')
            WHERE strength_session_id =
                p_session_id;

        END IF;


    ELSE

        UPDATE public.mobility_sessions AS session
        SET
            completed =
                (v_status = 'COMPLETED'),

            status = v_status,

            runner_notes =
                NULLIF(
                    BTRIM(p_runner_notes),
                    ''
                )

        WHERE session.mobility_session_id =
                p_session_id
          AND session.athlete_id =
                p_athlete_id;


        GET DIAGNOSTICS
            v_updated_count = ROW_COUNT;

    END IF;


    IF v_updated_count <> 1 THEN
        RAISE EXCEPTION
            'Không tìm thấy session thuộc runner hiện tại.';
    END IF;


    RETURN QUERY
    SELECT
        v_support_type,
        p_session_id,
        v_status;
END;
$function$;


COMMIT;




BEGIN;


CREATE OR REPLACE FUNCTION public.record_manual_run_activity
(
    p_athlete_id INTEGER,
    p_planned_workout_id BIGINT,
    p_activity_date DATE,
    p_distance_m INTEGER,
    p_duration_sec INTEGER,
    p_perceived_effort SMALLINT,
    p_fatigue_score SMALLINT,
    p_pain_flag BOOLEAN DEFAULT FALSE,
    p_notes VARCHAR DEFAULT NULL
)
RETURNS TABLE
(
    created_activity_id BIGINT,
    updated_planned_workout_id BIGINT,
    updated_workout_status VARCHAR,
    calculated_average_pace_sec_per_km INTEGER,
    calculated_completion_rate NUMERIC
)
LANGUAGE plpgsql
AS $function$
DECLARE
    v_planned_distance_m INTEGER;
    v_planned_duration_sec INTEGER;

    v_activity_id BIGINT;
    v_average_pace_sec_per_km INTEGER;

    v_distance_completion NUMERIC;
    v_duration_completion NUMERIC;
    v_completion_rate NUMERIC;

    v_workout_status VARCHAR;
BEGIN
    IF p_activity_date IS NULL
       OR p_activity_date > CURRENT_DATE THEN
        RAISE EXCEPTION
            'Ngày chạy thực tế không hợp lệ.';
    END IF;


    IF p_distance_m IS NULL
       OR p_distance_m <= 0 THEN
        RAISE EXCEPTION
            'Quãng đường phải lớn hơn 0.';
    END IF;


    IF p_duration_sec IS NULL
       OR p_duration_sec <= 0 THEN
        RAISE EXCEPTION
            'Thời gian chạy phải lớn hơn 0.';
    END IF;


    IF p_perceived_effort IS NULL
       OR p_perceived_effort NOT BETWEEN 1 AND 10 THEN
        RAISE EXCEPTION
            'RPE phải từ 1 đến 10.';
    END IF;


    IF p_fatigue_score IS NULL
       OR p_fatigue_score NOT BETWEEN 1 AND 10 THEN
        RAISE EXCEPTION
            'Mức độ mệt phải từ 1 đến 10.';
    END IF;


    SELECT
        workout.planned_distance_m,
        workout.planned_duration_sec
    INTO
        v_planned_distance_m,
        v_planned_duration_sec

    FROM public.planned_workouts AS workout

    JOIN public.training_plans AS plan
      ON plan.plan_id = workout.plan_id

    WHERE workout.planned_workout_id =
            p_planned_workout_id
      AND plan.athlete_id =
            p_athlete_id
      AND workout.planned_distance_m IS NOT NULL
      AND workout.planned_distance_m > 0

    FOR UPDATE OF workout;


    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Không tìm thấy bài chạy thuộc runner hiện tại.';
    END IF;


    IF EXISTS
    (
        SELECT 1
        FROM public.workout_matches AS match
        WHERE match.planned_workout_id =
            p_planned_workout_id
    ) THEN
        RAISE EXCEPTION
            'Bài chạy này đã có hoạt động được liên kết.';
    END IF;


    v_average_pace_sec_per_km :=
        ROUND
        (
            p_duration_sec::NUMERIC
            * 1000
            / p_distance_m
        );


    v_distance_completion :=
        LEAST
        (
            p_distance_m::NUMERIC
            / v_planned_distance_m,
            1
        );


    v_duration_completion :=
        CASE
            WHEN v_planned_duration_sec IS NULL
              OR v_planned_duration_sec = 0
                THEN 1

            ELSE
                LEAST
                (
                    p_duration_sec::NUMERIC
                    / v_planned_duration_sec,
                    1
                )
        END;


    v_completion_rate :=
        ROUND
        (
            (
                v_distance_completion
                + v_duration_completion
            )
            / 2,
            4
        );


    v_workout_status :=
        CASE
            WHEN v_completion_rate >= 0.90
                THEN 'COMPLETED'
            ELSE 'PARTIAL'
        END;


    INSERT INTO public.completed_activities AS activity
    (
        athlete_id,
        external_source,
        activity_date,
        activity_type,
        distance_m,
        duration_sec,
        moving_time_sec,
        average_pace_sec_per_km,
        perceived_effort,
        fatigue_score,
        pain_flag,
        notes
    )
    VALUES
    (
        p_athlete_id,
        'MANUAL',
        p_activity_date,
        'RUN',
        p_distance_m,
        p_duration_sec,
        p_duration_sec,
        v_average_pace_sec_per_km,
        p_perceived_effort,
        p_fatigue_score,
        COALESCE(p_pain_flag, FALSE),
        NULLIF(BTRIM(p_notes), '')
    )
    RETURNING activity.activity_id
    INTO v_activity_id;


    INSERT INTO public.workout_matches
    (
        planned_workout_id,
        activity_id,
        match_method,
        match_confidence,
        intensity_completion,
        structure_completion
    )
    VALUES
    (
        p_planned_workout_id,
        v_activity_id,
        'MANUAL',
        1.0000,
        v_completion_rate,
        NULL
    );


    UPDATE public.planned_workouts
    SET status = v_workout_status
    WHERE planned_workout_id =
        p_planned_workout_id;


    RETURN QUERY
    SELECT
        v_activity_id,
        p_planned_workout_id,
        v_workout_status,
        v_average_pace_sec_per_km,
        v_completion_rate;
END;
$function$;


CREATE OR REPLACE FUNCTION public.upsert_daily_wellness
(
    p_athlete_id INTEGER,
    p_wellness_date DATE,

    p_sleep_hours NUMERIC,
    p_sleep_quality SMALLINT,

    p_weight_kg NUMERIC DEFAULT NULL,
    p_resting_heart_rate SMALLINT DEFAULT NULL,
    p_hrv_ms NUMERIC DEFAULT NULL,

    p_fatigue_score SMALLINT DEFAULT NULL,
    p_stress_score SMALLINT DEFAULT NULL,
    p_soreness_score SMALLINT DEFAULT NULL,

    p_energy_score SMALLINT DEFAULT NULL,
    p_mood_score SMALLINT DEFAULT NULL,
    p_motivation_score SMALLINT DEFAULT NULL,

    p_notes VARCHAR DEFAULT NULL
)
RETURNS TABLE
(
    saved_daily_wellness_id BIGINT,
    calculated_readiness_score NUMERIC,
    calculated_readiness_band VARCHAR
)
LANGUAGE plpgsql
AS $function$
DECLARE
    v_readiness_score NUMERIC(5, 2);
    v_readiness_band VARCHAR(30);
    v_daily_wellness_id BIGINT;
BEGIN
    IF p_wellness_date IS NULL
       OR p_wellness_date > CURRENT_DATE THEN
        RAISE EXCEPTION
            'Ngày wellness không hợp lệ.';
    END IF;


    IF p_sleep_hours IS NULL
       OR p_sleep_hours <= 0
       OR p_sleep_hours > 24 THEN
        RAISE EXCEPTION
            'Số giờ ngủ phải lớn hơn 0 và không quá 24.';
    END IF;


    IF p_sleep_quality IS NULL
       OR p_sleep_quality NOT BETWEEN 1 AND 5 THEN
        RAISE EXCEPTION
            'Chất lượng giấc ngủ phải từ 1 đến 5.';
    END IF;


    IF p_fatigue_score IS NULL
       OR p_fatigue_score NOT BETWEEN 1 AND 5 THEN
        RAISE EXCEPTION
            'Mệt mỏi phải từ 1 đến 5.';
    END IF;


    IF p_stress_score IS NULL
       OR p_stress_score NOT BETWEEN 1 AND 5 THEN
        RAISE EXCEPTION
            'Căng thẳng phải từ 1 đến 5.';
    END IF;


    IF p_soreness_score IS NULL
       OR p_soreness_score NOT BETWEEN 1 AND 5 THEN
        RAISE EXCEPTION
            'Đau nhức phải từ 1 đến 5.';
    END IF;


    IF p_energy_score IS NULL
       OR p_energy_score NOT BETWEEN 1 AND 5 THEN
        RAISE EXCEPTION
            'Năng lượng phải từ 1 đến 5.';
    END IF;


    IF p_mood_score IS NULL
       OR p_mood_score NOT BETWEEN 1 AND 5 THEN
        RAISE EXCEPTION
            'Tâm trạng phải từ 1 đến 5.';
    END IF;


    IF p_motivation_score IS NULL
       OR p_motivation_score NOT BETWEEN 1 AND 5 THEN
        RAISE EXCEPTION
            'Động lực phải từ 1 đến 5.';
    END IF;


    IF p_weight_kg IS NOT NULL
       AND p_weight_kg <= 0 THEN
        RAISE EXCEPTION
            'Cân nặng phải lớn hơn 0.';
    END IF;


    IF p_resting_heart_rate IS NOT NULL
       AND p_resting_heart_rate <= 0 THEN
        RAISE EXCEPTION
            'Nhịp tim nghỉ phải lớn hơn 0.';
    END IF;


    IF p_hrv_ms IS NOT NULL
       AND p_hrv_ms < 0 THEN
        RAISE EXCEPTION
            'HRV không được âm.';
    END IF;


    v_readiness_score :=
        ROUND
        (
            (
                p_sleep_quality
                + (6 - p_fatigue_score)
                + (6 - p_stress_score)
                + (6 - p_soreness_score)
                + p_energy_score
                + p_mood_score
                + p_motivation_score
            )::NUMERIC
            / 35
            * 100,
            2
        );


    v_readiness_band :=
        CASE
            WHEN v_readiness_score >= 80
                THEN 'READY'
            WHEN v_readiness_score >= 60
                THEN 'MODERATE'
            ELSE 'LOW'
        END;


    INSERT INTO public.daily_wellness AS wellness
    (
        athlete_id,
        wellness_date,
        sleep_hours,
        sleep_quality,
        weight_kg,
        resting_heart_rate,
        hrv_ms,
        fatigue_score,
        stress_score,
        soreness_score,
        energy_score,
        mood_score,
        motivation_score,
        readiness_score,
        readiness_band,
        data_quality,
        notes,
        imported_at
    )
    VALUES
    (
        p_athlete_id,
        p_wellness_date,
        p_sleep_hours,
        p_sleep_quality,
        p_weight_kg,
        p_resting_heart_rate,
        p_hrv_ms,
        p_fatigue_score,
        p_stress_score,
        p_soreness_score,
        p_energy_score,
        p_mood_score,
        p_motivation_score,
        v_readiness_score,
        v_readiness_band,
        'COMPLETE',
        NULLIF(BTRIM(p_notes), ''),
        CURRENT_TIMESTAMP
    )

    ON CONFLICT
    (
        athlete_id,
        wellness_date
    )
    DO UPDATE
    SET
        sleep_hours =
            EXCLUDED.sleep_hours,

        sleep_quality =
            EXCLUDED.sleep_quality,

        weight_kg =
            EXCLUDED.weight_kg,

        resting_heart_rate =
            EXCLUDED.resting_heart_rate,

        hrv_ms =
            EXCLUDED.hrv_ms,

        fatigue_score =
            EXCLUDED.fatigue_score,

        stress_score =
            EXCLUDED.stress_score,

        soreness_score =
            EXCLUDED.soreness_score,

        energy_score =
            EXCLUDED.energy_score,

        mood_score =
            EXCLUDED.mood_score,

        motivation_score =
            EXCLUDED.motivation_score,

        readiness_score =
            EXCLUDED.readiness_score,

        readiness_band =
            EXCLUDED.readiness_band,

        data_quality =
            EXCLUDED.data_quality,

        notes =
            EXCLUDED.notes,

        imported_at =
            CURRENT_TIMESTAMP

    RETURNING wellness.daily_wellness_id
    INTO v_daily_wellness_id;


    RETURN QUERY
    SELECT
        v_daily_wellness_id,
        v_readiness_score,
        v_readiness_band;
END;
$function$;


COMMIT;
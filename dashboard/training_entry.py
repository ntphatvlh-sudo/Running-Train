from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from src.workout_logging import (
    RunActivityInput,
    WellnessInput,
    WorkoutLoggingError,
    save_daily_wellness,
    save_run_activity,
    update_support_session,
)


def load_records(
    engine: Engine,
    query: str,
    params: dict,
) -> pd.DataFrame:
    with engine.connect() as connection:
        return pd.read_sql(
            text(query),
            connection,
            params=params,
        )


def refresh_after_save(
    clear_dashboard_cache,
) -> None:
    clear_dashboard_cache()


def render_run_form(
    engine: Engine,
    athlete_id: int,
    plan_id: int,
    clear_dashboard_cache,
) -> None:
    runs = load_records(
        engine,
        """
        SELECT
            workout.planned_workout_id,
            workout.scheduled_date,
            workout.workout_name,
            workout.planned_distance_m,
            workout.planned_duration_sec,
            workout.status
        FROM public.planned_workouts AS workout
        JOIN public.training_plans AS plan
          ON plan.plan_id = workout.plan_id
        WHERE workout.plan_id = :plan_id
          AND plan.athlete_id = :athlete_id
          AND plan.status = 'ACTIVE'
          AND workout.planned_distance_m > 0
          AND workout.scheduled_date <= CURRENT_DATE
          AND workout.status NOT IN
          (
              'COMPLETED',
              'PARTIAL',
              'SKIPPED'
          )
        ORDER BY workout.scheduled_date DESC
        """,
        {
            "plan_id": plan_id,
            "athlete_id": athlete_id,
        },
    )

    if runs.empty:
        st.info(
            "Không có bài chạy đang chờ ghi nhận "
            "trong plan ACTIVE."
        )
        return

    runs["label"] = runs.apply(
        lambda row: (
            f"{row['scheduled_date']} — "
            f"{row['workout_name']} "
            f"({row['planned_distance_m'] / 1000:.1f} km)"
        ),
        axis=1,
    )

    labels = runs["label"].tolist()

    with st.form("web_run_activity_form"):
        selected_label = st.selectbox(
            "Bài chạy",
            options=labels,
        )

        selected = runs[
            runs["label"] == selected_label
        ].iloc[0]

        activity_date = st.date_input(
            "Ngày chạy thực tế",
            value=pd.Timestamp(
                selected["scheduled_date"]
            ).date(),
            max_value=date.today(),
        )

        distance_km = st.number_input(
            "Quãng đường thực tế (km)",
            min_value=0.01,
            max_value=500.0,
            value=float(
                selected["planned_distance_m"]
                / 1000
            ),
            step=0.1,
        )

        planned_duration = (
            int(selected["planned_duration_sec"])
            if pd.notna(
                selected["planned_duration_sec"]
            )
            else 1800
        )

        hour_column, minute_column, second_column = (
            st.columns(3)
        )

        with hour_column:
            duration_hours = st.number_input(
                "Giờ",
                min_value=0,
                max_value=24,
                value=planned_duration // 3600,
            )

        with minute_column:
            duration_minutes = st.number_input(
                "Phút",
                min_value=0,
                max_value=59,
                value=(
                    planned_duration % 3600
                ) // 60,
            )

        with second_column:
            duration_seconds = st.number_input(
                "Giây",
                min_value=0,
                max_value=59,
                value=planned_duration % 60,
            )

        effort_column, fatigue_column = (
            st.columns(2)
        )

        with effort_column:
            perceived_effort = st.slider(
                "RPE",
                min_value=1,
                max_value=10,
                value=5,
            )

        with fatigue_column:
            fatigue_score = st.slider(
                "Mức độ mệt sau chạy",
                min_value=1,
                max_value=10,
                value=5,
            )

        pain_flag = st.checkbox(
            "Có đau hoặc khó chịu bất thường"
        )

        notes = st.text_area(
            "Ghi chú chạy (không bắt buộc)"
        )

        submitted = st.form_submit_button(
            "Lưu hoạt động chạy",
            type="primary",
            use_container_width=True,
        )

    if not submitted:
        return

    duration_sec = (
        int(duration_hours) * 3600
        + int(duration_minutes) * 60
        + int(duration_seconds)
    )

    try:
        with engine.begin() as connection:
            result = save_run_activity(
                connection,
                RunActivityInput(
                    athlete_id=athlete_id,
                    planned_workout_id=int(
                        selected[
                            "planned_workout_id"
                        ]
                    ),
                    activity_date=activity_date,
                    distance_km=float(distance_km),
                    duration_sec=duration_sec,
                    perceived_effort=int(
                        perceived_effort
                    ),
                    fatigue_score=int(
                        fatigue_score
                    ),
                    pain_flag=bool(pain_flag),
                    notes=notes,
                ),
            )

        pace = int(
            result[
                "calculated_average_pace_sec_per_km"
            ]
        )

        st.success(
            "Đã lưu hoạt động. "
            f"Trạng thái: "
            f"{result['updated_workout_status']}. "
            f"Pace: {pace // 60}:"
            f"{pace % 60:02d}/km."
        )

        refresh_after_save(
            clear_dashboard_cache
        )

    except (
        WorkoutLoggingError,
        SQLAlchemyError,
    ) as error:
        st.error(str(error))


def render_support_form(
    engine: Engine,
    athlete_id: int,
    plan_id: int,
    support_type: str,
    clear_dashboard_cache,
) -> None:
    if support_type == "STRENGTH":
        query = """
            SELECT
                session.strength_session_id
                    AS session_id,
                session.session_date,
                COALESCE
                (
                    workout.workout_name,
                    'Strength'
                ) AS session_name,
                session.status
            FROM public.strength_sessions
                AS session
            JOIN public.training_plans AS plan
              ON plan.plan_id = :plan_id
             AND plan.athlete_id =
                    session.athlete_id
             AND session.session_date
                    BETWEEN plan.start_date
                        AND plan.end_date
            LEFT JOIN public.planned_workouts
                AS workout
              ON workout.plan_id = plan.plan_id
             AND workout.scheduled_date =
                    session.session_date
             AND workout.workout_type =
                    'STRENGTH'
            WHERE session.athlete_id =
                    :athlete_id
              AND plan.status = 'ACTIVE'
              AND session.session_date <=
                    CURRENT_DATE
              AND session.status = 'PLANNED'
            ORDER BY session.session_date DESC
        """
    else:
        query = """
            SELECT
                session.mobility_session_id
                    AS session_id,
                session.session_date,
                COALESCE
                (
                    NULLIF(session.routine, ''),
                    'Mobility'
                ) AS session_name,
                session.status
            FROM public.mobility_sessions
                AS session
            JOIN public.training_plans AS plan
              ON plan.plan_id = :plan_id
             AND plan.athlete_id =
                    session.athlete_id
             AND session.session_date
                    BETWEEN plan.start_date
                        AND plan.end_date
            WHERE session.athlete_id =
                    :athlete_id
              AND plan.status = 'ACTIVE'
              AND session.session_date <=
                    CURRENT_DATE
              AND session.status = 'PLANNED'
            ORDER BY session.session_date DESC
        """

    sessions = load_records(
        engine,
        query,
        {
            "plan_id": plan_id,
            "athlete_id": athlete_id,
        },
    )

    display_name = (
        "Strength"
        if support_type == "STRENGTH"
        else "Mobility"
    )

    if sessions.empty:
        st.info(
            f"Không có buổi {display_name} "
            "đang chờ cập nhật."
        )
        return

    sessions["label"] = sessions.apply(
        lambda row: (
            f"{row['session_date']} — "
            f"{row['session_name']}"
        ),
        axis=1,
    )

    with st.form(
        f"{support_type.lower()}_status_form"
    ):
        selected_label = st.selectbox(
            f"Buổi {display_name}",
            options=sessions["label"].tolist(),
        )

        result_label = st.radio(
            "Kết quả",
            options=[
                "Hoàn thành",
                "Bỏ buổi",
            ],
            horizontal=True,
        )

        runner_notes = st.text_area(
            "Ghi chú (không bắt buộc)"
        )

        submitted = st.form_submit_button(
            "Lưu trạng thái",
            type="primary",
            use_container_width=True,
        )

    if not submitted:
        return

    selected = sessions[
        sessions["label"] == selected_label
    ].iloc[0]

    status = (
        "COMPLETED"
        if result_label == "Hoàn thành"
        else "SKIPPED"
    )

    try:
        with engine.begin() as connection:
            update_support_session(
                connection,
                athlete_id=athlete_id,
                support_type=support_type,
                session_id=int(
                    selected["session_id"]
                ),
                status=status,
                runner_notes=runner_notes,
            )

        st.success(
            f"Đã cập nhật {display_name}: "
            f"{status}."
        )

        refresh_after_save(
            clear_dashboard_cache
        )

    except (
        WorkoutLoggingError,
        SQLAlchemyError,
    ) as error:
        st.error(str(error))


def render_wellness_form(
    engine: Engine,
    athlete_id: int,
    clear_dashboard_cache,
) -> None:
    with st.form("daily_wellness_form"):
        wellness_date = st.date_input(
            "Ngày",
            value=date.today(),
            max_value=date.today(),
        )

        sleep_hours = st.number_input(
            "Số giờ ngủ",
            min_value=0.1,
            max_value=24.0,
            value=7.0,
            step=0.25,
        )

        sleep_quality = st.slider(
            "Chất lượng giấc ngủ",
            1,
            5,
            3,
        )

        fatigue_score = st.slider(
            "Mệt mỏi",
            1,
            5,
            3,
        )

        stress_score = st.slider(
            "Căng thẳng",
            1,
            5,
            3,
        )

        soreness_score = st.slider(
            "Đau nhức",
            1,
            5,
            3,
        )

        energy_score = st.slider(
            "Năng lượng",
            1,
            5,
            3,
        )

        mood_score = st.slider(
            "Tâm trạng",
            1,
            5,
            3,
        )

        motivation_score = st.slider(
            "Động lực",
            1,
            5,
            3,
        )

        with st.expander(
            "Chỉ số bổ sung (không bắt buộc)"
        ):
            weight_kg = st.number_input(
                "Cân nặng (kg)",
                min_value=0.0,
                max_value=300.0,
                value=0.0,
                step=0.1,
            )

            resting_heart_rate = st.number_input(
                "Nhịp tim nghỉ",
                min_value=0,
                max_value=250,
                value=0,
            )

            hrv_ms = st.number_input(
                "HRV (ms)",
                min_value=0.0,
                max_value=500.0,
                value=0.0,
                step=0.1,
            )

        notes = st.text_area(
            "Ghi chú Wellness "
            "(không bắt buộc)"
        )

        submitted = st.form_submit_button(
            "Lưu Daily Wellness",
            type="primary",
            use_container_width=True,
        )

    if not submitted:
        return

    try:
        with engine.begin() as connection:
            result = save_daily_wellness(
                connection,
                WellnessInput(
                    athlete_id=athlete_id,
                    wellness_date=wellness_date,
                    sleep_hours=float(
                        sleep_hours
                    ),
                    sleep_quality=int(
                        sleep_quality
                    ),
                    fatigue_score=int(
                        fatigue_score
                    ),
                    stress_score=int(
                        stress_score
                    ),
                    soreness_score=int(
                        soreness_score
                    ),
                    energy_score=int(
                        energy_score
                    ),
                    mood_score=int(
                        mood_score
                    ),
                    motivation_score=int(
                        motivation_score
                    ),
                    weight_kg=(
                        float(weight_kg)
                        if weight_kg > 0
                        else None
                    ),
                    resting_heart_rate=(
                        int(resting_heart_rate)
                        if resting_heart_rate > 0
                        else None
                    ),
                    hrv_ms=(
                        float(hrv_ms)
                        if hrv_ms > 0
                        else None
                    ),
                    notes=notes,
                ),
            )

        st.success(
            "Đã lưu Daily Wellness. "
            f"Readiness: "
            f"{float(result['calculated_readiness_score']):.1f}"
            f" — "
            f"{result['calculated_readiness_band']}."
        )

        refresh_after_save(
            clear_dashboard_cache
        )

    except (
        WorkoutLoggingError,
        SQLAlchemyError,
    ) as error:
        st.error(str(error))


def render_training_entry(
    engine: Engine,
    athlete_id: int,
    plan_id: int,
    clear_dashboard_cache,
) -> None:
    st.header("Ghi nhận tập luyện và Wellness")

    run_tab, strength_tab, mobility_tab, wellness_tab = (
        st.tabs(
            [
                "🏃 Run",
                "🏋️ Strength",
                "🧘 Mobility",
                "❤️ Wellness",
            ]
        )
    )

    with run_tab:
        render_run_form(
            engine,
            athlete_id,
            plan_id,
            clear_dashboard_cache,
        )

    with strength_tab:
        render_support_form(
            engine,
            athlete_id,
            plan_id,
            "STRENGTH",
            clear_dashboard_cache,
        )

    with mobility_tab:
        render_support_form(
            engine,
            athlete_id,
            plan_id,
            "MOBILITY",
            clear_dashboard_cache,
        )

    with wellness_tab:
        render_wellness_form(
            engine,
            athlete_id,
            clear_dashboard_cache,
        )
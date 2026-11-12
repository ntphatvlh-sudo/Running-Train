from datetime import date, timedelta

import pandas as pd
import streamlit as st
from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.plan_generator import (
    GeneratedTrainingPlan,
    TrainingPlanInput,
    build_daily_mobility,
    validate_generated_plan,
)
from src.ai_plan_service import (
    convert_and_validate_ai_workouts,

    request_ai_workouts,
)


def load_latest_pr(
    read_query,
    athlete_id: int,
):
    result = read_query(
        """
        SELECT
            distance_m,
            completed_duration_sec,
            achieved_date
        FROM public.athlete_personal_records
        WHERE athlete_id = :athlete_id
        ORDER BY achieved_date DESC, personal_record_id DESC
        LIMIT 1
        """,
        {"athlete_id": athlete_id},
    )

    if result.empty:
        return None

    return result.iloc[0].to_dict()

def save_confirmed_plan(
    engine: Engine,
    *,
    athlete_id: int,
    goal_id: int,
    plan_name: str,
    start_date: date,
    end_date: date,
    planned_days_per_week: int,
    plan_level: str,
    workouts: list,
    daily_mobility: list,
) -> int:
    with engine.begin() as connection:
        # Cancel overlapping active plans, keeping their history.
        connection.execute(
            text(
                """
                UPDATE public.training_plans
                SET status = 'CANCELLED'
                WHERE athlete_id = :athlete_id
                  AND status = 'ACTIVE'
                  AND daterange(start_date, end_date, '[]')
                      && daterange(:start_date, :end_date, '[]')
                """
            ),
            {
                "athlete_id": athlete_id,
                "start_date": start_date,
                "end_date": end_date,
            },
        )

        plan_id = connection.execute(
            text(
                """
                INSERT INTO public.training_plans
                (
                    athlete_id,
                    goal_id,
                    plan_name,
                    start_date,
                    end_date,
                    planned_days_per_week,
                    plan_level,
                    status
                )
                VALUES
                (
                    :athlete_id,
                    :goal_id,
                    :plan_name,
                    :start_date,
                    :end_date,
                    :planned_days_per_week,
                    :plan_level,
                    'ACTIVE'
                )
                RETURNING plan_id
                """
            ),
            {
                "athlete_id": athlete_id,
                "goal_id": goal_id,
                "plan_name": plan_name,
                "start_date": start_date,
                "end_date": end_date,
                "planned_days_per_week": planned_days_per_week,
                "plan_level": plan_level,
            },
        ).scalar_one()

        connection.execute(
            text(
                """
                INSERT INTO public.planned_workouts
                (
                    plan_id,
                    scheduled_date,
                    workout_type,
                    workout_name,
                    planned_distance_m,
                    planned_duration_sec,
                    target_pace_sec_per_km,
                    notes
                )
                VALUES
                (
                    :plan_id,
                    :scheduled_date,
                    :workout_type,
                    :workout_name,
                    :distance_m,
                    :duration_sec,
                    :pace,
                    :notes
                )
                """
            ),
            [
                {
                    "plan_id": plan_id,
                    "scheduled_date": workout.scheduled_date,
                    "workout_type": workout.workout_type,
                    "workout_name": workout.workout_name,
                    "distance_m": workout.planned_distance_m,
                    "duration_sec": workout.planned_duration_sec,
                    "pace": workout.target_pace_sec_per_km,
                    "notes": workout.notes,
                }
                for workout in workouts
            ],
        )

        connection.execute(
            text(
                """
                INSERT INTO public.mobility_sessions
                (
                    athlete_id,
                    external_session_id,
                    session_date,
                    routine,
                    completed,
                    plan_id,
                    status
                )
                VALUES
                (
                    :athlete_id,
                    :external_session_id,
                    :session_date,
                    :routine,
                    FALSE,
                    :plan_id,
                    'PLANNED'
                )
                """
            ),
            [
                {
                    "athlete_id": athlete_id,
                    "external_session_id": (
                        f"WEBPLAN-{plan_id}-{item.scheduled_date.isoformat()}"
                    ),
                    "session_date": item.scheduled_date,
                    "routine": item.routine,
                    "plan_id": plan_id,
                }
                for item in daily_mobility
            ],
        )

    return int(plan_id)


def load_mobility_reference_routines(
    read_query,
    athlete_id: int,
) -> dict[str, str]:
    query = """
        WITH routine_usage AS (
            SELECT
                CASE
                    WHEN btrim(routine) ILIKE 'AM activation:%'
                        THEN 'ACTIVATION'
                    WHEN btrim(routine) ILIKE 'Recovery mobility:%'
                        THEN 'RECOVERY'
                END AS routine_type,
                btrim(routine) AS routine,
                COUNT(*) FILTER (
                    WHERE athlete_id = :athlete_id
                ) AS athlete_usage,
                COUNT(*) AS total_usage
            FROM public.mobility_sessions
            WHERE routine IS NOT NULL
              AND btrim(routine) <> ''
              AND (
                    btrim(routine) ILIKE 'AM activation:%'
                 OR btrim(routine) ILIKE 'Recovery mobility:%'
              )
            GROUP BY 1, 2
        ),
        ranked_routines AS (
            SELECT
                routine_type,
                routine,
                ROW_NUMBER() OVER (
                    PARTITION BY routine_type
                    ORDER BY
                        athlete_usage DESC,
                        total_usage DESC,
                        routine
                ) AS routine_rank
            FROM routine_usage
        )
        SELECT routine_type, routine
        FROM ranked_routines
        WHERE routine_rank = 1
    """

    result = read_query(
        query,
        {"athlete_id": athlete_id},
    )

    routines = {
        row["routine_type"]: row["routine"].strip()
        for row in result.to_dict(orient="records")
    }

    required_types = {"ACTIVATION", "RECOVERY"}
    missing_types = required_types - routines.keys()

    if missing_types:
        raise ValueError(
            "Database thiếu mẫu Mobility: "
            + ", ".join(sorted(missing_types))
        )

    return routines

def render_ten_k_preview(
    read_query,
    athlete_id: int,
    engine: Engine,
) -> None:
    st.header("Preview kế hoạch")

    goals = read_query(
        """
        SELECT
            goal_id,
            goal_name,
            target_distance_m,
            target_duration_sec,
            target_date
        FROM public.goals
        WHERE athlete_id = :athlete_id
          AND status = 'ACTIVE'
          AND target_distance_m IS NOT NULL
        ORDER BY priority_level, target_date, goal_id
        """,
        {"athlete_id": athlete_id},
    )

    if goals.empty:
        st.info("Hãy tạo goal ACTIVE có cự ly trước khi tạo Preview.")
        return

    goal_labels = {
        int(row.goal_id):
            f"{row.goal_name} — {row.target_distance_m / 1000:g} km"
        for row in goals.itertuples()
    }

    selected_goal_id = st.selectbox(
        "Goal dùng cho plan mới",
        options=list(goal_labels),
        format_func=lambda goal_id: goal_labels[goal_id],
    )
    selected_goal = goals.loc[
        goals["goal_id"] == selected_goal_id
    ].iloc[0]

    race_date = pd.Timestamp(selected_goal["target_date"]).date()
    goal_distance_m = int(selected_goal["target_distance_m"])

    latest_pr = load_latest_pr(
        read_query,
        athlete_id,
    )

    if latest_pr is None:
        st.warning("Chưa có PR trong hồ sơ runner. Cần có PR trước khi tạo Preview.")
        return

    st.caption(
        f"PR gần nhất trong hồ sơ: {latest_pr['distance_m'] / 1000:g} km, "
        f"{latest_pr['completed_duration_sec'] // 60} phút, "
        f"ngày {latest_pr['achieved_date']}."
    )

    st.sidebar.header("Thông tin kế hoạch")


    plan_level = st.sidebar.selectbox(
        "Trình độ",
        ["BEGINNER", "INTERMEDIATE", "ADVANCED"],
    )

    allowed_goal_distance = {
        "BEGINNER": [5_000, 10_000],
        "INTERMEDIATE": [10_000, 21_097],
        "ADVANCED": [21_097, 42_195],
    }

    if goal_distance_m not in allowed_goal_distance[plan_level]:
        st.warning("Cự ly goal không phù hợp với trình độ đã chọn.")
        return

    allowed_runs_per_week = {
        "BEGINNER": [4],
        "INTERMEDIATE": [4,5],
        "ADVANCED": [5],
    }

    has_injury = st.sidebar.checkbox(
        "Đang có chấn thương",
        value=False,
    )

    runs_per_week = st.sidebar.selectbox(
        "Số buổi chạy/tuần",
        options=allowed_runs_per_week[plan_level]
    )

    weekday_names = [
        'Thứ Hai',
        'Thứ Ba',
        'Thứ Tư',
        'Thứ Năm',
        'Thứ Sáu',
        'Thứ Bảy',
        'Chủ Nhật',
    ]

    rest_weekday = st.sidebar.selectbox(
        "Ngày nghỉ chính",
        options=range(7),
        format_func=lambda weekday: weekday_names[weekday],
    )

    plan_input = TrainingPlanInput(
        race_date=race_date,
        goal_distance_m=goal_distance_m,
        latest_pr_distance_m=int(latest_pr["distance_m"]),
        latest_pr_duration_sec=int(latest_pr["completed_duration_sec"]),
        runs_per_week=runs_per_week,
        plan_level=plan_level,
        rest_weekday=rest_weekday,
        has_injury=has_injury,
        target_duration_sec=(
            int(selected_goal["target_duration_sec"])
            if pd.notna(selected_goal["target_duration_sec"])
            else None
        ),    
    )

    full_plan_start = (
        plan_input.race_date
        - timedelta(weeks=17)
        + timedelta(days=1)
    )

    today_week_start = date.today() - timedelta(
        days=date.today().weekday()
    )

    available_start = max(full_plan_start, today_week_start)
    available_weeks = (
        plan_input.race_date - available_start
    ).days // 7 + 1

    if available_weeks < 17:
        st.warning(
            f"Chỉ còn {available_weeks} tuần đến ngày thi. "
            "Kế hoạch đã được rút ngắn; thời gian này ít hơn "
            "mức khuyến nghị 17 tuần."
        )

    if st.button("Tạo Preview kế hoạch"):
        for key in (
            "plan_preview_workouts",
            "plan_preview_mobility",
            "plan_preview_athlete_id",
            "plan_preview_input",
            f"saved_plan_{athlete_id}_{selected_goal_id}",
            f"confirm_plan_{athlete_id}",
        ):
            st.session_state.pop(key, None)

        try:
            mobility_templates = load_mobility_reference_routines(
                read_query,
                athlete_id,
            )
            daily_mobility = build_daily_mobility(
                start_date=available_start,
                race_date=plan_input.race_date,
                rest_weekday=plan_input.rest_weekday,
                activation_routine=mobility_templates["ACTIVATION"],
                recovery_routine=mobility_templates["RECOVERY"],
            )

            ai_output = request_ai_workouts(
                plan_input,
                available_start,
            )
            workouts = convert_and_validate_ai_workouts(
                ai_output,
                plan_input,
                available_start,
            )

            generated_plan = GeneratedTrainingPlan(
                workouts=workouts,
                daily_mobility=daily_mobility,
            )
            validate_generated_plan(
                generated_plan,
                available_start,
                plan_input.race_date,
            )
        except Exception as error:
            st.error(f"Không tạo được Preview: {error}")
        else:
            st.session_state["plan_preview_workouts"] = workouts
            st.session_state["plan_preview_mobility"] = daily_mobility
            st.session_state["plan_preview_athlete_id"] = athlete_id
            st.session_state["plan_preview_input"] = plan_input

    if (
        "plan_preview_workouts" not in st.session_state
        or "plan_preview_mobility" not in st.session_state
        or st.session_state.get("plan_preview_athlete_id") != athlete_id
        or st.session_state.get("plan_preview_input") != plan_input
    ):
        st.info("Nhấn “Tạo Preview kế hoạch” để tạo lịch.")
        return

    workouts = st.session_state["plan_preview_workouts"]

    rows = [
        {
            "Ngày": workout.scheduled_date,
            "Loại": workout.workout_type,
            "Tên buổi tập": workout.workout_name,
            "Quãng đường km": (
                None
                if workout.planned_distance_m is None
                else workout.planned_distance_m/1000
            ),
            "Thời lượng phút": (
                None
                if workout.planned_duration_sec is None
                else round(workout.planned_duration_sec/60)
            ),
            "Pace giây/km": workout.target_pace_sec_per_km,
            "Ghi chú": workout.notes,
        }
        for workout in workouts
    ]

    preview = pd.DataFrame(rows)

    training_session_count = sum(
        workout.workout_type != "REST"
        for workout in workouts
    )
    st.metric("Số buổi tập", training_session_count)
    st.metric(
        "Tổng quãng đường km",
        round(preview["Quãng đường km"].sum(), 1),
    )

    st.dataframe(
        preview,
        hide_index=True,
    )


    daily_mobility = st.session_state["plan_preview_mobility"]
    mobility_preview = pd.DataFrame(
        [
            {
                "Ngày": item.scheduled_date,
                "Bài Mobility": item.routine,
            }
            for item in daily_mobility
        ]
    )

    st.subheader("Mobility hằng ngày")
    st.dataframe(mobility_preview, hide_index=True)

    st.divider()
    st.subheader("Xác nhận bản Preview")

    save_key = f"saved_plan_{athlete_id}_{selected_goal_id}"

    confirmed = st.checkbox(
        "Tôi đã xem và xác nhận bản Preview này",
        key=f"confirm_plan_{athlete_id}",
    )

    if confirmed:
        saved_plan_id = st.session_state.get(save_key)

        if saved_plan_id is not None:
            st.success(f"Plan #{saved_plan_id} đã được kích hoạt.")
        elif st.button(
            "Xác nhận và kích hoạt plan",
            type="primary",
            key=f"activate_plan_{athlete_id}_{selected_goal_id}",
        ):
            try:
                plan_id = save_confirmed_plan(
                    engine,
                    athlete_id=athlete_id,
                    goal_id=int(selected_goal_id),
                    plan_name=f"{selected_goal['goal_name']} — {plan_level}",
                    start_date=available_start,
                    end_date=race_date,
                    planned_days_per_week=runs_per_week,
                    plan_level=plan_level,
                    workouts=workouts,
                    daily_mobility=daily_mobility,
                )
            except Exception as error:
                st.error(f"Không lưu được plan: {error}")
            else:
                st.session_state[save_key] = plan_id
                st.success(f"Đã kích hoạt plan #{plan_id}.")
    else:
        st.info("Hãy kiểm tra lịch trước khi xác nhận.")